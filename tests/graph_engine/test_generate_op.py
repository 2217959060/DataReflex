# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

import pytest

from data_reflex.graph_engine.nodes.generate_op import NodeGenerateOp
from data_reflex.models.schemas import (
    GenerateOpReq,
    GenerateOpResp,
    IntentParseResp,
    PlanReq,
    SubTask,
)
from data_reflex.models.state import create_initial_task_state
from data_reflex.modules.generate_op.generator import OpGenerator, validate_op_format
from data_reflex.modules.plan.builder import PlanBuilder
from data_reflex.utils.tracing import create_tracer, reset_tracer


@pytest.fixture(autouse=True)
def _noop_tracer() -> Any:
    reset_tracer(create_tracer())
    yield
    reset_tracer(None)


class _FlakyGenerator:
    """Fails format on first call per subtask, then returns valid SQL."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def generate(self, req: GenerateOpReq) -> GenerateOpResp:
        sid = req.sub_task.task_id
        self.calls[sid] = self.calls.get(sid, 0) + 1
        if self.calls[sid] == 1:
            return GenerateOpResp(
                op_type="sql",
                op_content="NOT VALID SQL (((",
                expected_input=dict(req.sub_task.expected_input),
                expected_output=dict(req.sub_task.expected_output),
                format_valid=False,
                error_msg="sql format invalid",
            )
        return OpGenerator().generate(req)


class _AlwaysInvalidGenerator:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def generate(self, req: GenerateOpReq) -> GenerateOpResp:
        sid = req.sub_task.task_id
        self.calls[sid] = self.calls.get(sid, 0) + 1
        return GenerateOpResp(
            op_type="sql",
            op_content="!!!",
            format_valid=False,
            error_msg="always bad",
        )


class _SelectiveFailGenerator:
    """First subtask always invalid; second always valid — batch continues."""

    def __init__(self, fail_id: str) -> None:
        self.fail_id = fail_id
        self.calls: dict[str, int] = {}

    def generate(self, req: GenerateOpReq) -> GenerateOpResp:
        sid = req.sub_task.task_id
        self.calls[sid] = self.calls.get(sid, 0) + 1
        if sid == self.fail_id:
            return GenerateOpResp(
                op_type="sql",
                op_content="bad",
                format_valid=False,
                error_msg="forced fail",
            )
        return OpGenerator().generate(req)


def _channel_state() -> Any:
    plan = PlanBuilder().build(
        PlanReq(
            dataset_id="ds-1",
            normalized_query="tag=channel_compare | query=渠道对比",
        ),
        question_tag="channel_compare",
    )
    state = create_initial_task_state(
        dataset_id="ds-1", user_query="渠道对比", task_id="t-gen"
    )
    state["intent"] = IntentParseResp(
        in_scope=True,
        question_tag="channel_compare",
        normalized_query="tag=channel_compare | query=渠道对比",
        constraints=[],
    )
    state["plan"] = plan
    return state


def test_happy_path_generates_valid_sql() -> None:
    node = NodeGenerateOp()
    delta = node.run(_channel_state())
    assert delta["next_node"] == "safety_check"
    assert delta["generated_ops"]
    assert delta["current_op"] is not None
    assert delta["current_op"].format_valid is True
    assert delta["current_op"].op_type == "sql"
    assert "SELECT" in delta["current_op"].op_content.upper()


def test_format_invalid_retries_then_succeeds() -> None:
    flaky = _FlakyGenerator()
    node = NodeGenerateOp(generator=flaky)
    delta = node.run(_channel_state())
    assert delta["next_node"] == "safety_check"
    # First pending task retried twice.
    first_id = delta["current_sub_task_id"]
    assert flaky.calls[first_id] == 2
    assert delta["current_op"].format_valid is True


def test_format_retry_exhausted_marks_failed_continues_batch() -> None:
    state = _channel_state()
    assert state["plan"] is not None
    batch_ids = [t.task_id for t in state["plan"].sub_tasks if t.parallel_group == 0]
    # Channel plan has one task in group 0; add a sibling in same group.
    extra = SubTask(
        task_id="st-extra",
        description="extra parallel agg",
        depends_on=[],
        expected_input={
            "question_tag": "channel_compare",
            "tables": ["orders"],
            "group_by": ["channel"],
        },
        expected_output={"columns": ["channel", "amount_sum"]},
        parallel_group=0,
    )
    state["plan"].sub_tasks.append(extra)
    fail_id = batch_ids[0]

    gen = _SelectiveFailGenerator(fail_id=fail_id)
    node = NodeGenerateOp(generator=gen, format_max_attempts=2)
    delta = node.run(state)

    failed = [r for r in delta["sub_task_results"] if r.status == "failed"]
    assert any(r.sub_task_id == fail_id for r in failed)
    assert gen.calls[fail_id] == 2  # retried then exhausted
    # Sibling still generated.
    assert "st-extra" in delta["generated_ops"]
    assert delta["generated_ops"]["st-extra"].format_valid is True
    assert delta["next_node"] == "safety_check"


def test_all_batch_invalid_goes_error_classify() -> None:
    node = NodeGenerateOp(generator=_AlwaysInvalidGenerator(), format_max_attempts=2)
    delta = node.run(_channel_state())
    assert delta["next_node"] == "error_classify"
    assert not delta["generated_ops"]
    assert any(r.status == "failed" for r in delta["sub_task_results"])
    # Two format attempts for the single group-0 task.
    assert sum(node._generator.calls.values()) == 2  # type: ignore[attr-defined]


def test_validate_op_format_helpers() -> None:
    ok, _ = validate_op_format("sql", "SELECT 1 AS n")
    assert ok is True
    bad, err = validate_op_format("sql", "DELETE FROM orders")
    assert bad is False
    assert err is not None
    ok_py, _ = validate_op_format("python", "x = 1\n")
    assert ok_py is True
    bad_py, _ = validate_op_format("python", "def (\n")
    assert bad_py is False


def test_return_rate_and_repurchase_templates() -> None:
    gen = OpGenerator()
    rr = gen.generate(
        GenerateOpReq(
            sub_task=SubTask(
                task_id="st-rate-topn",
                description="rate",
                expected_input={
                    "question_tag": "return_rate_topn",
                    "metric": "return_rate",
                    "constraints": ["top_n=5"],
                },
            ),
            constraints=["top_n=5"],
        )
    )
    assert rr.format_valid is True
    assert "LIMIT 5" in rr.op_content

    rp = gen.generate(
        GenerateOpReq(
            sub_task=SubTask(
                task_id="st-repurchase",
                description="repurchase",
                expected_input={"question_tag": "repurchase_rate"},
            )
        )
    )
    assert rp.format_valid is True
    assert rp.op_type == "python"


def test_channel_constraint_lands_in_sql() -> None:
    gen = OpGenerator()
    cons = ["channel IN ('APP', '官网')"]
    resp = gen.generate(
        GenerateOpReq(
            sub_task=SubTask(
                task_id="st-agg-channel",
                description="agg",
                expected_input={
                    "question_tag": "channel_compare",
                    "tables": ["orders"],
                    "group_by": ["channel"],
                },
            ),
            constraints=cons,
        )
    )
    assert resp.format_valid is True
    assert "channel IN ('APP', '官网')" in resp.op_content

    compare = gen.generate(
        GenerateOpReq(
            sub_task=SubTask(
                task_id="st-compare-channel",
                description="compare",
                depends_on=["st-agg-channel"],
                expected_input={
                    "question_tag": "channel_compare",
                    "from_sub_task": "st-agg-channel",
                    "reuse_result": True,
                },
                expected_output={
                    "columns": ["channel", "amount_sum", "order_count"]
                },
            ),
            constraints=cons,
        )
    )
    assert compare.format_valid is True
    assert "_reuse_placeholder" in compare.op_content


def test_template_sql_applies_exclude_and_channel_in() -> None:
    from data_reflex.modules.generate_op.generator import (
        OpGenerator,
        _sql_respects_constraints,
    )

    gen = OpGenerator()
    cons = ["channel IN ('官网')", "exclude=APP"]
    resp = gen.generate(
        GenerateOpReq(
            sub_task=SubTask(
                task_id="st-agg-channel",
                description="agg",
                expected_input={
                    "question_tag": "channel_compare",
                    "tables": ["orders"],
                    "group_by": ["channel"],
                },
                expected_output={
                    "columns": ["channel", "amount_sum", "order_count"]
                },
            ),
            constraints=cons,
        )
    )
    assert resp.format_valid is True
    assert "channel IN ('官网')" in resp.op_content
    assert "channel <> 'APP'" in resp.op_content
    assert _sql_respects_constraints(resp.op_content, cons) is True
    assert (
        _sql_respects_constraints(
            "SELECT channel, SUM(amount) FROM orders "
            "WHERE channel IN ('APP', '官网') GROUP BY channel",
            cons,
        )
        is False
    )
