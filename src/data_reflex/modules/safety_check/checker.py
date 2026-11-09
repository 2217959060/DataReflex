# -*- coding: utf-8 -*-
"""安全校验：SQL/Python 走 AST，禁止只靠关键字黑名单。

上游：``Node_SafetyCheck``（人审开关不能削弱本检查）。
本文件：拦截写操作、危险 Python、未授权列；``SELECT *`` 一律拒绝。
下游：通过 → Execute；失败 → Report（不给人审放行口）。

解析失败默认拒绝（``unparseable``），避免未知语句进沙箱。

See ``tasks.md`` T11 / D-SAFE-01。
"""

from __future__ import annotations

import ast
from typing import Any, ClassVar, cast

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from data_reflex.models.schemas import SafetyCheckReq, SafetyCheckResp
from data_reflex.utils.logger import get_logger, summarize_op

logger = get_logger(__name__)

# DDL/DML/admin nodes treated as dangerous writes (sqlglot expression types).
_SQL_WRITE_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Command,
    exp.Replace,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Merge,
)

# Builtins / names that must not be called in generated Python.
_DANGEROUS_CALL_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "breakpoint",
        "input",
        "exit",
        "quit",
    }
)

_DANGEROUS_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "pathlib",
        "importlib",
        "ctypes",
        "socket",
        "requests",
        "urllib",
        "http",
        "pickle",
        "shelve",
        "tempfile",
        "multiprocessing",
        "threading",
        "signal",
        "builtins",
    }
)


def _fail(
    violation_type: str,
    detail: str,
) -> SafetyCheckResp:
    return SafetyCheckResp(
        passed=False,
        violation_type=violation_type,  # type: ignore[arg-type]
        violation_detail=detail,
    )


def _ok() -> SafetyCheckResp:
    return SafetyCheckResp(passed=True, violation_type=None, violation_detail=None)


class SafetyChecker:
    """沙箱前最后一道 AST 门禁；不记录 op 原文到日志。"""

    sql_dialect: ClassVar[str] = "sqlite"

    def check(self, req: SafetyCheckReq) -> SafetyCheckResp:
        """Run the appropriate checker; never logs ``op_content`` 原文."""
        logger.info(
            "safety check start",
            extra={
                "task_id": "-",
                "dataset_id": "-",
                **summarize_op(req.op_type, req.op_content),
            },
        )
        if req.op_type == "sql":
            result = self._check_sql(req.op_content, req.allowed_columns)
        elif req.op_type == "python":
            result = self._check_python(req.op_content, req.allowed_columns)
        else:
            result = _fail("unparseable", f"unsupported op_type={req.op_type!r}")

        logger.info(
            "safety check done",
            extra={
                "task_id": "-",
                "dataset_id": "-",
                "passed": result.passed,
                "violation_type": result.violation_type or "-",
            },
        )
        return result

    # ------------------------------------------------------------------ SQL

    def _check_sql(self, content: str, allowed_columns: list[str]) -> SafetyCheckResp:
        text = content.strip()
        if not text:
            return _fail("unparseable", "empty SQL")

        try:
            statements = parse(text, read=self.sql_dialect)
        except ParseError as exc:
            return _fail("unparseable", f"SQL parse error: {exc}")
        except Exception as exc:  # noqa: BLE001 — treat any parse failure as block
            return _fail("unparseable", f"SQL parse error: {type(exc).__name__}: {exc}")

        if not statements or any(s is None for s in statements):
            return _fail("unparseable", "SQL produced no statements")

        for stmt in statements:
            if stmt is None:
                return _fail("unparseable", "SQL produced a null statement")
            expression = cast(Any, stmt)
            write_hit = self._find_sql_write(expression)
            if write_hit is not None:
                return _fail(
                    "dangerous_write",
                    f"dangerous SQL write detected: {write_hit}",
                )

            col_violation = self._check_sql_columns(expression, allowed_columns)
            if col_violation is not None:
                return col_violation

        return _ok()

    def _find_sql_write(self, node: Any) -> str | None:
        for child in node.walk():
            if isinstance(child, _SQL_WRITE_TYPES):
                return type(child).__name__
            # Some dialects surface truncate as a generic expression.
            name = type(child).__name__.lower()
            if "truncate" in name:
                return type(child).__name__
        return None

    def _check_sql_columns(
        self,
        stmt: Any,
        allowed_columns: list[str],
    ) -> SafetyCheckResp | None:
        # Empty whitelist: still block SELECT * (cannot prove column safety);
        # explicit named columns are allowed when no whitelist is provided so
        # unit tests can focus on write detection — see D-SAFE-01.
        # COUNT(*)/SUM(*) use Star under aggregates — not a column-star expand.
        has_select_star = any(
            not isinstance(star.parent, exp.AggFunc) for star in stmt.find_all(exp.Star)
        )
        if has_select_star:
            return _fail(
                "unauthorized_column",
                "SELECT * is forbidden; expand to an explicit column list "
                "within the field whitelist",
            )

        if not allowed_columns:
            return None

        allowed = {c.lower() for c in allowed_columns}
        # SELECT aliases (e.g. SUM(amount) AS amount_sum) are not table columns;
        # ORDER BY / HAVING may reference them — do not treat as unauthorized.
        select_aliases = {
            str(alias.alias).lower()
            for alias in stmt.find_all(exp.Alias)
            if alias.alias
        }
        referenced: set[str] = set()
        for col in stmt.find_all(exp.Column):
            # Use column name only (ignore table qualifier).
            name = col.name
            if name and name != "*":
                referenced.add(name.lower())

        unauthorized = sorted(referenced - allowed - select_aliases)
        if unauthorized:
            return _fail(
                "unauthorized_column",
                "unauthorized columns: " + ", ".join(unauthorized),
            )
        return None

    # --------------------------------------------------------------- Python

    def _check_python(
        self, content: str, allowed_columns: list[str]
    ) -> SafetyCheckResp:
        text = content.strip()
        if not text:
            return _fail("unparseable", "empty Python")

        try:
            tree = ast.parse(text, mode="exec")
        except SyntaxError as exc:
            return _fail("unparseable", f"Python parse error: {exc}")

        visitor = _PythonSafetyVisitor()
        visitor.visit(tree)
        if visitor.violation_type is not None:
            return _fail(
                visitor.violation_type,
                visitor.violation_detail or "dangerous Python detected",
            )

        # Optional: string literals embedding SELECT * (cannot expand whitelist).
        if allowed_columns:
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    normalized = " ".join(node.value.upper().split())
                    if "SELECT *" in normalized:
                        return _fail(
                            "unauthorized_column",
                            "Python embeds SELECT *; expand columns against whitelist",
                        )
        return _ok()


class _PythonSafetyVisitor(ast.NodeVisitor):
    """Collect first Python safety violation while walking the AST."""

    def __init__(self) -> None:
        self.violation_type: str | None = None
        self.violation_detail: str | None = None

    def _set(self, vtype: str, detail: str) -> None:
        if self.violation_type is None:
            self.violation_type = vtype
            self.violation_detail = detail

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in _DANGEROUS_MODULES:
                self._set(
                    "dangerous_write",
                    f"forbidden import: {alias.name}",
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = (node.module or "").split(".", 1)[0]
        if mod in _DANGEROUS_MODULES or node.module in _DANGEROUS_MODULES:
            self._set(
                "dangerous_write",
                f"forbidden import from: {node.module or '*'}",
            )
        # from x import * — dynamic surface
        if any(a.name == "*" for a in node.names):
            self._set("dangerous_write", "star-import is forbidden")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name in _DANGEROUS_CALL_NAMES:
            self._set("dangerous_write", f"forbidden call: {name}()")
        if name == "open" or name.endswith(".open"):
            mode = _open_mode_arg(node) or "r"
            # Any write/create/append or read-write ("+") mode is forbidden.
            if any(ch in mode for ch in ("w", "a", "x", "+")):
                self._set(
                    "dangerous_write",
                    f"file write open() mode={mode!r} is forbidden",
                )
        if name in {
            "os.system",
            "os.popen",
            "os.execv",
            "os.execve",
            "os.spawn",
            "os.remove",
            "os.unlink",
            "os.rmdir",
            "os.removedirs",
            "os.rename",
            "os.replace",
            "subprocess.run",
            "subprocess.call",
            "subprocess.Popen",
            "subprocess.check_output",
            "subprocess.check_call",
            "importlib.import_module",
            "importlib.__import__",
        }:
            self._set("dangerous_write", f"forbidden call: {name}()")
        # getattr(obj, dynamic) — any getattr is blocked per risk P0-2.
        if name == "getattr" or name.endswith(".getattr"):
            self._set("dangerous_write", "getattr() is forbidden")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # os.system as attribute load without Call still suspicious if later called;
        # Call visitor handles calls. Block __import__ attribute access patterns.
        if node.attr in {"system", "popen", "remove", "unlink"}:
            base = _call_name(node.value)
            if base in {"os", "subprocess"} or base.endswith(".os"):
                self._set(
                    "dangerous_write",
                    f"forbidden attribute access: {base}.{node.attr}",
                )
        self.generic_visit(node)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return ""


def _open_mode_arg(node: ast.Call) -> str | None:
    # open(path, mode) or open(path, mode=...)
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        val = node.args[1].value
        return str(val) if isinstance(val, str) else None
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            val = kw.value.value
            return str(val) if isinstance(val, str) else None
    return None
