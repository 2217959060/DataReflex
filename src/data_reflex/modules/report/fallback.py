# -*- coding: utf-8 -*-
"""降级报告：静态诊断文案，明确「非分析结论」，禁止编造查询/结论。

上游：``Node_FallbackReport``（模型不可用且开关开启）。
本文件：数据概览 + 失败范围 + 手工建议；不含可执行 SQL/脚本。
下游：任务以 ``status=fallback`` 结束。

See ``tasks.md`` T31 / D-FALLBACK-01。
"""

from __future__ import annotations

from typing import Any

from data_reflex.models.schemas import (
    FallbackReportReq,
    FallbackReportResp,
    TableSchema,
)
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

_STATIC_MARKER = "静态诊断"
_NON_ANALYSIS = "非分析结论"

# Must not invent analysis conclusions or emit runnable ops/scripts.
_FORBIDDEN_SNIPPETS = (
    "SELECT ",
    "INSERT ",
    "DROP ",
    "import ",
    "os.system",
    "分析结论：",
    "因此可以判定",
    "结果显示渠道A优于",
)


class FallbackReportBuilder:
    """生成静态诊断 FallbackReportResp；绝不输出新查询或分析判定。"""

    def build(self, req: FallbackReportReq) -> FallbackReportResp:
        data_overview = self._data_overview(req.table_schemas, req.dataset_id)
        parse_status = {
            "parsed_keywords": list(req.parsed_keywords),
            "keyword_count": len(req.parsed_keywords),
            "has_schema": bool(req.table_schemas),
        }
        failure_scope = {
            "failed_node": req.failed_node,
            "failed_sub_tasks": list(req.failed_sub_tasks),
            "failed_sub_task_count": len(req.failed_sub_tasks),
            "completed_sub_task_count": len(req.completed_results),
        }
        suggestions = self._manual_suggestions(req.table_schemas, req.parsed_keywords)
        completed = [
            {
                "sub_task_id": r.sub_task_id,
                "status": r.status,
                "row_count": len(r.result_rows),
                "columns": list(r.result_columns),
                "error_summary": r.error_summary,
            }
            for r in req.completed_results
        ]

        summary = (
            f"【{_NON_ANALYSIS}】【{_STATIC_MARKER}】 "
            f"task={req.task_id} dataset={req.dataset_id} "
            f"failed_node={req.failed_node}; "
            "模型不可用或调用耗尽，本报告仅为诊断信息，不是数据分析结论。"
            f" completed={len(req.completed_results)} "
            f"failed_sub_tasks={len(req.failed_sub_tasks)}."
        )
        self._assert_no_fabrication(summary, suggestions)

        logger.info(
            "fallback report built",
            extra={
                "task_id": req.task_id,
                "dataset_id": req.dataset_id,
                "failed_node": req.failed_node,
            },
        )
        return FallbackReportResp(
            summary=summary,
            data_overview=data_overview,
            parse_status=parse_status,
            failure_scope=failure_scope,
            manual_suggestions=suggestions,
            completed_results=completed,
            is_analysis_conclusion=False,
        )

    def _data_overview(
        self, schemas: list[TableSchema], dataset_id: str
    ) -> dict[str, Any]:
        tables: list[dict[str, Any]] = []
        for schema in schemas:
            cols = [{"name": c.name, "dtype": c.dtype} for c in schema.columns]
            tables.append(
                {
                    "file_name": schema.file_name,
                    "column_count": len(schema.columns),
                    "columns": cols,
                    "row_count": schema.row_count,
                }
            )
        return {
            "dataset_id": dataset_id,
            "table_count": len(tables),
            "tables": tables,
            "note": "overview from declared schema only; no computed analytics",
        }

    def _manual_suggestions(
        self, schemas: list[TableSchema], keywords: list[str]
    ) -> list[str]:
        """Field / template tips only — no executable SQL or Python."""
        tips: list[str] = [
            "可在表格工具中按 schema 字段手工筛选汇总；本系统未生成可执行查询或脚本。",
            "六类问题模板：退货率 TopN / 渠道对比 / 时间趋势 / 用户分层 / 促销对比 / 复购率。",
        ]
        sensitive = {"phone", "email", "mobile", "id_card"}
        for schema in schemas:
            safe_cols = [
                c.name for c in schema.columns if c.name.lower() not in sensitive
            ]
            if safe_cols:
                preview = ", ".join(safe_cols[:8])
                more = "…" if len(safe_cols) > 8 else ""
                tips.append(f"表 {schema.file_name} 可用分析字段示例：{preview}{more}")
        if keywords:
            tips.append("已识别关键词/约束：" + "; ".join(keywords[:12]))
        tips.append("恢复模型或提高调用上限后，可重新提交同一问题获得正式分析报告。")
        return tips

    def _assert_no_fabrication(self, summary: str, suggestions: list[str]) -> None:
        blob = summary + "\n" + "\n".join(suggestions)
        upper = blob.upper()
        for needle in _FORBIDDEN_SNIPPETS:
            if needle.upper() in upper or needle in blob:
                raise ValueError(
                    "fallback report must not contain fabricated "
                    f"ops/conclusions: {needle!r}"
                )
