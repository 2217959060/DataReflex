# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.config.settings import Settings
from data_reflex.graph_engine.nodes.reflect import NodeReflect
from data_reflex.models.schemas import (
    ErrorClassifyResp,
    PlanResp,
    ReflectReq,
    SubTask,
)
from data_reflex.models.state import StrategyFlags, create_initial_task_state
from data_reflex.modules.reflect.reflector import Reflector
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


def _classified(
    *,
    fingerprint: str = "syntax|sql syntax error near from",
    retryable: bool = True,
    error_type: str = "syntax",
) -> ErrorClassifyResp:
    return ErrorClassifyResp(
        error_type=error_type,  # type: ignore[arg-type]
        error_detail="SQL syntax error near FROM",
        error_fingerprint=fingerprint,
        retryable=retryable,
        suggested_fix="Fix SQL syntax and regenerate.",
    )


def _state(
    *,
    fingerprints: list[str],
    classified: ErrorClassifyResp | None = None,
    retry_count: int = 0,
    llm_calls_used: int = 0,
    max_retry: int = 3,
    max_llm: int = 20,
    enable_reflector: bool = True,
    sub_id: str = "st-1",
) -> dict[str, Any]:
    settings = Settings.model_validate(
        {
            "max_retry_rounds": max_retry,
            "max_llm_calls_per_task": max_llm,
        }
    )
    state: dict[str, Any] = dict(
        create_initial_task_state(
            dataset_id="ds-1",
            user_query="渠道对比",
            task_id="t-reflect",
            strategy_flags=StrategyFlags(enable_reflector=enable_reflector),
        )
    )
    state["current_sub_task_id"] = sub_id
    state["error_fingerprints"] = fingerprints
    state["error_classify"] = classified or _classified()
    state["retry_counts"] = {sub_id: retry_count} if retry_count else {}
    state["llm_calls_used"] = llm_calls_used
    state["plan"] = PlanResp(
        success=True,
        sub_tasks=[SubTask(task_id=sub_id, description="agg by channel")],
    )
    state["_settings"] = settings
    return state


def _node(state: dict[str, Any]) -> NodeReflect:
    return NodeReflect(settings=state["_settings"])


def test_first_failure_retries_to_generate_op() -> None:
    """First occurrence of a fingerprint → continue heal."""
    fp = "syntax|sql syntax error near from"
    state = _state(fingerprints=[fp], classified=_classified(fingerprint=fp))
    delta = _node(state).run(state)  # type: ignore[arg-type]
    assert delta["next_node"] == "generate_op"
    assert delta["reflect"].should_retry is True
    assert delta["reflect"].circuit_breaker is False
    assert delta["reflect"].final_status == "retrying"
    assert delta["reflect"].fix_hint
    assert delta["retry_counts"]["st-1"] == 1
    assert delta["sub_task_results"][0].status == "healing"


def test_second_same_fingerprint_melts() -> None:
    fp = "syntax|sql syntax error near from"
    state = _state(
        fingerprints=[fp, fp],
        classified=_classified(fingerprint=fp),
        retry_count=1,
    )
    delta = _node(state).run(state)  # type: ignore[arg-type]
    assert delta["next_node"] == "report"
    assert delta["reflect"].circuit_breaker is True
    assert delta["reflect"].melt_reason == "duplicate_fingerprint"
    assert delta["reflect"].should_retry is False
    assert delta["reflect"].final_status == "melted"


def test_different_fingerprints_do_not_false_melt() -> None:
    state = _state(
        fingerprints=[
            "syntax|sql syntax error near from",
            "runtime|zerodivisionerror division by zero",
        ],
        classified=_classified(
            fingerprint="runtime|zerodivisionerror division by zero",
            error_type="runtime",
        ),
        retry_count=1,
    )
    delta = _node(state).run(state)  # type: ignore[arg-type]
    assert delta["reflect"].circuit_breaker is False
    assert delta["reflect"].should_retry is True
    assert delta["next_node"] == "generate_op"


def test_llm_call_limit_melts_with_correct_reason() -> None:
    fp = "schema_semantic|no such column amountx"
    state = _state(
        fingerprints=[fp],
        classified=_classified(fingerprint=fp, error_type="schema_semantic"),
        llm_calls_used=20,
        max_llm=20,
    )
    delta = _node(state).run(state)  # type: ignore[arg-type]
    assert delta["next_node"] == "report"
    assert delta["reflect"].melt_reason == "llm_call_limit"
    assert delta["reflect"].circuit_breaker is True


def test_llm_call_limit_outranks_duplicate_fingerprint() -> None:
    fp = "syntax|sql syntax error near from"
    state = _state(
        fingerprints=[fp, fp],
        classified=_classified(fingerprint=fp),
        llm_calls_used=5,
        max_llm=5,
        retry_count=1,
    )
    delta = _node(state).run(state)  # type: ignore[arg-type]
    assert delta["reflect"].melt_reason == "llm_call_limit"


def test_retry_limit_melts() -> None:
    fp = "runtime|valueerror boom"
    state = _state(
        fingerprints=[fp],
        classified=_classified(fingerprint=fp, error_type="runtime"),
        retry_count=3,
        max_retry=3,
    )
    delta = _node(state).run(state)  # type: ignore[arg-type]
    assert delta["reflect"].melt_reason == "retry_limit"
    assert delta["next_node"] == "report"


def test_reflector_unit_consecutive_rule() -> None:
    reflector = Reflector()
    classified = _classified(fingerprint="a|x")
    base = dict(
        sub_task=SubTask(task_id="s", description="d"),
        error_classify=classified,
        max_retry=3,
        llm_calls_used=0,
        max_llm_calls=20,
    )
    r1 = reflector.reflect(
        ReflectReq(retry_count=0, previous_fingerprints=["a|x"], **base)
    )
    assert r1.should_retry is True
    r2 = reflector.reflect(
        ReflectReq(retry_count=1, previous_fingerprints=["a|x", "a|x"], **base)
    )
    assert r2.melt_reason == "duplicate_fingerprint"
