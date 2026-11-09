# -*- coding: utf-8 -*-
"""Graph 各节点调用 LLM 时用的 Prompt 片段（v2-1）。

和自愈相关的是 ``reflect_fix_hint_messages``：
把 ErrorClassify 的模板 suggested_fix + 错误详情交给模型，
让它输出一句短中文 fix_hint（不是让用户澄清，也不是直接改 SQL）。
"""

from __future__ import annotations

import json
import re

from data_reflex.models.schemas import GenerateOpReq, SubTask

_SCHEMA_BLURB = """
Fixed read-only tables (SQLite / CSV stems):
- orders(order_id, user_id, product_id, product_name, channel, order_date, amount, quantity, is_promo)
- returns(return_id, order_id, product_id, return_date, reason)
- users(user_id, user_segment, register_date)
Only SELECT (or pandas read-only analysis). No INSERT/UPDATE/DELETE/DDL.
Python ops must assign a DataFrame named `result` (tables available as DataFrames: orders, returns, users).
""".strip()


def generate_op_messages(req: GenerateOpReq) -> list[dict[str, str]]:
    st = req.sub_task
    payload = {
        "sub_task_id": st.task_id,
        "description": st.description,
        "expected_input": st.expected_input,
        "expected_output": st.expected_output,
        "depends_on": st.depends_on,
        "constraints": list(req.constraints),
        "previous_error": req.previous_error,
    }
    system = (
        "You generate one analysis operation for DataReflex. "
        "Reply with ONLY a JSON object: "
        '{"op_type":"sql"|"python","op_content":"..."}. '
        "No markdown fences. Prefer SQL SELECT. " + _SCHEMA_BLURB
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def parse_generate_op_json(text: str) -> tuple[str, str]:
    """Extract ``(op_type, op_content)`` from model text."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    op_type = str(data.get("op_type") or "").strip().lower()
    op_content = str(data.get("op_content") or "")
    if op_type not in {"sql", "python"}:
        raise ValueError(f"invalid op_type: {op_type}")
    if not op_content.strip():
        raise ValueError("op_content is empty")
    return op_type, op_content


def reflect_fix_hint_messages(
    *,
    sub_task: SubTask,
    error_detail: str,
    suggested_fix: str | None,
) -> list[dict[str, str]]:
    """构造 Reflect 润色「修复提示」的 chat messages。

    调用方：``Reflector._maybe_enrich_hint``。
    模型只输出短中文提示；真正再生成 SQL/Python 仍由 GenerateOp 节点负责。
    """
    system = (
        "You help repair a failed analysis op. "
        "Reply with ONE short Chinese fix hint (max 120 chars), no code fences. "
        "Do not invent table columns beyond the schema. " + _SCHEMA_BLURB
    )
    user = json.dumps(
        {
            "sub_task_id": sub_task.task_id,
            "description": sub_task.description,
            "error_detail": error_detail,  # 沙箱里的关键错误行
            "suggested_fix": suggested_fix,  # 分类器模板建议
        },
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def clarification_messages(*, vague: list[str], question: str) -> list[dict[str, str]]:
    system = (
        "Rewrite one concise Chinese clarification question for an analytics bot. "
        "Ask the user to replace vague phrases with explicit filters "
        "(channel enum, TopN, time grain, promo flag). "
        "Reply with plain text only, one paragraph, max 200 chars."
    )
    user = json.dumps(
        {"user_question": question, "vague_phrases": vague},
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def conclusion_polish_messages(
    *,
    user_query: str,
    question_tag: str,
    draft: str,
    table_preview: str,
) -> list[dict[str, str]]:
    system = (
        "You polish a Chinese analytics conclusion for a demo dashboard. "
        "Rules: (1) Keep EVERY number from the draft unchanged "
        "(same digits; you may add a % display only if the draft already implies a rate). "
        "(2) Do not invent channels, products, segments, or facts not in the draft/table. "
        "(3) One short paragraph, plain Chinese, max 200 Chinese characters. "
        "(4) No markdown, no bullet list, no English labels."
    )
    user = json.dumps(
        {
            "user_question": user_query,
            "question_tag": question_tag,
            "draft_conclusion": draft,
            "table_preview": table_preview,
        },
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def clip_hint(text: str, *, limit: int = 120) -> str:
    """压空白并截断 fix_hint，避免提示过长污染后续 GenerateOp。"""
    t = " ".join(text.strip().split())
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"
