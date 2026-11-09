# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_reflex.models import (
    ConfirmReq,
    StrategyFlags,
    TaskEvent,
    TaskSubmitReq,
    create_initial_task_state,
)


def test_initial_task_state_defaults() -> None:
    state = create_initial_task_state(dataset_id="ds1", user_query="退货率 Top5")
    assert state["status"] == "pending"
    assert state["retry_counts"] == {}
    assert state["error_fingerprints"] == []
    assert state["llm_calls_used"] == 0
    assert state["strategy_flags"].enable_human_confirm is True
    assert state["intent"] is None
    assert state["plan"] is None
    assert state["final_report"] is None
    assert state["fallback_report"] is None
    assert state["sub_task_results"] == []
    assert state["dataset_id"] == "ds1"


def test_strategy_flags_all_enabled_by_default() -> None:
    flags = StrategyFlags()
    assert flags.enable_planner is True
    assert flags.enable_reflector is True
    assert flags.enable_sandbox is True
    assert flags.enable_human_confirm is True
    assert flags.enable_fallback_report is True


def test_confirm_req_requires_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        ConfirmReq(
            task_id="t1",
            confirm_type="plan",
            decision="accept",
        )  # type: ignore[call-arg]


def test_task_event_fields() -> None:
    event = TaskEvent(
        task_id="t1",
        seq=0,
        event_type="task_started",
        payload={},
        timestamp="2025-10-01T00:00:00+08:00",
    )
    assert event.seq == 0


def test_task_submit_embeds_default_strategy_flags() -> None:
    req = TaskSubmitReq(dataset_id="ds1", user_query="渠道对比")
    assert req.strategy_flags.enable_human_confirm is True
    assert req.conversation_context == []


def test_task_submit_accepts_conversation_context() -> None:
    req = TaskSubmitReq(
        dataset_id="ds1",
        user_query="同上，排除 APP",
        conversation_context=[
            {
                "dataset_id": "ds1",
                "question_tag": "channel_compare",
                "user_query": "各渠道对比",
                "constraints": [],
            }
        ],
    )
    assert len(req.conversation_context) == 1
    assert req.conversation_context[0]["question_tag"] == "channel_compare"
