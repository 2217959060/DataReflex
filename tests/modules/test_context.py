# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from data_reflex.models.state import ContextManageReq
from data_reflex.modules.context.manager import (
    ContextManager,
    detects_anaphora,
    infer_question_tag,
)


@pytest.fixture()
def manager() -> ContextManager:
    return ContextManager()


def _turn(
    *,
    dataset_id: str = "ds-1",
    question_tag: str = "channel_compare",
    user_query: str = "各渠道销售额对比",
    plan: dict | None = None,
    results: list | None = None,
    constraints: list | None = None,
) -> dict:
    return {
        "dataset_id": dataset_id,
        "question_tag": question_tag,
        "user_query": user_query,
        "plan": plan or {"steps": 1},
        "results": results or [{"channel": "APP", "amount": 100}],
        "constraints": constraints or ["channel IS NOT NULL"],
    }


def test_dataset_switch_clears_context(manager: ContextManager) -> None:
    req = ContextManageReq(
        dataset_id="ds-2",
        previous_dataset_id="ds-1",
        conversation_context=[_turn(dataset_id="ds-1")],
        user_query="各渠道对比",
    )
    resp = manager.manage(req, question_tag="channel_compare")
    assert resp.context_cleared is True
    assert resp.is_new_topic is True
    assert resp.active_context == []
    assert resp.clarification_required is False


def test_same_topic_retains_recent_turns(manager: ContextManager) -> None:
    history = [
        _turn(question_tag="channel_compare", user_query="渠道对比 round1"),
        _turn(question_tag="channel_compare", user_query="渠道对比 round2"),
        _turn(question_tag="channel_compare", user_query="渠道对比 round3"),
    ]
    req = ContextManageReq(
        dataset_id="ds-1",
        previous_dataset_id="ds-1",
        conversation_context=history,
        user_query="再看一下各渠道金额",
    )
    resp = manager.manage(req, question_tag="channel_compare")
    assert resp.context_cleared is False
    assert resp.is_new_topic is False
    assert resp.clarification_required is False
    assert len(resp.active_context) == 2
    assert resp.active_context[0]["user_query"] == "渠道对比 round2"
    assert resp.active_context[1]["user_query"] == "渠道对比 round3"


def test_tag_change_without_anaphora_is_new_topic(manager: ContextManager) -> None:
    req = ContextManageReq(
        dataset_id="ds-1",
        previous_dataset_id="ds-1",
        conversation_context=[_turn(question_tag="channel_compare")],
        user_query="统计各产品退货率 Top5",
    )
    resp = manager.manage(req, question_tag="return_rate_topn")
    assert resp.is_new_topic is True
    assert resp.active_context == []
    assert resp.context_cleared is False


def test_tag_change_with_anaphora_keeps_context(manager: ContextManager) -> None:
    prior = _turn(question_tag="return_rate_topn", user_query="退货率 Top10")
    req = ContextManageReq(
        dataset_id="ds-1",
        previous_dataset_id="ds-1",
        conversation_context=[prior],
        user_query="再按渠道看退货率",
    )
    resp = manager.manage(req, question_tag="channel_compare")
    assert resp.is_new_topic is False
    assert len(resp.active_context) == 1
    assert resp.clarification_required is False


def test_unresolved_anaphora_requires_clarification(manager: ContextManager) -> None:
    req = ContextManageReq(
        dataset_id="ds-1",
        previous_dataset_id="ds-1",
        conversation_context=[],
        user_query="再按渠道排除异常订单",
    )
    resp = manager.manage(req)
    assert resp.clarification_required is True
    assert resp.clarification_questions
    assert resp.active_context == []
    assert resp.context_cleared is False


def test_anaphora_with_context_does_not_clarify(manager: ContextManager) -> None:
    req = ContextManageReq(
        dataset_id="ds-1",
        previous_dataset_id="ds-1",
        conversation_context=[_turn()],
        user_query="同上，排除 APP 渠道",
    )
    resp = manager.manage(req, question_tag="channel_compare")
    assert resp.clarification_required is False
    assert len(resp.active_context) == 1


def test_infer_and_detect_helpers() -> None:
    assert infer_question_tag("各渠道销售额对比") == "channel_compare"
    assert infer_question_tag("上个月退货率 Top3") == "return_rate_topn"
    assert infer_question_tag("你好") is None
    assert detects_anaphora("再按渠道过滤") is True
    assert detects_anaphora("各渠道销售额对比") is False
