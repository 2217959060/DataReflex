# -*- coding: utf-8 -*-
"""报告中文结论：先用结果表套模板草稿，可选 GLM 润色（不得改数字/瞎编事实）。

上游：``ReportBuilder`` / ``Node_Report``。
本文件：``draft_conclusion`` 规则生成；``build_conclusion`` 可调 LLM 抛光。
下游：并入 report.summary 的人话部分。

无 Key 或失败时仍返回模板草稿，保证演示可出中文结论。

See roadmap V2-4 / D-V2-CONCLUSION-01。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from data_reflex.config.settings import Settings, get_settings
from data_reflex.models.schemas import SubTaskResult
from data_reflex.modules.llm.client import LlmClient, LlmError, build_llm_client
from data_reflex.modules.llm.prompts import (
    clip_hint,
    conclusion_polish_messages,
)
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def build_conclusion(
    *,
    user_query: str,
    question_tag: str | None,
    reportable: Sequence[SubTaskResult],
    settings: Settings | None = None,
    llm: LlmClient | None = None,
) -> str | None:
    """返回中文结论；无法起草时返回 None。"""
    draft = draft_conclusion(
        user_query=user_query,
        question_tag=question_tag,
        reportable=reportable,
    )
    if not draft:
        return None

    cfg = settings or get_settings()
    if not (cfg.enable_llm_ops and cfg.llm_api_key.strip()):
        return draft

    try:
        client = llm or build_llm_client(cfg)
        polished = client.chat(
            messages=conclusion_polish_messages(
                user_query=user_query,
                question_tag=question_tag or "",
                draft=draft,
                table_preview=_table_preview(reportable),
            ),
            max_tokens=280,
        )
        text = clip_hint(polished.replace("\n", " ").strip(), limit=280)
        if text and polish_preserves_numbers(draft, text):
            return text
        logger.info(
            "conclusion polish rejected; using template draft",
            extra={"task_id": "-", "dataset_id": "-"},
        )
    except (LlmError, ValueError, TypeError) as exc:
        logger.info(
            "conclusion polish unavailable; using template draft",
            extra={
                "task_id": "-",
                "dataset_id": "-",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
    return draft


def draft_conclusion(
    *,
    user_query: str,
    question_tag: str | None,
    reportable: Sequence[SubTaskResult],
) -> str | None:
    """Deterministic Chinese draft from the primary result table."""
    primary = _primary_result(reportable)
    if primary is None or not primary.result_rows:
        return None

    tag = (question_tag or "").strip() or _infer_tag(primary.sub_task_id)
    rows = list(primary.result_rows)
    cols = list(primary.result_columns) or list(rows[0].keys())

    builders = {
        "channel_compare": _draft_channel,
        "return_rate_topn": _draft_return_rate,
        "time_trend": _draft_time_trend,
        "segment_compare": _draft_segment,
        "promo_compare": _draft_promo,
        "repurchase_rate": _draft_repurchase,
    }
    fn = builders.get(tag)
    if fn is None:
        return _draft_generic(user_query, rows, cols)
    text = fn(rows, cols)
    return text or _draft_generic(user_query, rows, cols)


def polish_preserves_numbers(draft: str, polished: str) -> bool:
    """True when every significant number in ``draft`` still appears in ``polished``."""
    draft_nums = _significant_numbers(draft)
    if not draft_nums:
        return bool(polished.strip())
    hay = polished.replace(",", "").replace("，", "")
    for num in draft_nums:
        if num not in hay:
            # Allow percentage rewrite: 0.8333 → 83.3
            try:
                val = float(num)
            except ValueError:
                return False
            if 0 < val <= 1:
                pct = f"{val * 100:.1f}".rstrip("0").rstrip(".")
                pct0 = f"{val * 100:.0f}"
                if pct in hay or pct0 in hay or f"{val * 100:.2f}" in hay:
                    continue
            return False
    return True


def _significant_numbers(text: str) -> list[str]:
    out: list[str] = []
    for m in _NUM_RE.finditer(text):
        token = m.group(0)
        # Skip lone tiny ints that are too weak as anchors (e.g. "1 张表").
        if "." not in token and len(token) == 1:
            continue
        out.append(token)
    return out


def _primary_result(reportable: Sequence[SubTaskResult]) -> SubTaskResult | None:
    if not reportable:
        return None
    # Prefer last success (leaf / final table).
    return reportable[-1]


def _infer_tag(sub_task_id: str) -> str:
    sid = sub_task_id.lower()
    if "repurchase" in sid:
        return "repurchase_rate"
    if "rate" in sid or "topn" in sid:
        return "return_rate_topn"
    if "channel" in sid:
        return "channel_compare"
    if "segment" in sid:
        return "segment_compare"
    if "promo" in sid:
        return "promo_compare"
    if "trend" in sid or "time" in sid or "series" in sid:
        return "time_trend"
    return ""


def _fmt_num(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    # rates
    if 0 <= f <= 1:
        return f"{f:.4f}".rstrip("0").rstrip(".")
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _fmt_pct(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{f * 100:.1f}%".replace(".0%", "%")


def _row_get(row: Mapping[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in row:
            return row[k]
    lower = {str(k).lower(): v for k, v in row.items()}
    for k in keys:
        if k.lower() in lower:
            return lower[k.lower()]
    return None


def _sort_rows(
    rows: list[dict[str, Any]], key: str, *, reverse: bool = True
) -> list[dict[str, Any]]:
    def score(r: Mapping[str, Any]) -> float:
        v = _row_get(r, key)
        try:
            return float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return float("-inf") if reverse else float("inf")

    return sorted(rows, key=score, reverse=reverse)


def _draft_channel(rows: list[dict[str, Any]], cols: list[str]) -> str | None:
    metric = "amount_sum" if "amount_sum" in cols else (
        "amount" if "amount" in cols else None
    )
    if metric is None:
        return None
    ordered = _sort_rows(rows, metric, reverse=True)
    if not ordered:
        return None
    top = ordered[0]
    name = _row_get(top, "channel")
    val = _fmt_num(_row_get(top, metric))
    if len(ordered) == 1:
        return f"渠道「{name}」的销售额为 {val}。"
    second = ordered[1]
    name2 = _row_get(second, "channel")
    val2 = _fmt_num(_row_get(second, metric))
    try:
        gap = float(_row_get(top, metric)) - float(_row_get(second, metric))  # type: ignore[arg-type]
        gap_s = _fmt_num(gap)
    except (TypeError, ValueError):
        gap_s = None
    if gap_s is not None:
        return (
            f"「{name}」销售额 {_fmt_num(_row_get(top, metric))}，"
            f"高于「{name2}」的 {val2}，差额约 {gap_s}。"
        )
    return f"「{name}」销售额 {val}，「{name2}」为 {val2}。"


def _draft_return_rate(rows: list[dict[str, Any]], cols: list[str]) -> str | None:
    ordered = _sort_rows(rows, "return_rate", reverse=True)
    if not ordered:
        return None
    top = ordered[0]
    name = _row_get(top, "product_name", "product_id")
    rate = _row_get(top, "return_rate")
    n = len(ordered)
    return (
        f"退货率最高的是「{name}」（{_fmt_pct(rate)}）"
        f"{f'，以下为 Top{n} 结果' if n > 1 else ''}。"
    )


def _draft_time_trend(rows: list[dict[str, Any]], cols: list[str]) -> str | None:
    if not rows:
        return None
    metric = "amount_sum" if "amount_sum" in cols else "amount"
    first, last = rows[0], rows[-1]
    p0 = _row_get(first, "period", "order_date")
    p1 = _row_get(last, "period", "order_date")
    v0 = _fmt_num(_row_get(first, metric))
    v1 = _fmt_num(_row_get(last, metric))
    return f"时间趋势：从 {p0} 的销售额 {v0} 到 {p1} 的 {v1}（共 {len(rows)} 个时间点）。"


def _draft_segment(rows: list[dict[str, Any]], cols: list[str]) -> str | None:
    metric = "amount_sum" if "amount_sum" in cols else "amount"
    ordered = _sort_rows(rows, metric, reverse=True)
    if not ordered:
        return None
    top = ordered[0]
    return (
        f"用户分层中，「{_row_get(top, 'user_segment')}」销售额最高"
        f"（{_fmt_num(_row_get(top, metric))}）。"
    )


def _draft_promo(rows: list[dict[str, Any]], cols: list[str]) -> str | None:
    metric = "amount_sum" if "amount_sum" in cols else "amount"
    promo = next((r for r in rows if str(_row_get(r, "is_promo")) in {"1", "True", "true"}), None)
    non = next((r for r in rows if str(_row_get(r, "is_promo")) in {"0", "False", "false"}), None)
    if promo is None or non is None:
        ordered = _sort_rows(rows, metric, reverse=True)
        if not ordered:
            return None
        top = ordered[0]
        return (
            f"按促销标记汇总后，is_promo={_row_get(top, 'is_promo')} "
            f"一侧销售额为 {_fmt_num(_row_get(top, metric))}。"
        )
    return (
        f"促销订单销售额 {_fmt_num(_row_get(promo, metric))}，"
        f"非促销为 {_fmt_num(_row_get(non, metric))}。"
    )


def _draft_repurchase(rows: list[dict[str, Any]], cols: list[str]) -> str | None:
    row = rows[0]
    rate = _row_get(row, "repurchase_rate")
    repeat = _row_get(row, "repeat_users")
    total = _row_get(row, "total_users")
    return (
        f"全量用户复购率约 {_fmt_pct(rate)}"
        f"（复购用户 {_fmt_num(repeat)} / 下单用户 {_fmt_num(total)}）。"
    )


def _draft_generic(
    user_query: str, rows: list[dict[str, Any]], cols: list[str]
) -> str:
    q = (user_query or "").strip() or "该问题"
    return f"「{q}」分析完成，结果表共 {len(rows)} 行、{len(cols)} 列。"


def _table_preview(reportable: Sequence[SubTaskResult], *, max_rows: int = 8) -> str:
    primary = _primary_result(reportable)
    if primary is None:
        return "[]"
    rows = list(primary.result_rows)[:max_rows]
    cols = list(primary.result_columns) or (list(rows[0].keys()) if rows else [])
    return str(
        {
            "sub_task_id": primary.sub_task_id,
            "columns": cols,
            "rows": rows,
        }
    )
