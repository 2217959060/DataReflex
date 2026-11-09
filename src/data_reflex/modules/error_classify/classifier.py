# -*- coding: utf-8 -*-
"""把沙箱执行结果归成错误类型，并给出「稳定指纹 + 模板修复建议」。

自愈链路的上游：
  Execute 失败 → 本模块 classify()
               → Reflector 读 retryable / suggested_fix / fingerprint
               → 决定熔断或带着 hint 再 GenerateOp

指纹用途：同一类错连续出现两次就熔断，避免无限重试。
suggested_fix：英文模板句，后面 Reflect 可选用 LLM 润成中文短 hint。

See ``tasks.md`` T14 and decision D-ERRCLS-01.
"""

from __future__ import annotations

import re
from typing import Literal

from data_reflex.models.schemas import ErrorClassifyReq, ErrorClassifyResp, ExecuteResp
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

ErrorType = Literal[
    "syntax",  # SQL/Python 语法挂了
    "schema_semantic",  # 表/列名不对、KeyError 等
    "runtime",  # 超时、资源杀、其它运行时
    "result_anomaly",  # 跑通了但结果异常（空表/比例离谱等）
    "none",  # 没有需要自愈的错误
]

# --- 下面三组正则：从 stderr/stdout 里「猜」错误大类（优先级见 _detect_type）---

_SYNTAX_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bsyntax\s*error\b",
        r"\bparse\s*error\b",
        r"\bunexpected\s+token\b",
        r"\binvalid\s+syntax\b",
        r"\bSyntaxError\b",
        r"\bIndentationError\b",
        r"near\s+[\"'].+[\"']",
    )
)

_SCHEMA_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bno such (column|table|database)\b",
        r"\bunknown (column|table|field)\b",
        r"\bambiguous column\b",
        r"\bcolumn .+ does not exist\b",
        r"\btable .+ does not exist\b",
        r"\bundefined column\b",
        r"\bNameError\b",  # missing name often schema/binding in generated py
        r"\bKeyError\b",
        r"\bhas no attribute\b",
    )
)

_ANOMALY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bresult\s*anomaly\b",
        r"\bempty[_ ]?(result|anomaly)\b",
        r"\banomalous\s+empty\b",
        r"\bratio\s*(out of range|invalid)\b",
        r"\bunexpected\s+empty\b",
        r"\brow\s*count\s*(anomaly|exceed|invalid)\b",
    )
)

# 指纹归一化：把路径/uuid/长数字抹掉，保证「同错同指纹、异错可区分」
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/)[^\s:]+")
_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_LONG_NUM_RE = re.compile(r"\b\d{3,}\b")
_WS_RE = re.compile(r"\s+")


def normalize_error_line(text: str) -> str:
    """把关键错误行归一化，供指纹使用（D-ERRCLS-01）。"""
    line = text.strip().splitlines()[0] if text.strip() else ""
    line = line.lower()
    line = _PATH_RE.sub("<path>", line)
    line = _UUID_RE.sub("<id>", line)
    line = _HEX_RE.sub("<hex>", line)
    line = _LONG_NUM_RE.sub("<n>", line)
    line = _WS_RE.sub(" ", line).strip()
    return line


def _combined_text(resp: ExecuteResp) -> str:
    return f"{resp.stderr}\n{resp.stdout}".strip()


def _matches(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def _key_line(resp: ExecuteResp) -> str:
    """挑一行最有代表性的错误文本（优先 stderr）。"""
    for blob in (resp.stderr, resp.stdout):
        for raw in blob.splitlines():
            line = raw.strip()
            if line:
                return line
    if resp.resource_killed:
        return "resource_killed"
    if not resp.success:
        return f"exit_code={resp.exit_code}"
    return ""


class ErrorClassifier:
    """分类沙箱结果，供自愈环使用。"""

    def classify(self, req: ErrorClassifyReq) -> ErrorClassifyResp:
        """输出：类型 + 详情 + 指纹 + 可否重试 + 模板 suggested_fix。"""
        resp = req.execute_resp
        error_type = self._detect_type(resp, req.op_type)
        key = _key_line(resp)
        normalized = normalize_error_line(key) if key else ""
        # 指纹格式：`类型|归一化关键行`；成功无错则 `none|`
        fingerprint = (
            f"{error_type}|"
            if error_type == "none"
            else f"{error_type}|{normalized or 'unknown'}"
        )
        retryable = self._retryable(error_type, resp)
        fix = self._suggested_fix(error_type, req.op_type, key)
        detail = key if error_type != "none" else ""

        logger.info(
            "error classified",
            extra={
                "task_id": "-",
                "dataset_id": "-",
                "error_type": error_type,
                "retryable": retryable,
                "op_type": req.op_type,
            },
        )
        return ErrorClassifyResp(
            error_type=error_type,
            error_detail=detail,
            error_fingerprint=fingerprint,
            retryable=retryable,
            suggested_fix=fix,
        )

    def _detect_type(self, resp: ExecuteResp, op_type: str) -> ErrorType:
        _ = op_type
        if resp.success and not resp.resource_killed and not _combined_text(resp):
            return "none"
        if resp.success and not resp.resource_killed:
            text = _combined_text(resp)
            if text and _matches(_ANOMALY_PATTERNS, text):
                return "result_anomaly"
            # Successful run with empty noise → none
            if not text or resp.exit_code == 0:
                return "none"

        text = _combined_text(resp)
        # 优先级：语法 → schema → 结果异常 → 运行时（含被 OOM/超时杀掉）
        if _matches(_SYNTAX_PATTERNS, text):
            return "syntax"
        if _matches(_SCHEMA_PATTERNS, text):
            return "schema_semantic"
        if _matches(_ANOMALY_PATTERNS, text):
            return "result_anomaly"
        if resp.resource_killed or not resp.success:
            return "runtime"
        return "none"

    def _retryable(self, error_type: ErrorType, resp: ExecuteResp) -> bool:
        """资源硬杀不可靠重试；其余四类错误默认可交给 Reflect 自愈。"""
        if error_type == "none":
            return False
        # Hard resource kills are not fixed by regenerating the same op shape.
        if resp.resource_killed:
            return False
        return error_type in {
            "syntax",
            "schema_semantic",
            "runtime",
            "result_anomaly",
        }

    def _suggested_fix(
        self, error_type: ErrorType, op_type: str, key_line: str
    ) -> str | None:
        """按错误类型返回固定英文模板建议（不调 LLM）。

        Reflector 可能再把它润色成中文短句；没有 Key 时就原样当 fix_hint。
        """
        if error_type == "none":
            return None
        if error_type == "syntax":
            return (
                f"Fix {op_type} syntax near the reported token; "
                "regenerate a parseable statement/script."
            )
        if error_type == "schema_semantic":
            return (
                "Align identifiers with authorized schema columns/tables; "
                "avoid unknown or ambiguous fields."
            )
        if error_type == "result_anomaly":
            return (
                "Adjust query filters/aggregations to satisfy structured "
                "result constraints (empty/ratio/row bounds)."
            )
        # runtime
        if "timeout" in key_line.lower():
            return "Simplify the operation to finish within the sandbox timeout."
        return "Handle the runtime failure; avoid unbounded loops and invalid ops."
