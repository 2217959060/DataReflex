# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from data_reflex.models.schemas import SafetyCheckReq
from data_reflex.modules.safety_check.checker import SafetyChecker

ALLOWED = ["order_id", "user_id", "amount", "channel", "product_name"]


@pytest.fixture()
def checker() -> SafetyChecker:
    return SafetyChecker()


def test_safe_select_passes(checker: SafetyChecker) -> None:
    resp = checker.check(
        SafetyCheckReq(
            op_type="sql",
            op_content="SELECT order_id, amount FROM orders WHERE channel = 'APP'",
            allowed_columns=ALLOWED,
        )
    )
    assert resp.passed is True
    assert resp.violation_type is None


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders(order_id) VALUES ('x')",
        "UPDATE orders SET amount = 0",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "ALTER TABLE orders ADD COLUMN x INT",
        "CREATE TABLE hack(a INT)",
        "TRUNCATE TABLE orders",
        "/* bypass */ DeLeTe FrOm orders",
        "SeLeCt 1; DROP TABLE orders",
        "-- comment\nDROP TABLE orders",
    ],
)
def test_sql_writes_blocked_despite_case_and_comments(
    checker: SafetyChecker, sql: str
) -> None:
    resp = checker.check(
        SafetyCheckReq(op_type="sql", op_content=sql, allowed_columns=ALLOWED)
    )
    assert resp.passed is False
    assert resp.violation_type == "dangerous_write"
    assert resp.violation_detail


def test_select_star_triggers_whitelist(checker: SafetyChecker) -> None:
    resp = checker.check(
        SafetyCheckReq(
            op_type="sql",
            op_content="SELECT * FROM orders",
            allowed_columns=ALLOWED,
        )
    )
    assert resp.passed is False
    assert resp.violation_type == "unauthorized_column"
    assert "SELECT *" in (resp.violation_detail or "")


def test_count_star_and_select_alias_allowed(checker: SafetyChecker) -> None:
    """COUNT(*) / ORDER BY alias must not be treated as SELECT * / unauthorized."""
    resp = checker.check(
        SafetyCheckReq(
            op_type="sql",
            op_content=(
                "SELECT channel, SUM(amount) AS amount_sum, COUNT(*) AS order_count "
                "FROM orders GROUP BY channel ORDER BY amount_sum DESC"
            ),
            allowed_columns=ALLOWED,
        )
    )
    assert resp.passed is True


def test_unauthorized_column_blocked(checker: SafetyChecker) -> None:
    resp = checker.check(
        SafetyCheckReq(
            op_type="sql",
            op_content="SELECT order_id, phone FROM orders",
            allowed_columns=ALLOWED,
        )
    )
    assert resp.passed is False
    assert resp.violation_type == "unauthorized_column"
    assert "phone" in (resp.violation_detail or "")


def test_unparseable_sql(checker: SafetyChecker) -> None:
    resp = checker.check(
        SafetyCheckReq(
            op_type="sql",
            op_content="SELECT FROM WHERE",
            allowed_columns=ALLOWED,
        )
    )
    assert resp.passed is False
    assert resp.violation_type == "unparseable"


def test_safe_python_passes(checker: SafetyChecker) -> None:
    code = (
        "import pandas as pd\n"
        "df = pd.DataFrame({'order_id':[1], 'amount':[2.0]})\n"
        "result = df.groupby('order_id')['amount'].sum()\n"
    )
    resp = checker.check(
        SafetyCheckReq(op_type="python", op_content=code, allowed_columns=ALLOWED)
    )
    assert resp.passed is True


@pytest.mark.parametrize(
    ("code", "needle"),
    [
        ("eval('1+1')", "eval"),
        ("exec('x=1')", "exec"),
        ("__import__('os').system('id')", "__import__"),
        ("getattr(obj, 'x')", "getattr"),
        ("import os\nos.system('echo hi')", "os"),
        ("import subprocess\nsubprocess.run(['ls'])", "subprocess"),
        ("open('out.txt', 'w').write('x')", "open"),
        ("from importlib import import_module\nimport_module('os')", "import"),
    ],
)
def test_dangerous_python_blocked(
    checker: SafetyChecker, code: str, needle: str
) -> None:
    resp = checker.check(
        SafetyCheckReq(op_type="python", op_content=code, allowed_columns=ALLOWED)
    )
    assert resp.passed is False
    assert resp.violation_type == "dangerous_write"
    assert needle.lower() in (resp.violation_detail or "").lower()


def test_python_syntax_error_unparseable(checker: SafetyChecker) -> None:
    resp = checker.check(
        SafetyCheckReq(
            op_type="python",
            op_content="def broken(",
            allowed_columns=ALLOWED,
        )
    )
    assert resp.passed is False
    assert resp.violation_type == "unparseable"


def test_dynamic_concat_write_sql_still_blocked(checker: SafetyChecker) -> None:
    """Final op_content is a complete write statement (as emitted after concat)."""
    # Simulates generator concatenating fragments into one statement string.
    part_a = "IN"
    part_b = "SERT INTO orders(order_id) VALUES ('1')"
    sql = part_a + part_b
    resp = checker.check(
        SafetyCheckReq(op_type="sql", op_content=sql, allowed_columns=ALLOWED)
    )
    assert resp.passed is False
    assert resp.violation_type == "dangerous_write"
