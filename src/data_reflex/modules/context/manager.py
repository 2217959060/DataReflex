# -*- coding: utf-8 -*-
"""同数据集会话上下文（最近 1–2 轮）：新话题隔离、指代线索、澄清门禁。

上游：前端携带 ``conversation_context``；``Node_IntentParse`` 调用本模块。
本文件：按固定规则决定保留/清空/要求澄清（不猜测实体指代）。
下游：意图解析继承标签/约束；无法绑定则 ``clarification_required``。

规则 id：``dataset_or_label_change_without_anaphora``（``defaults.md`` §3）。

See ``tasks.md`` T18 / D-CTX-01。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from data_reflex.models.state import ContextManageReq, ContextManageResp
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

# Locked by schema.md §7 / D-MODEL-01.
QUESTION_TAGS: frozenset[str] = frozenset(
    {
        "return_rate_topn",
        "channel_compare",
        "time_trend",
        "segment_compare",
        "promo_compare",
        "repurchase_rate",
    }
)

MAX_CONTEXT_TURNS = 2
NEW_TOPIC_RULE_ID = "dataset_or_label_change_without_anaphora"

# Cue that the user is referring to prior turns (v1 lexical rules only).
_ANAPHORA_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"再按",
        r"排除",
        r"同上",
        r"同样",
        r"继续",
        r"基于上",
        r"在此基础上",
        r"刚才",
        r"上述",
        r"上轮",
        r"上一[轮次]",
        r"它们",
        r"它",
        r"这个结果",
        r"那个结果",
        r"这些",
        r"那些",
        r"\bagain\b",
        r"\bsame\b",
        r"\bas\s+above\b",
        r"\bexclude\b",
        r"\bfilter\s+further\b",
        r"\bprevious\b",
    )
)

# Keyword → tag (first match wins; longer/more specific phrases listed first).
_TAG_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("return_rate_topn", ("退货率", "return rate", "return_rate")),
    ("repurchase_rate", ("复购率", "复购", "repurchase")),
    ("promo_compare", ("促销", "非促销", "promo")),
    ("segment_compare", ("用户分层", "分层对比", "user_segment", "segment")),
    ("channel_compare", ("渠道对比", "渠道", "channel")),
    ("time_trend", ("时间趋势", "趋势", "按日", "按周", "按月", "time trend", "trend")),
)


class ContextManager:
    """按固定规则管理 1–2 轮上下文；指代绑不上则要求澄清，不猜测。"""

    def __init__(self, *, max_turns: int = MAX_CONTEXT_TURNS) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        self.max_turns = max_turns

    def manage(
        self,
        req: ContextManageReq,
        *,
        question_tag: str | None = None,
    ) -> ContextManageResp:
        """Evaluate context for the current user query.

        ``question_tag`` is intentionally outside ``ContextManageReq`` (IO frozen);
        when omitted, a lexical hint is inferred from ``user_query`` for rule-2.
        """
        # 1) Dataset switch → hard clear.
        if (
            req.previous_dataset_id is not None
            and req.previous_dataset_id != req.dataset_id
        ):
            logger.info(
                "context cleared on dataset switch",
                extra={
                    "task_id": "-",
                    "dataset_id": req.dataset_id,
                    "previous_dataset_id": req.previous_dataset_id,
                },
            )
            return ContextManageResp(
                active_context=[],
                is_new_topic=True,
                context_cleared=True,
                clarification_required=False,
                clarification_questions=[],
            )

        same_ds = self._filter_same_dataset(req.conversation_context, req.dataset_id)
        current_tag = self._normalize_tag(question_tag) or infer_question_tag(
            req.user_query
        )
        last_tag = self._last_question_tag(same_ds)
        has_anaphora = detects_anaphora(req.user_query)

        # 2) Anaphora with nothing to bind → clarify, do not guess.
        if has_anaphora and not same_ds:
            questions = [
                "当前没有可引用的上一轮分析结果。"
                "请说明要分析的具体指标、维度或范围（例如渠道对比、退货率 TopN），"
                "不要使用「再按 / 同上 / 它」等指代。"
            ]
            return ContextManageResp(
                active_context=[],
                is_new_topic=False,
                context_cleared=False,
                clarification_required=True,
                clarification_questions=questions,
            )

        # 3) Label change without resolvable anaphora → new topic, isolate.
        tag_changed = (
            current_tag is not None and last_tag is not None and current_tag != last_tag
        )
        if tag_changed and not has_anaphora:
            logger.info(
                "new topic: question tag changed without anaphora",
                extra={
                    "task_id": "-",
                    "dataset_id": req.dataset_id,
                    "previous_tag": last_tag,
                    "current_tag": current_tag,
                },
            )
            return ContextManageResp(
                active_context=[],
                is_new_topic=True,
                context_cleared=False,
                clarification_required=False,
                clarification_questions=[],
            )

        # 4) Same topic or resolvable anaphora → keep last 1–2 turns.
        retained = same_ds[-self.max_turns :]
        return ContextManageResp(
            active_context=list(retained),
            is_new_topic=False,
            context_cleared=False,
            clarification_required=False,
            clarification_questions=[],
        )

    def _filter_same_dataset(
        self, turns: Sequence[Mapping[str, Any]], dataset_id: str
    ) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for turn in turns:
            turn_ds = turn.get("dataset_id")
            if turn_ds is None or str(turn_ds) == dataset_id:
                kept.append(dict(turn))
        return kept

    def _last_question_tag(self, turns: Sequence[Mapping[str, Any]]) -> str | None:
        if not turns:
            return None
        return self._normalize_tag(turns[-1].get("question_tag"))

    @staticmethod
    def _normalize_tag(value: object | None) -> str | None:
        if value is None:
            return None
        tag = str(value).strip()
        if tag in QUESTION_TAGS:
            return tag
        return None


def detects_anaphora(user_query: str) -> bool:
    """Return True if the query contains v1 anaphora cues."""
    text = user_query.strip()
    if not text:
        return False
    return any(p.search(text) for p in _ANAPHORA_PATTERNS)


def infer_question_tag(user_query: str) -> str | None:
    """Lexical hint for the six fixed question tags; None if ambiguous."""
    text = user_query.strip().lower()
    if not text:
        return None
    # Also search original for Chinese (lower does not change CJK).
    original = user_query.strip()
    for tag, keywords in _TAG_KEYWORD_RULES:
        for kw in keywords:
            needle = kw.lower()
            if needle in text or kw in original:
                return tag
    return None
