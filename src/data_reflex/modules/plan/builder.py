# -*- coding: utf-8 -*-
"""规划：按六类 question_tag 用确定性模板拆 SubTask（不调 LLM）。

上游：``Node_Plan``（意图已 in_scope）。
本文件：产出有序子任务与依赖；``single_step`` 时压成单步探测。
下游：``PlanValidate`` → 人审或 GenerateOp。

V2-3：中间步可 ``report=False``；冗余对比步 ``reuse_result`` 让 Execute 复用上步行。

See ``tasks.md`` T21 / D-PLAN-01。
"""

from __future__ import annotations

import re
from typing import Any

from data_reflex.models.schemas import PlanReq, PlanResp, SubTask
from data_reflex.modules.context.manager import QUESTION_TAGS
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

_TAG_IN_NORMALIZED_RE = re.compile(r"\btag=([a-z0-9_]+)\b", re.IGNORECASE)


def _st(
    task_id: str,
    description: str,
    *,
    depends_on: list[str] | None = None,
    expected_input: dict[str, Any] | None = None,
    expected_output: dict[str, Any] | None = None,
    parallel_group: int = 0,
) -> SubTask:
    return SubTask(
        task_id=task_id,
        description=description,
        depends_on=list(depends_on or []),
        expected_input=dict(expected_input or {}),
        expected_output=dict(expected_output or {}),
        parallel_group=parallel_group,
    )


class PlanBuilder:
    """按标签套模板生成 PlanResp；无 LLM，结果可复现。"""

    def build(
        self,
        req: PlanReq,
        *,
        question_tag: str | None = None,
        single_step: bool = False,
    ) -> PlanResp:
        """Return a plan. ``question_tag`` is outside frozen ``PlanReq``.

        When ``single_step`` is True (``enable_planner=False``), collapse the
        multi-step template into one probe SubTask — same eval path, fewer steps.
        """
        tag = self._resolve_tag(req, question_tag)
        if tag is None:
            return PlanResp(
                success=False,
                sub_tasks=[],
                error_msg=(
                    "cannot plan: missing or unsupported question_tag; "
                    "expected one of the six fixed classes"
                ),
            )

        if not (req.normalized_query or "").strip():
            return PlanResp(
                success=False,
                sub_tasks=[],
                error_msg="cannot plan: normalized_query is empty",
            )

        try:
            sub_tasks = self._template_for(tag, req)
            if single_step:
                sub_tasks = self._collapse_to_single_step(sub_tasks, tag, req)
        except Exception as exc:  # noqa: BLE001 — convert to plan failure
            logger.warning(
                "plan template failed",
                extra={
                    "task_id": "-",
                    "dataset_id": req.dataset_id,
                    "error": type(exc).__name__,
                },
            )
            return PlanResp(
                success=False,
                sub_tasks=[],
                error_msg=f"plan build failed: {type(exc).__name__}: {exc}",
            )

        if len(sub_tasks) < 1:
            return PlanResp(
                success=False,
                sub_tasks=[],
                error_msg="plan produced no sub-tasks",
            )

        logger.info(
            "plan built",
            extra={
                "task_id": "-",
                "dataset_id": req.dataset_id,
                "question_tag": tag,
                "sub_task_count": len(sub_tasks),
                "single_step": single_step,
            },
        )
        return PlanResp(
            success=True,
            sub_tasks=sub_tasks,
            has_cycle=False,
            missing_inputs=[],
            error_msg=None,
        )

    def _collapse_to_single_step(
        self, steps: list[SubTask], tag: str, req: PlanReq
    ) -> list[SubTask]:
        """Merge multi-step template into one exploratory probe SubTask."""
        tables: list[str] = []
        group_by: list[str] = []
        metric: Any = None
        for st in steps:
            ei = st.expected_input
            raw_tables = ei.get("tables")
            if isinstance(raw_tables, list):
                for name in raw_tables:
                    if isinstance(name, str) and name not in tables:
                        tables.append(name)
            raw_gb = ei.get("group_by")
            if isinstance(raw_gb, list) and raw_gb:
                group_by = [str(x) for x in raw_gb]
            if ei.get("metric") is not None:
                metric = ei.get("metric")

        expected_input: dict[str, Any] = {
            "dataset_id": req.dataset_id,
            "constraints": list(req.constraints),
            "question_tag": tag,
            "tables": tables or ["orders"],
            "single_step": True,
        }
        if group_by:
            expected_input["group_by"] = group_by
        if metric is not None:
            expected_input["metric"] = metric

        expected_output = dict(steps[-1].expected_output) if steps else {}
        return [
            _st(
                "st-probe",
                f"Single-step probe execution for {tag}",
                expected_input=expected_input,
                expected_output=expected_output,
                parallel_group=0,
            )
        ]

    def _resolve_tag(self, req: PlanReq, question_tag: str | None) -> str | None:
        if question_tag and question_tag in QUESTION_TAGS:
            return question_tag
        match = _TAG_IN_NORMALIZED_RE.search(req.normalized_query or "")
        if match:
            candidate = match.group(1).strip()
            if candidate in QUESTION_TAGS:
                return candidate
        return None

    def _template_for(self, tag: str, req: PlanReq) -> list[SubTask]:
        constraints = list(req.constraints)
        base_in = {
            "dataset_id": req.dataset_id,
            "constraints": constraints,
            "question_tag": tag,
        }
        builders = {
            "return_rate_topn": self._plan_return_rate_topn,
            "channel_compare": self._plan_channel_compare,
            "time_trend": self._plan_time_trend,
            "segment_compare": self._plan_segment_compare,
            "promo_compare": self._plan_promo_compare,
            "repurchase_rate": self._plan_repurchase_rate,
        }
        return builders[tag](base_in)

    def _plan_return_rate_topn(self, base_in: dict[str, Any]) -> list[SubTask]:
        # Join then aggregate — always ≥2 steps for multi-table TopN.
        return [
            _st(
                "st-load-join",
                "Load orders and returns; join on order_id/product_id",
                expected_input={**base_in, "tables": ["orders", "returns"]},
                expected_output={
                    "columns": ["order_id", "product_id", "product_name", "returned"],
                    "row_grain": "order_product",
                    "report": False,
                },
                parallel_group=0,
            ),
            _st(
                "st-rate-topn",
                "Compute per-product return rate and select TopN",
                depends_on=["st-load-join"],
                expected_input={
                    **base_in,
                    "from_sub_task": "st-load-join",
                    "metric": "return_rate",
                },
                expected_output={
                    "columns": [
                        "product_id",
                        "product_name",
                        "return_rate",
                        "order_count",
                    ],
                    "chart": "bar",
                    "report": True,
                },
                parallel_group=1,
            ),
        ]

    def _plan_channel_compare(self, base_in: dict[str, Any]) -> list[SubTask]:
        return [
            _st(
                "st-agg-channel",
                "Aggregate orders metrics by channel",
                expected_input={
                    **base_in,
                    "tables": ["orders"],
                    "group_by": ["channel"],
                },
                expected_output={
                    "columns": ["channel", "amount_sum", "order_count"],
                    "report": False,
                },
                parallel_group=0,
            ),
            _st(
                "st-compare-channel",
                "Publish channel comparison table and bar chart",
                depends_on=["st-agg-channel"],
                expected_input={
                    **base_in,
                    "from_sub_task": "st-agg-channel",
                    "reuse_result": True,
                },
                expected_output={
                    "columns": ["channel", "amount_sum", "order_count"],
                    "chart": "bar",
                    "report": True,
                },
                parallel_group=1,
            ),
        ]

    def _plan_time_trend(self, base_in: dict[str, Any]) -> list[SubTask]:
        return [
            _st(
                "st-agg-time",
                "Aggregate orders by time grain (day/week/month)",
                expected_input={
                    **base_in,
                    "tables": ["orders"],
                    "group_by": ["order_date"],
                },
                expected_output={
                    "columns": ["period", "amount_sum", "order_count"],
                    "report": False,
                },
                parallel_group=0,
            ),
            _st(
                "st-series-trend",
                "Publish time series for line chart",
                depends_on=["st-agg-time"],
                expected_input={
                    **base_in,
                    "from_sub_task": "st-agg-time",
                    "reuse_result": True,
                },
                expected_output={
                    "columns": ["period", "amount_sum", "order_count"],
                    "chart": "line",
                    "report": True,
                },
                parallel_group=1,
            ),
        ]

    def _plan_segment_compare(self, base_in: dict[str, Any]) -> list[SubTask]:
        return [
            _st(
                "st-join-users",
                "Join orders with users on user_id for segment labels",
                expected_input={**base_in, "tables": ["orders", "users"]},
                expected_output={
                    "columns": ["user_id", "user_segment", "amount", "order_id"],
                    "report": False,
                },
                parallel_group=0,
            ),
            _st(
                "st-agg-segment",
                "Compare metrics across user_segment",
                depends_on=["st-join-users"],
                expected_input={
                    **base_in,
                    "from_sub_task": "st-join-users",
                    "group_by": ["user_segment"],
                },
                expected_output={
                    "columns": ["user_segment", "amount_sum", "order_count"],
                    "chart": "bar",
                    "report": True,
                },
                parallel_group=1,
            ),
        ]

    def _plan_promo_compare(self, base_in: dict[str, Any]) -> list[SubTask]:
        return [
            _st(
                "st-agg-promo",
                "Aggregate orders by is_promo flag",
                expected_input={
                    **base_in,
                    "tables": ["orders"],
                    "group_by": ["is_promo"],
                },
                expected_output={
                    "columns": ["is_promo", "amount_sum", "order_count"],
                    "report": False,
                },
                parallel_group=0,
            ),
            _st(
                "st-compare-promo",
                "Publish promo vs non-promo comparison",
                depends_on=["st-agg-promo"],
                expected_input={
                    **base_in,
                    "from_sub_task": "st-agg-promo",
                    "reuse_result": True,
                },
                expected_output={
                    "columns": ["is_promo", "amount_sum", "order_count"],
                    "chart": "bar",
                    "report": True,
                },
                parallel_group=1,
            ),
        ]

    def _plan_repurchase_rate(self, base_in: dict[str, Any]) -> list[SubTask]:
        return [
            _st(
                "st-user-orders",
                "Count orders per user within analysis window",
                expected_input={
                    **base_in,
                    "tables": ["orders"],
                    "group_by": ["user_id"],
                },
                expected_output={
                    "columns": ["user_id", "order_count"],
                    "report": False,
                },
                parallel_group=0,
            ),
            _st(
                "st-repurchase",
                "Compute repurchase rate (users with orders>=2 / users)",
                depends_on=["st-user-orders"],
                expected_input={**base_in, "from_sub_task": "st-user-orders"},
                expected_output={
                    "columns": ["repurchase_rate", "repeat_users", "total_users"],
                    "chart": "table",
                    "report": True,
                },
                parallel_group=1,
            ),
        ]
