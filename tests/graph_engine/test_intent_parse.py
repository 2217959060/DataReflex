# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.graph_engine.nodes.intent_parse import NodeIntentParse
from data_reflex.models.state import create_initial_task_state
from data_reflex.modules.intent_parse.parser import (
    IntentParser,
    extract_constraints,
    merge_constraints,
)
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


@pytest.fixture()
def node() -> NodeIntentParse:
    return NodeIntentParse()


def test_in_scope_channel_compare_with_constraints(node: NodeIntentParse) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="对比 APP 与 官网 渠道销售额",
        task_id="t-intent-1",
    )
    delta = node.run(state)
    intent = delta["intent"]
    assert intent.in_scope is True
    assert intent.need_clarification is False
    assert intent.question_tag == "channel_compare"
    assert intent.reject_reason is None
    assert any("channel IN" in c for c in intent.constraints)
    assert "APP" in intent.constraints[0] or any("APP" in c for c in intent.constraints)
    assert intent.normalized_query is not None


def test_out_of_scope_returns_reject_reason(node: NodeIntentParse) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="预测下个月各渠道销售额",
        task_id="t-oos",
    )
    delta = node.run(state)
    intent = delta["intent"]
    assert intent.in_scope is False
    assert intent.reject_reason is not None
    assert (
        "不支持" in intent.reject_reason
        or "六类" in intent.reject_reason
        or "预测" in intent.reject_reason
    )
    assert intent.question_tag is None


def test_unmapped_query_out_of_scope(node: NodeIntentParse) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="今天天气怎么样",
        task_id="t-weather",
    )
    delta = node.run(state)
    intent = delta["intent"]
    assert intent.in_scope is False
    assert intent.reject_reason is not None
    assert "六类" in intent.reject_reason


def test_fuzzy_anaphora_requires_clarification(node: NodeIntentParse) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="再按渠道排除异常订单",
        task_id="t-ana",
        conversation_context=[],
    )
    delta = node.run(state)
    intent = delta["intent"]
    assert intent.need_clarification is True
    assert intent.clarification_questions
    assert intent.in_scope is True
    assert intent.normalized_query is None


def test_vague_nl_constraint_requires_clarification(node: NodeIntentParse) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="按用户喜好看各渠道对比",
        task_id="t-vague",
    )
    delta = node.run(state)
    intent = delta["intent"]
    assert intent.question_tag == "channel_compare"
    assert intent.need_clarification is True
    assert any("喜好" in q or "显式" in q for q in intent.clarification_questions)


def test_return_rate_topn_extracts_top_n(node: NodeIntentParse) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="上个月退货率 Top5 产品",
        task_id="t-topn",
    )
    delta = node.run(state)
    intent = delta["intent"]
    assert intent.in_scope is True
    assert intent.question_tag == "return_rate_topn"
    assert "top_n=5" in intent.constraints
    assert "relative_period=last_month" in intent.constraints


def test_anaphora_with_context_inherits_tag(node: NodeIntentParse) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="同上，排除 APP",
        task_id="t-ctx",
        conversation_context=[
            {
                "dataset_id": "ds-1",
                "question_tag": "channel_compare",
                "user_query": "各渠道对比",
                "plan": {},
                "results": [],
                "constraints": ["channel IN ('APP', '官网')"],
            }
        ],
    )
    delta = node.run(state)
    intent = delta["intent"]
    assert intent.need_clarification is False
    assert intent.in_scope is True
    assert intent.question_tag == "channel_compare"
    assert any("exclude=APP" in c for c in intent.constraints)
    assert any(c == "channel IN ('官网')" for c in intent.constraints)
    assert len(delta["conversation_context"]) == 1


def test_entity_anaphora_asks_clarification(node: NodeIntentParse) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="刚才那个渠道怎么样",
        task_id="t-entity",
        conversation_context=[
            {
                "dataset_id": "ds-1",
                "question_tag": "channel_compare",
                "user_query": "各渠道对比",
                "constraints": ["channel IN ('APP', '官网')"],
                "plan": {},
                "results": [],
            }
        ],
    )
    delta = node.run(state)
    intent = delta["intent"]
    assert intent.need_clarification is True
    assert intent.in_scope is True


def test_anaphora_without_context_clarifies(node: NodeIntentParse) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="同上，排除 APP",
        task_id="t-no-ctx",
        conversation_context=[],
    )
    delta = node.run(state)
    intent = delta["intent"]
    assert intent.need_clarification is True


def test_extract_constraints_helper() -> None:
    cons, vague = extract_constraints("按日看时间趋势", "time_trend")
    assert "time_grain=day" in cons
    assert vague == []


def test_extract_top_n_with_space() -> None:
    cons, _ = extract_constraints(
        "退货率最高的前 5 个商品有哪些？", "return_rate_topn"
    )
    assert "top_n=5" in cons


def test_extract_channel_in_pair() -> None:
    cons, _ = extract_constraints(
        "对比 APP 和官网 渠道销售额", "channel_compare"
    )
    assert any("channel IN" in c and "APP" in c and "官网" in c for c in cons)


def test_parser_direct_api() -> None:
    from data_reflex.models.schemas import IntentParseReq

    parser = IntentParser()
    intent, ctx = parser.parse(
        IntentParseReq(dataset_id="ds", user_query="用户分层对比销售额")
    )
    assert intent.question_tag == "segment_compare"
    assert ctx.clarification_required is False


def test_merge_constraints_current_overrides_and_exclude_appends() -> None:
    prior = ["channel IN ('APP', '官网')", "top_n=10", "exclude=小程序"]
    current = ["top_n=5", "exclude=APP"]
    merged = merge_constraints(prior, current)
    assert "top_n=5" in merged
    assert "top_n=10" not in merged
    # exclude strips APP from the prior IN-list
    assert "channel IN ('官网')" in merged
    assert "channel IN ('APP', '官网')" not in merged
    assert "exclude=小程序" in merged
    assert "exclude=APP" in merged


def test_exclude_app_does_not_become_channel_in() -> None:
    cons, _ = extract_constraints("同上，排除 APP", "channel_compare")
    assert "exclude=APP" in cons
    assert not any("channel IN" in c for c in cons)


def test_followup_exclude_merges_prior_channel_in(node: NodeIntentParse) -> None:
    state = create_initial_task_state(
        dataset_id="ds-1",
        user_query="同上，排除 APP",
        task_id="t-excl",
        conversation_context=[
            {
                "dataset_id": "ds-1",
                "question_tag": "channel_compare",
                "user_query": "对比 APP 和官网 渠道销售额",
                "constraints": ["channel IN ('APP', '官网')"],
                "plan": {},
                "results": [],
            }
        ],
    )
    intent = node.run(state)["intent"]
    assert intent.need_clarification is False
    assert intent.question_tag == "channel_compare"
    assert "exclude=APP" in intent.constraints
    assert any(c == "channel IN ('官网')" for c in intent.constraints)
