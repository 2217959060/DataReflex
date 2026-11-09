# -*- coding: utf-8 -*-
"""操作生成：确定性模板为主；``enable_llm_ops`` 时先试 GLM，失败回退模板。

上游：``Node_GenerateOp``（可带 Reflect/结果校验的 ``fix_hint`` / previous_error）。
本文件：产出 ``op_type`` + ``op_content``，做格式门；约束写入 SQL（V2-2）。
下游：必须再过 Safety + 沙箱，宿主机从不执行生成代码。

有 Key 时单测若断言模板行为，需关 ``ENABLE_LLM_OPS``（否则 LLM 可能改 op_type）。

See ``tasks.md`` T24 / D-GENOP-01；roadmap V2-1 / V2-2。
"""

from __future__ import annotations

import ast
import re
from typing import Literal

from data_reflex.config.settings import Settings, get_settings
from data_reflex.models.schemas import GenerateOpReq, GenerateOpResp, SubTask
from data_reflex.modules.llm.client import LlmClient, LlmError, build_llm_client
from data_reflex.modules.llm.prompts import generate_op_messages, parse_generate_op_json
from data_reflex.utils.logger import get_logger, summarize_op

logger = get_logger(__name__)

OpType = Literal["sql", "python"]

_TOP_N_RE = re.compile(r"top_n=(\d+)")
_TIME_GRAIN_RE = re.compile(r"time_grain=(day|week|month)")
_CHANNEL_IN_RE = re.compile(r"channel\s+IN\s*\(([^)]*)\)", re.IGNORECASE)
_SEGMENT_IN_RE = re.compile(r"user_segment\s+IN\s*\(([^)]*)\)", re.IGNORECASE)
_IS_PROMO_RE = re.compile(r"is_promo=([01])\b")
_EXCLUDE_RE = re.compile(r"exclude=([A-Za-z0-9_\u4e00-\u9fff]+)")


class OpGenerator:
    """为单个 SubTask 生成 SQL/Python；LLM 失败或格式门不通过则模板兜底。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        llm: LlmClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._llm = llm
        self.last_llm_calls: int = 0
        self.last_source: Literal["template", "llm"] = "template"

    def generate(self, req: GenerateOpReq) -> GenerateOpResp:
        """Generate once. Callers retry on ``format_valid=False``."""
        self.last_llm_calls = 0
        self.last_source = "template"
        try:
            op_type, content = self._build_content(req)
        except Exception as exc:  # noqa: BLE001
            return GenerateOpResp(
                op_type="sql",
                op_content="",
                expected_input=dict(req.sub_task.expected_input),
                expected_output=dict(req.sub_task.expected_output),
                format_valid=False,
                error_msg=f"generate failed: {type(exc).__name__}: {exc}",
            )

        valid, err = validate_op_format(op_type, content)
        meta = summarize_op(op_type, content)
        logger.info(
            "op generated",
            extra={
                "task_id": "-",
                "dataset_id": "-",
                "sub_task_id": req.sub_task.task_id,
                **meta,
                "format_valid": valid,
                "source": self.last_source,
            },
        )
        return GenerateOpResp(
            op_type=op_type,
            op_content=content if valid else content,
            expected_input=dict(req.sub_task.expected_input),
            expected_output=dict(req.sub_task.expected_output),
            format_valid=valid,
            error_msg=err,
        )

    def _build_content(self, req: GenerateOpReq) -> tuple[OpType, str]:
        # V2-3 reuse steps must stay deterministic stubs (Execute copies prior rows).
        if req.sub_task.expected_input.get("reuse_result"):
            return self._build_template(req)
        if self._llm_enabled():
            llm_result = self._try_llm(req)
            if llm_result is not None:
                self.last_source = "llm"
                return llm_result
        return self._build_template(req)

    def _llm_enabled(self) -> bool:
        return bool(
            self._settings.enable_llm_ops and self._settings.llm_api_key.strip()
        )

    def _client(self) -> LlmClient:
        if self._llm is not None:
            return self._llm
        return build_llm_client(self._settings)

    def _try_llm(self, req: GenerateOpReq) -> tuple[OpType, str] | None:
        try:
            text = self._client().chat(
                messages=generate_op_messages(req),
                max_tokens=700,
            )
            self.last_llm_calls = 1
            op_type, content = parse_generate_op_json(text)
            valid, err = validate_op_format(op_type, content)
            if not valid:
                logger.info(
                    "llm op rejected by format gate; falling back to template",
                    extra={
                        "task_id": "-",
                        "dataset_id": "-",
                        "sub_task_id": req.sub_task.task_id,
                        "error": err or "",
                    },
                )
                return None
            typed: OpType = "sql" if op_type == "sql" else "python"
            # V2-2: if LLM drops explicit filters / LIMIT, fall back to template.
            if typed == "sql" and not _sql_respects_constraints(
                content, list(req.constraints or [])
            ):
                logger.info(
                    "llm op missing required constraints; falling back to template",
                    extra={
                        "task_id": "-",
                        "dataset_id": "-",
                        "sub_task_id": req.sub_task.task_id,
                    },
                )
                return None
            return typed, content
        except (LlmError, ValueError, TypeError) as exc:
            logger.info(
                "llm op unavailable; falling back to template",
                extra={
                    "task_id": "-",
                    "dataset_id": "-",
                    "sub_task_id": req.sub_task.task_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return None

    def _build_template(self, req: GenerateOpReq) -> tuple[OpType, str]:
        st = req.sub_task
        tag = str(st.expected_input.get("question_tag") or "")
        tables = st.expected_input.get("tables") or []
        group_by = st.expected_input.get("group_by") or []
        metric = st.expected_input.get("metric")
        constraints = list(req.constraints) or list(
            st.expected_input.get("constraints") or []
        )
        _ = req.previous_error

        # V2-3: compare/publish steps reuse prior rows in Execute; emit a safe stub.
        if st.expected_input.get("reuse_result"):
            return "sql", "SELECT 1 AS _reuse_placeholder"

        if (
            metric == "return_rate"
            or "repurchase" in st.task_id
            or tag == "repurchase_rate"
        ):
            if "repurchase" in st.task_id or (
                tag == "repurchase_rate" and "st-repurchase" in st.task_id
            ):
                return "python", _python_repurchase()
            if metric == "return_rate":
                top_n = _top_n(constraints, default=10)
                return "sql", _sql_return_rate_topn(top_n, constraints)

        if tables == ["orders", "returns"] or "join" in st.task_id.lower():
            if "users" in tables:
                return "sql", _sql_join_orders_users(constraints)
            return "sql", _sql_join_orders_returns(constraints)

        if tables == ["orders", "users"]:
            return "sql", _sql_join_orders_users(constraints)

        if group_by == ["channel"] or "channel" in st.task_id:
            return "sql", _sql_agg_channel(constraints)

        if group_by == ["order_date"] or "time" in st.task_id or "trend" in st.task_id:
            grain = _time_grain(constraints)
            return "sql", _sql_agg_time(grain, constraints)

        if group_by == ["user_segment"] or "segment" in st.task_id:
            return "sql", _sql_agg_segment(constraints)

        if group_by == ["is_promo"] or "promo" in st.task_id:
            return "sql", _sql_agg_promo(constraints)

        if (
            group_by == ["user_id"]
            or "user-orders" in st.task_id
            or "user_orders" in st.task_id
        ):
            return "sql", _sql_user_order_counts(constraints)

        if st.depends_on:
            return "sql", _sql_passthrough_compare(st, constraints)

        return (
            "sql",
            _sql_agg_channel(constraints),
        )


def validate_op_format(op_type: str, op_content: str) -> tuple[bool, str | None]:
    """Return ``(ok, error_msg)`` for SQL/Python surface format."""
    text = (op_content or "").strip()
    if not text:
        return False, "op_content is empty"

    if op_type == "sql":
        return _validate_sql(text)
    if op_type == "python":
        return _validate_python(text)
    return False, f"unsupported op_type: {op_type}"


def _validate_sql(text: str) -> tuple[bool, str | None]:
    try:
        import sqlglot
        from sqlglot import exp

        statements = sqlglot.parse(text, read="sqlite")
        if not statements or statements[0] is None:
            return False, "sql parse returned empty"
        root = statements[0]
        if not isinstance(root, exp.Select) and not root.find(exp.Select):
            return False, "sql must be a SELECT query"
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"sql format invalid: {type(exc).__name__}: {exc}"


def _validate_python(text: str) -> tuple[bool, str | None]:
    try:
        ast.parse(text)
        return True, None
    except SyntaxError as exc:
        return False, f"python format invalid: {exc.msg}"


def _sql_respects_constraints(sql: str, constraints: list[str]) -> bool:
    """True when key explicit constraints appear to be reflected in SQL."""
    text = sql or ""
    lower = text.lower()
    for item in constraints:
        if _CHANNEL_IN_RE.search(item) and " in (" not in lower:
            return False
        if _SEGMENT_IN_RE.search(item) and " in (" not in lower:
            return False
        top = _TOP_N_RE.search(item)
        if top and f"limit {top.group(1)}" not in lower:
            return False
        promo = _IS_PROMO_RE.search(item)
        if promo and "is_promo" not in lower:
            return False
        ex = _EXCLUDE_RE.search(item)
        if ex:
            val = ex.group(1).lower()
            # Accept <> / != / NOT IN … exclude token.
            if val not in lower:
                return False
            if (
                "<>" not in text
                and "!=" not in text
                and "not in" not in lower
                and "not (" not in lower
            ):
                return False
    return True


def _top_n(constraints: list[str], *, default: int) -> int:
    for item in constraints:
        match = _TOP_N_RE.search(item)
        if match:
            return max(1, int(match.group(1)))
    return default


def _time_grain(constraints: list[str]) -> str:
    for item in constraints:
        match = _TIME_GRAIN_RE.search(item)
        if match:
            return match.group(1)
    return "day"


def _quote_ident_list(raw: str) -> str:
    """Normalize IN-list literals; keep only simple quoted/unquoted tokens."""
    parts: list[str] = []
    for token in raw.split(","):
        t = token.strip()
        if not t:
            continue
        if (t.startswith("'") and t.endswith("'")) or (
            t.startswith('"') and t.endswith('"')
        ):
            inner = t[1:-1].replace("'", "''")
            parts.append(f"'{inner}'")
        elif re.match(r"^[A-Za-z0-9_\u4e00-\u9fff]+$", t):
            parts.append(f"'{t}'")
    return ", ".join(parts)


def _orders_filters(
    constraints: list[str],
    *,
    channel_col: str = "channel",
    promo_col: str = "is_promo",
) -> list[str]:
    """Build WHERE fragments for the orders grain (channel / promo / exclude)."""
    clauses: list[str] = []
    for item in constraints:
        m = _CHANNEL_IN_RE.search(item)
        if m:
            listed = _quote_ident_list(m.group(1))
            if listed:
                clauses.append(f"{channel_col} IN ({listed})")
            continue
        m = _IS_PROMO_RE.search(item)
        if m:
            clauses.append(f"{promo_col} = {m.group(1)}")
            continue
        m = _EXCLUDE_RE.search(item)
        if m:
            val = m.group(1).replace("'", "''")
            clauses.append(f"{channel_col} <> '{val}'")
    return clauses


def _segment_filters(
    constraints: list[str], *, segment_col: str = "u.user_segment"
) -> list[str]:
    clauses: list[str] = []
    for item in constraints:
        m = _SEGMENT_IN_RE.search(item)
        if m:
            listed = _quote_ident_list(m.group(1))
            if listed:
                clauses.append(f"{segment_col} IN ({listed})")
    return clauses


def _sql_where(clauses: list[str]) -> str:
    return f" WHERE {' AND '.join(clauses)}" if clauses else ""


def _sql_join_orders_returns(constraints: list[str] | None = None) -> str:
    where = _sql_where(_orders_filters(constraints or [], channel_col="o.channel"))
    return (
        "SELECT o.order_id, o.product_id, o.product_name, "
        "CASE WHEN r.return_id IS NULL THEN 0 ELSE 1 END AS returned "
        "FROM orders o "
        "LEFT JOIN returns r ON o.order_id = r.order_id "
        "AND o.product_id = r.product_id"
        f"{where}"
    )


def _sql_join_orders_users(constraints: list[str] | None = None) -> str:
    cons = constraints or []
    clauses = _orders_filters(cons, channel_col="o.channel") + _segment_filters(cons)
    where = _sql_where(clauses)
    return (
        "SELECT o.order_id, o.user_id, o.amount, u.user_segment "
        "FROM orders o "
        "INNER JOIN users u ON o.user_id = u.user_id"
        f"{where}"
    )


def _sql_return_rate_topn(
    top_n: int, constraints: list[str] | None = None
) -> str:
    where = _sql_where(_orders_filters(constraints or [], channel_col="o.channel"))
    return (
        "SELECT o.product_id, o.product_name, "
        "CAST(COUNT(DISTINCT r.return_id) AS FLOAT) / "
        "NULLIF(COUNT(DISTINCT o.order_id), 0) AS return_rate, "
        "COUNT(DISTINCT o.order_id) AS order_count "
        "FROM orders o "
        "LEFT JOIN returns r ON o.order_id = r.order_id "
        "AND o.product_id = r.product_id"
        f"{where} "
        "GROUP BY o.product_id, o.product_name "
        f"ORDER BY return_rate DESC LIMIT {top_n}"
    )


def _sql_agg_channel(constraints: list[str] | None = None) -> str:
    where = _sql_where(_orders_filters(constraints or []))
    return (
        "SELECT channel, SUM(amount) AS amount_sum, COUNT(*) AS order_count "
        f"FROM orders{where} GROUP BY channel ORDER BY amount_sum DESC"
    )


def _sql_agg_time(grain: str, constraints: list[str] | None = None) -> str:
    if grain == "week":
        period = "strftime('%Y-%W', order_date)"
    elif grain == "month":
        period = "strftime('%Y-%m', order_date)"
    else:
        period = "order_date"
    where = _sql_where(_orders_filters(constraints or []))
    return (
        f"SELECT {period} AS period, SUM(amount) AS amount_sum, "
        f"COUNT(*) AS order_count FROM orders{where} "
        "GROUP BY period ORDER BY period"
    )


def _sql_agg_segment(constraints: list[str] | None = None) -> str:
    cons = constraints or []
    clauses = _orders_filters(cons, channel_col="o.channel") + _segment_filters(cons)
    where = _sql_where(clauses)
    return (
        "SELECT u.user_segment, SUM(o.amount) AS amount_sum, COUNT(*) AS order_count "
        "FROM orders o INNER JOIN users u ON o.user_id = u.user_id"
        f"{where} "
        "GROUP BY u.user_segment ORDER BY amount_sum DESC"
    )


def _sql_agg_promo(constraints: list[str] | None = None) -> str:
    cons = list(constraints or [])
    # Promo vs non-promo compare keeps both groups; drop single is_promo=.
    if any("compare_by=is_promo" in c for c in cons):
        cons = [c for c in cons if not _IS_PROMO_RE.search(c)]
    where = _sql_where(_orders_filters(cons))
    return (
        "SELECT is_promo, SUM(amount) AS amount_sum, COUNT(*) AS order_count "
        f"FROM orders{where} GROUP BY is_promo ORDER BY is_promo"
    )


def _sql_user_order_counts(constraints: list[str] | None = None) -> str:
    where = _sql_where(_orders_filters(constraints or []))
    return (
        f"SELECT user_id, COUNT(*) AS order_count FROM orders{where} "
        "GROUP BY user_id"
    )


def _sql_passthrough_compare(
    st: SubTask, constraints: list[str] | None = None
) -> str:
    """Second-step compare: re-run the matching aggregation with constraints.

    Avoids ``SELECT amount_sum FROM orders`` (invalid / unfiltered dump).
    """
    cons = constraints or []
    tid = st.task_id.lower()
    if "channel" in tid:
        return _sql_agg_channel(cons)
    if "segment" in tid:
        return _sql_agg_segment(cons)
    if "promo" in tid:
        return _sql_agg_promo(cons)
    if "trend" in tid or "series" in tid or "time" in tid:
        return _sql_agg_time(_time_grain(cons), cons)
    if "rate" in tid or "topn" in tid:
        return _sql_return_rate_topn(_top_n(cons, default=10), cons)
    cols = st.expected_output.get("columns") or ["channel", "amount_sum"]
    col_sql = ", ".join(
        str(c) for c in cols if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(c))
    )
    if not col_sql:
        col_sql = "channel, amount_sum"
    where = _sql_where(_orders_filters(cons))
    return f"SELECT {col_sql} FROM orders{where} LIMIT 1000"


def _python_repurchase() -> str:
    return (
        "import pandas as pd\n"
        "counts = orders.groupby('user_id').size()\n"
        "total_users = int(counts.shape[0])\n"
        "repeat_users = int((counts >= 2).sum())\n"
        "repurchase_rate = (repeat_users / total_users) if total_users else None\n"
        "result = pd.DataFrame([{\n"
        "    'repurchase_rate': repurchase_rate,\n"
        "    'repeat_users': repeat_users,\n"
        "    'total_users': total_users,\n"
        "}])\n"
    )
