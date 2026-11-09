# -*- coding: utf-8 -*-
"""意图解析：固定三表六类；规则引擎为主，可选 LLM 润色澄清问句。

上游：``Node_IntentParse``；先跑 ``ContextManager``。
本文件：超范围拒绝、模糊词澄清、显式约束抽取、六类标签映射。
下游：写 ``IntentParseResp`` → 节点设 ``next_node`` 为 plan 或 report。

核心路径不调 LLM（评测可复现）；``enable_llm_ops`` 时仅润色澄清文案。

See ``tasks.md`` T20 / D-INTENT-01。
"""

from __future__ import annotations

import re
from typing import Any, Literal, cast

from data_reflex.config.settings import Settings, get_settings
from data_reflex.models.schemas import IntentParseReq, IntentParseResp
from data_reflex.models.state import ContextManageReq, ContextManageResp
from data_reflex.modules.context.manager import (
    QUESTION_TAGS,
    ContextManager,
    detects_anaphora,
    infer_question_tag,
)
from data_reflex.modules.llm.client import LlmClient, LlmError, build_llm_client
from data_reflex.modules.llm.prompts import clarification_messages, clip_hint
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

QuestionTagLiteral = Literal[
    "return_rate_topn",
    "channel_compare",
    "time_trend",
    "segment_compare",
    "promo_compare",
    "repurchase_rate",
]

# v1 excluded capabilities (schema.md §8).
_OUT_OF_SCOPE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"预测",
        r"预报",
        r"forecast",
        r"聚类",
        r"cluster",
        r"训练模型",
        r"机器学习",
        r"embedding",
        r"写[入进库]",
        r"删[除掉]",
        r"\bupdate\b",
        r"\binsert\b",
        r"\bdrop\b",
        r"删改文件",
        r"库存",
        r"inventory",
        r"评论情感",
        r"推荐系统",
    )
)

# Foreign tables / domains not in the three-table contract.
_FOREIGN_DOMAIN_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"库存表",
        r"评论表",
        r"物流表",
        r"广告表",
        r"\binventory\b",
        r"\breviews?\b",
        r"\blogistics\b",
    )
)

# NL constraint cues that cannot become structured predicates in v1.
_VAGUE_CONSTRAINT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"好看",
        r"合适",
        r"差不多",
        r"比较好",
        r"体验好",
        r"用户喜好",
        r"感觉不错",
        r"随便",
        r"尽量",
        r"大概",
    )
)

# Allow spaces: 「前 5」「前5」「Top 5」「退货率最高的前 5 个」
_TOP_N_RE = re.compile(
    r"(?:top\s*[#-]?\s*|前\s*|最高(?:的)?前\s*|退货率\s*top\s*)(\d+)",
    re.IGNORECASE,
)
_CHANNEL_VALUES = ("APP", "官网", "线下门店", "第三方平台")
_SEGMENT_VALUES = ("新客", "老客", "VIP")
_TIME_GRAIN_RE = re.compile(r"按\s*([日周月])|按天|daily|weekly|monthly", re.IGNORECASE)
_LAST_MONTH_RE = re.compile(r"上个?月|last\s+month", re.IGNORECASE)
_EXCLUDE_RE = re.compile(r"排除\s*([A-Za-z0-9_\u4e00-\u9fff]+)")
_PROMO_ONLY_RE = re.compile(r"仅?(?:促销|promo)(?!与非)", re.IGNORECASE)
_NON_PROMO_RE = re.compile(r"非促销|非\s*promo", re.IGNORECASE)


class IntentParser:
    """规则映射用户问题 → IntentParseResp；澄清/拒绝仍算业务成功分支。"""

    def __init__(
        self,
        *,
        context_manager: ContextManager | None = None,
        settings: Settings | None = None,
        llm: LlmClient | None = None,
    ) -> None:
        self._context = context_manager or ContextManager()
        self._settings = settings or get_settings()
        self._llm = llm
        self.last_llm_calls: int = 0

    def parse(
        self,
        req: IntentParseReq,
        *,
        previous_dataset_id: str | None = None,
        context_resp: ContextManageResp | None = None,
    ) -> tuple[IntentParseResp, ContextManageResp]:
        """Parse intent and apply context rules.

        Returns ``(intent, context_resp)``. When ``context_resp`` is provided it
        is reused; otherwise ``ContextManager.manage`` runs first.
        """
        self.last_llm_calls = 0
        query = req.user_query.strip()
        tag_hint = infer_question_tag(query)

        if context_resp is None:
            ctx_req = ContextManageReq(
                dataset_id=req.dataset_id,
                previous_dataset_id=previous_dataset_id,
                conversation_context=list(req.conversation_context),
                user_query=query,
            )
            context_resp = self._context.manage(ctx_req, question_tag=tag_hint)

        # 1) Unresolved anaphora → clarify, do not guess.
        if context_resp.clarification_required:
            intent = IntentParseResp(
                in_scope=True,
                reject_reason=None,
                need_clarification=True,
                clarification_questions=list(context_resp.clarification_questions)
                or ["无法解析指代，请改写为不依赖「再按/同上/它」的完整分析请求。"],
                normalized_query=None,
                constraints=[],
                question_tag=None,
            )
            return intent, context_resp

        # 2) Hard out-of-scope capabilities / foreign domains.
        oos_reason = self._out_of_scope_reason(query)
        if oos_reason is not None:
            intent = IntentParseResp(
                in_scope=False,
                reject_reason=oos_reason,
                need_clarification=False,
                clarification_questions=[],
                normalized_query=None,
                constraints=[],
                question_tag=None,
            )
            return intent, context_resp

        # 3) Resolve tag: current hint, or inherit from active context on anaphora.
        question_tag = tag_hint
        if (
            question_tag is None
            and detects_anaphora(query)
            and context_resp.active_context
        ):
            question_tag = self._tag_from_context(context_resp.active_context)

        if question_tag is None or question_tag not in QUESTION_TAGS:
            intent = IntentParseResp(
                in_scope=False,
                reject_reason=(
                    "请求无法映射到 v1 固定六类问题"
                    "（退货率 TopN / 渠道对比 / 时间趋势 / 用户分层对比 / "
                    "促销对比 / 复购率）。"
                ),
                need_clarification=False,
                clarification_questions=[],
                normalized_query=None,
                constraints=[],
                question_tag=None,
            )
            return intent, context_resp

        # 4) Extract explicit constraints; vague NL → clarification.
        constraints, vague = extract_constraints(query, question_tag)
        if vague:
            default_q = (
                "以下表述无法转为显式条件，请改用可执行的过滤："
                + "、".join(vague)
                + "。例如指定渠道枚举、TopN、时间粒度或促销标记。"
            )
            intent = IntentParseResp(
                in_scope=True,
                reject_reason=None,
                need_clarification=True,
                clarification_questions=[
                    self._polish_clarification(query, vague, default_q)
                ],
                normalized_query=None,
                constraints=constraints,
                question_tag=cast(QuestionTagLiteral, question_tag),
            )
            return intent, context_resp

        # V2-5 / 2A: do not guess entity refs like「刚才那个渠道」.
        if needs_entity_anaphora_clarify(query, constraints):
            intent = IntentParseResp(
                in_scope=True,
                reject_reason=None,
                need_clarification=True,
                clarification_questions=[
                    "无法从上一轮结果解析「刚才那个 / 那个渠道」等实体指代。"
                    "请改写成显式条件，例如「排除 APP」或「只看官网渠道」。"
                ],
                normalized_query=None,
                constraints=constraints,
                question_tag=cast(QuestionTagLiteral, question_tag),
            )
            return intent, context_resp

        # Same tag + retained context → merge prior constraints (current wins).
        prior_tag = self._tag_from_context(context_resp.active_context)
        if context_resp.active_context and prior_tag == question_tag:
            prior = _constraints_from_context(context_resp.active_context)
            constraints = merge_constraints(prior, constraints)

        normalized = normalize_query(query, question_tag, constraints)
        intent = IntentParseResp(
            in_scope=True,
            reject_reason=None,
            need_clarification=False,
            clarification_questions=[],
            normalized_query=normalized,
            constraints=constraints,
            question_tag=cast(QuestionTagLiteral, question_tag),
        )
        logger.info(
            "intent parsed",
            extra={
                "task_id": "-",
                "dataset_id": req.dataset_id,
                "question_tag": question_tag,
                "constraint_count": len(constraints),
            },
        )
        return intent, context_resp

    def _polish_clarification(
        self, query: str, vague: list[str], default_q: str
    ) -> str:
        if not (self._settings.enable_llm_ops and self._settings.llm_api_key.strip()):
            return default_q
        client = self._llm or build_llm_client(self._settings)
        try:
            text = client.chat(
                messages=clarification_messages(vague=vague, question=query),
                max_tokens=160,
            )
            self.last_llm_calls = 1
            polished = clip_hint(text, limit=200)
            return polished or default_q
        except LlmError as exc:
            logger.info(
                "clarification llm skipped",
                extra={
                    "task_id": "-",
                    "dataset_id": "-",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return default_q

    def _out_of_scope_reason(self, query: str) -> str | None:
        for pattern in _OUT_OF_SCOPE_PATTERNS:
            if pattern.search(query):
                return (
                    f"请求涉及 v1 不支持的能力（匹配: {pattern.pattern}）；"
                    "仅支持固定三表只读六类分析。"
                )
        for pattern in _FOREIGN_DOMAIN_PATTERNS:
            if pattern.search(query):
                return "请求依赖契约外的表/领域；v1 仅支持 orders / returns / users。"
        return None

    @staticmethod
    def _tag_from_context(turns: list[dict[str, Any]]) -> str | None:
        if not turns:
            return None
        raw = turns[-1].get("question_tag")
        if raw is None:
            return None
        tag = str(raw).strip()
        return tag if tag in QUESTION_TAGS else None


# Incremental follow-ups we can resolve without reading prior result rows.
_INCREMENTAL_ANAPHORA_RE = re.compile(
    r"排除|再按|同上|同样|继续|基于上|在此基础上|\bagain\b|\bsame\b|\bexclude\b",
    re.IGNORECASE,
)
# Soft entity pointers that would require reading prior tables (out of V2-5 2A).
_ENTITY_ANAPHORA_RE = re.compile(
    r"刚才那个|那个渠道|那个商品|那个结果|这个结果|上轮的那个|"
    r"(?<![A-Za-z\u4e00-\u9fff])它(?![们A-Za-z\u4e00-\u9fff])",
)


def needs_entity_anaphora_clarify(user_query: str, current_constraints: list[str]) -> bool:
    """True when the query points at a prior-row entity we refuse to guess."""
    if not detects_anaphora(user_query):
        return False
    if _INCREMENTAL_ANAPHORA_RE.search(user_query):
        return False
    if not _ENTITY_ANAPHORA_RE.search(user_query):
        return False
    # Defaults from extract_constraints do not count as user-supplied filters.
    actionable = [
        c
        for c in current_constraints
        if not c.startswith("top_n=")
        and not c.startswith("time_grain=")
        and not c.startswith("compare_by=")
    ]
    return len(actionable) == 0


def _constraints_from_context(turns: list[dict[str, Any]]) -> list[str]:
    if not turns:
        return []
    raw = turns[-1].get("constraints")
    if not isinstance(raw, list):
        return []
    return [str(c) for c in raw if c]


def merge_constraints(prior: list[str], current: list[str]) -> list[str]:
    """Merge prior-turn constraints with current; current overrides same key.

    ``exclude=`` entries are unioned. Prefix keys (``top_n=``, ``channel IN``, …)
    keep a single winner from ``current`` when present. After merge, excluded
    values are stripped from any ``channel IN`` / ``user_segment IN`` lists so
    we never emit contradictory ``IN ('APP') AND channel <> 'APP'``.
    """
    override_prefixes = (
        "top_n=",
        "channel IN",
        "user_segment IN",
        "relative_period=",
        "time_grain=",
        "is_promo=",
        "compare_by=",
    )

    def _prefix(item: str) -> str | None:
        for p in override_prefixes:
            if item.startswith(p):
                return p
        return None

    current_prefixes = {_prefix(c) for c in current if _prefix(c)}
    out: list[str] = []
    seen_exclude: set[str] = set()

    for item in prior:
        if item.startswith("exclude="):
            if item not in seen_exclude and item not in current:
                out.append(item)
                seen_exclude.add(item)
            continue
        p = _prefix(item)
        if p is not None and p in current_prefixes:
            continue
        if item not in out:
            out.append(item)

    for item in current:
        if item.startswith("exclude="):
            if item not in seen_exclude:
                out.append(item)
                seen_exclude.add(item)
            continue
        if item not in out:
            out.append(item)

    return _apply_excludes_to_in_lists(out)


_IN_LIST_RE = re.compile(
    r"^(channel|user_segment)\s+IN\s*\(([^)]*)\)\s*$", re.IGNORECASE
)


def _apply_excludes_to_in_lists(constraints: list[str]) -> list[str]:
    """Remove excluded tokens from IN-lists; drop empty IN clauses."""
    excluded = {
        item.split("=", 1)[1]
        for item in constraints
        if item.startswith("exclude=") and "=" in item
    }
    if not excluded:
        return constraints

    out: list[str] = []
    for item in constraints:
        m = _IN_LIST_RE.match(item.strip())
        if not m:
            out.append(item)
            continue
        col = m.group(1)
        kept: list[str] = []
        for token in m.group(2).split(","):
            t = token.strip()
            if not t:
                continue
            if (t.startswith("'") and t.endswith("'")) or (
                t.startswith('"') and t.endswith('"')
            ):
                inner = t[1:-1]
            else:
                inner = t
            if inner in excluded:
                continue
            kept.append(f"'{inner}'")
        if kept:
            out.append(f"{col} IN ({', '.join(kept)})")
        # empty IN after exclude → omit; bare exclude= still filters in SQL
    return out


def extract_constraints(
    user_query: str, question_tag: str
) -> tuple[list[str], list[str]]:
    """Return ``(explicit_constraints, vague_phrases)``."""
    constraints: list[str] = []
    vague: list[str] = []

    for pattern in _VAGUE_CONSTRAINT_PATTERNS:
        match = pattern.search(user_query)
        if match:
            vague.append(match.group(0))

    top = _TOP_N_RE.search(user_query)
    if top:
        constraints.append(f"top_n={int(top.group(1))}")
    elif question_tag == "return_rate_topn":
        constraints.append("top_n=10")  # schema-typical default when TopN implied

    # Collect excludes first so 「排除 APP」 does not also become channel IN ('APP').
    excluded_vals = [m.group(1) for m in _EXCLUDE_RE.finditer(user_query)]
    for val in excluded_vals:
        constraints.append(f"exclude={val}")
    excluded_set = set(excluded_vals)

    found_channels = [
        c for c in _CHANNEL_VALUES if c in user_query and c not in excluded_set
    ]
    if found_channels:
        quoted = ", ".join(f"'{c}'" for c in found_channels)
        constraints.append(f"channel IN ({quoted})")

    found_segments = [
        s for s in _SEGMENT_VALUES if s in user_query and s not in excluded_set
    ]
    if found_segments:
        quoted = ", ".join(f"'{s}'" for s in found_segments)
        constraints.append(f"user_segment IN ({quoted})")

    if _LAST_MONTH_RE.search(user_query):
        constraints.append("relative_period=last_month")

    grain = _TIME_GRAIN_RE.search(user_query)
    if grain:
        token = grain.group(0)
        if "日" in token or "天" in token or "daily" in token.lower():
            constraints.append("time_grain=day")
        elif "周" in token or "weekly" in token.lower():
            constraints.append("time_grain=week")
        else:
            constraints.append("time_grain=month")
    elif question_tag == "time_trend" and "time_grain=" not in " ".join(constraints):
        constraints.append("time_grain=day")

    if _NON_PROMO_RE.search(user_query):
        constraints.append("is_promo=0")
    elif _PROMO_ONLY_RE.search(user_query) and "促销与非" not in user_query:
        constraints.append("is_promo=1")
    elif question_tag == "promo_compare":
        constraints.append("compare_by=is_promo")

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for item in constraints:
        if item not in seen:
            seen.add(item)
            unique.append(item)

    vague_unique = list(dict.fromkeys(vague))
    return unique, vague_unique


def normalize_query(user_query: str, question_tag: str, constraints: list[str]) -> str:
    """Build a short normalized description for planning."""
    parts = [f"tag={question_tag}", f"query={user_query.strip()}"]
    if constraints:
        parts.append("constraints=[" + "; ".join(constraints) + "]")
    return " | ".join(parts)
