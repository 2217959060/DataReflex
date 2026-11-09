# -*- coding: utf-8 -*-
from __future__ import annotations

from data_reflex.models.schemas import SubTaskResult
from data_reflex.modules.report.conclusion import (
    draft_conclusion,
    polish_preserves_numbers,
)


def _rows(sub_id: str, rows: list[dict[str, object]]) -> SubTaskResult:
    return SubTaskResult(
        sub_task_id=sub_id,
        status="success",
        result_rows=rows,
        result_columns=list(rows[0].keys()) if rows else [],
        duration_ms=1,
    )


def test_channel_compare_draft() -> None:
    text = draft_conclusion(
        user_query="对比 APP 和官网销售额",
        question_tag="channel_compare",
        reportable=[
            _rows(
                "st-compare-channel",
                [
                    {"channel": "APP", "amount_sum": 1200},
                    {"channel": "官网", "amount_sum": 800},
                ],
            )
        ],
    )
    assert text is not None
    assert "APP" in text
    assert "1200" in text
    assert "官网" in text


def test_return_rate_topn_draft() -> None:
    text = draft_conclusion(
        user_query="退货率最高的商品 Top3",
        question_tag="return_rate_topn",
        reportable=[
            _rows(
                "st-topn-rate",
                [
                    {"product_name": "耳机", "return_rate": 0.25},
                    {"product_name": "键盘", "return_rate": 0.1},
                ],
            )
        ],
    )
    assert text is not None
    assert "耳机" in text
    assert "25%" in text


def test_repurchase_draft() -> None:
    text = draft_conclusion(
        user_query="复购率",
        question_tag="repurchase_rate",
        reportable=[
            _rows(
                "st-repurchase",
                [
                    {
                        "repurchase_rate": 0.3333,
                        "repeat_users": 10,
                        "total_users": 30,
                    }
                ],
            )
        ],
    )
    assert text is not None
    assert "33.3%" in text
    assert "10" in text
    assert "30" in text


def test_polish_preserves_numbers() -> None:
    draft = "「APP」销售额 1200，高于「官网」的 800，差额约 400。"
    ok = "APP 渠道销售额达到 1200，官网为 800，两者差额约 400。"
    bad = "APP 渠道销售额达到 1500，官网为 800。"
    assert polish_preserves_numbers(draft, ok) is True
    assert polish_preserves_numbers(draft, bad) is False


def test_polish_allows_rate_percent_rewrite() -> None:
    draft = "复购率约 0.3333（复购用户 10 / 下单用户 30）。"
    polished = "全量用户复购率约 33.3%（复购用户 10 / 下单用户 30）。"
    assert polish_preserves_numbers(draft, polished) is True
