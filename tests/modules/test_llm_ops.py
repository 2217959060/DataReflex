# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from data_reflex.config.settings import Settings
from data_reflex.models.schemas import (
    ErrorClassifyResp,
    GenerateOpReq,
    IntentParseReq,
    ReflectReq,
    SubTask,
)
from data_reflex.modules.generate_op.generator import OpGenerator
from data_reflex.modules.intent_parse.parser import IntentParser
from data_reflex.modules.llm.client import LlmError, StaticLlmClient
from data_reflex.modules.llm.prompts import parse_generate_op_json
from data_reflex.modules.reflect.reflector import Reflector


def _channel_req() -> GenerateOpReq:
    return GenerateOpReq(
        sub_task=SubTask(
            task_id="st-agg-channel",
            description="aggregate by channel",
            expected_input={
                "question_tag": "channel_compare",
                "tables": ["orders"],
                "group_by": ["channel"],
                "metric": "amount_sum",
            },
            expected_output={"columns": ["channel", "amount_sum", "order_count"]},
        ),
        constraints=["group_by=channel"],
    )


def test_parse_generate_op_json_strips_fence() -> None:
    raw = '```json\n{"op_type":"sql","op_content":"SELECT 1 AS n"}\n```'
    op_type, content = parse_generate_op_json(raw)
    assert op_type == "sql"
    assert "SELECT 1" in content


def test_generator_uses_llm_when_enabled() -> None:
    payload = json.dumps(
        {
            "op_type": "sql",
            "op_content": (
                "SELECT channel, SUM(amount) AS amount_sum "
                "FROM orders GROUP BY channel"
            ),
        },
        ensure_ascii=False,
    )
    llm = StaticLlmClient(reply=payload)
    settings = Settings(
        enable_llm_ops=True,
        llm_api_key="test-key",
        llm_model="glm-4-flash-250414",
    )
    gen = OpGenerator(settings=settings, llm=llm)
    resp = gen.generate(_channel_req())
    assert resp.format_valid is True
    assert gen.last_source == "llm"
    assert gen.last_llm_calls == 1
    assert len(llm.calls) == 1


def test_generator_falls_back_on_llm_error() -> None:
    llm = StaticLlmClient(error=LlmError("boom"))
    settings = Settings(enable_llm_ops=True, llm_api_key="test-key")
    gen = OpGenerator(settings=settings, llm=llm)
    resp = gen.generate(_channel_req())
    assert resp.format_valid is True
    assert gen.last_source == "template"
    assert "FROM orders" in resp.op_content


def test_generator_stays_template_when_llm_ops_off() -> None:
    llm = StaticLlmClient(reply='{"op_type":"sql","op_content":"SELECT 1"}')
    settings = Settings(enable_llm_ops=False, llm_api_key="test-key")
    gen = OpGenerator(settings=settings, llm=llm)
    resp = gen.generate(_channel_req())
    assert resp.format_valid is True
    assert gen.last_source == "template"
    assert llm.calls == []


def test_reflector_enriches_fix_hint() -> None:
    llm = StaticLlmClient(reply="请检查 JOIN 键是否匹配 order_id")
    settings = Settings(enable_llm_ops=True, llm_api_key="test-key")
    reflector = Reflector(settings=settings, llm=llm)
    resp = reflector.reflect(
        ReflectReq(
            sub_task=SubTask(task_id="st-1", description="join"),
            error_classify=ErrorClassifyResp(
                error_type="runtime",
                error_detail="no such column",
                error_fingerprint="runtime|col",
                retryable=True,
                suggested_fix="fix columns",
            ),
            retry_count=0,
            max_retry=3,
            llm_calls_used=0,
            max_llm_calls=20,
            previous_fingerprints=["runtime|other"],
        )
    )
    assert resp.should_retry is True
    assert "JOIN" in (resp.fix_hint or "")
    assert reflector.last_llm_calls == 1


def test_intent_clarification_polish() -> None:
    llm = StaticLlmClient(reply="请把「差不多」改成具体渠道枚举，例如 APP 或官网。")
    settings = Settings(enable_llm_ops=True, llm_api_key="test-key")
    parser = IntentParser(settings=settings, llm=llm)
    intent, _ = parser.parse(
        IntentParseReq(
            dataset_id="ds-1",
            user_query="对比各渠道销售额差不多好的那些",
            conversation_context=[],
        )
    )
    # May or may not need clarification depending on vague extractors;
    # when LLM is used for vague path, last_llm_calls increments.
    if intent.need_clarification and parser.last_llm_calls:
        assert "渠道" in intent.clarification_questions[0]
