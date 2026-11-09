# -*- coding: utf-8 -*-
"""计划校验：依赖环、输入完备、表范围；并给出图路由 hint。

上游：``Node_PlanValidate``。
本文件：失败则改写 plan 为 success=False；成功则按人审开关选下一跳。
下游路由：``human_confirm`` / ``generate_op`` / ``report``。

See ``tasks.md`` T22 / D-PLANVAL-01。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from data_reflex.models.schemas import PlanResp, SubTask
from data_reflex.modules.context.manager import QUESTION_TAGS
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

ALLOWED_TABLES: frozenset[str] = frozenset({"orders", "returns", "users"})

NextNode = Literal["report", "human_confirm", "generate_op"]


@dataclass(frozen=True)
class PlanValidateOutcome:
    """校验后的 plan + 建议 ``next_node``（给人审/生成/报告）。"""

    plan: PlanResp
    next_node: NextNode


class PlanValidator:
    """查环、缺输入、越权表引用；决定能否进入人审或生成。"""

    def validate(
        self,
        plan: PlanResp,
        *,
        enable_human_confirm: bool = True,
    ) -> PlanValidateOutcome:
        if not plan.success or not plan.sub_tasks:
            updated = plan.model_copy(
                update={
                    "success": False,
                    "error_msg": plan.error_msg or "plan is empty or already failed",
                }
            )
            return PlanValidateOutcome(plan=updated, next_node="report")

        tasks = list(plan.sub_tasks)
        task_ids = {t.task_id for t in tasks}

        cycle_detail = detect_cycle(tasks)
        missing = find_missing_inputs(tasks, task_ids)
        oos = find_out_of_scope(tasks)

        if cycle_detail is not None or missing or oos:
            all_missing = list(dict.fromkeys([*missing, *oos]))
            updated = plan.model_copy(
                update={
                    "success": False,
                    "has_cycle": cycle_detail is not None,
                    "cycle_detail": cycle_detail,
                    "missing_inputs": all_missing,
                    "error_msg": _error_message(cycle_detail, all_missing),
                }
            )
            logger.info(
                "plan validation failed",
                extra={
                    "task_id": "-",
                    "dataset_id": "-",
                    "has_cycle": updated.has_cycle,
                    "missing_count": len(all_missing),
                },
            )
            return PlanValidateOutcome(plan=updated, next_node="report")

        updated = plan.model_copy(
            update={
                "success": True,
                "has_cycle": False,
                "cycle_detail": None,
                "missing_inputs": [],
                "error_msg": None,
            }
        )
        next_node: NextNode = "human_confirm" if enable_human_confirm else "generate_op"
        return PlanValidateOutcome(plan=updated, next_node=next_node)


def detect_cycle(tasks: Sequence[SubTask]) -> str | None:
    """Return a cycle description if ``depends_on`` has a directed cycle."""
    graph: dict[str, list[str]] = {t.task_id: list(t.depends_on) for t in tasks}
    # Edges are dependency → task (dep must run before task). For cycle we
    # walk task → its depends_on (task depends on X means edge task→X in
    # "waits for" sense). Cycle in either orientation is the same undirected
    # ... actually A depends_on B, B depends_on A: graph A:[B], B:[A].
    white, gray, black = 0, 1, 2
    color: dict[str, int] = {tid: white for tid in graph}
    stack: list[str] = []

    def dfs(node: str) -> str | None:
        color[node] = gray
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in color:
                # Unknown id handled as missing_input, skip for cycle.
                continue
            if color[dep] == gray:
                # Found back edge: stack from dep to end + dep.
                if dep in stack:
                    idx = stack.index(dep)
                    cycle_path = stack[idx:] + [dep]
                    return " → ".join(cycle_path)
                return f"{node} → {dep}"
            if color[dep] == white:
                found = dfs(dep)
                if found:
                    return found
        stack.pop()
        color[node] = black
        return None

    for tid in graph:
        if color[tid] == white:
            detail = dfs(tid)
            if detail:
                return detail
    return None


def find_missing_inputs(tasks: Sequence[SubTask], task_ids: set[str]) -> list[str]:
    """Collect missing dependency / from_sub_task references."""
    missing: list[str] = []
    for task in tasks:
        for dep in task.depends_on:
            if dep not in task_ids:
                missing.append(f"{task.task_id}.depends_on:{dep}")
        from_st = task.expected_input.get("from_sub_task")
        if from_st is not None:
            from_id = str(from_st)
            if from_id not in task_ids:
                missing.append(f"{task.task_id}.expected_input.from_sub_task:{from_id}")
            elif from_id not in task.depends_on:
                missing.append(
                    f"{task.task_id}.from_sub_task_not_in_depends_on:{from_id}"
                )
        tables = task.expected_input.get("tables")
        if tables is not None and not isinstance(tables, list):
            missing.append(f"{task.task_id}.expected_input.tables:invalid_type")
        elif isinstance(tables, list) and len(tables) == 0:
            missing.append(f"{task.task_id}.expected_input.tables:empty")
    return missing


def find_out_of_scope(tasks: Sequence[SubTask]) -> list[str]:
    """Flag tables / tags outside the v1 three-table six-class contract."""
    issues: list[str] = []
    for task in tasks:
        tables = task.expected_input.get("tables")
        if isinstance(tables, list):
            for table in tables:
                name = str(table)
                if name not in ALLOWED_TABLES:
                    issues.append(f"{task.task_id}.table_out_of_scope:{name}")
        tag = task.expected_input.get("question_tag")
        if tag is not None and str(tag) not in QUESTION_TAGS:
            issues.append(f"{task.task_id}.question_tag_out_of_scope:{tag}")
    return issues


def _error_message(cycle_detail: str | None, missing: list[str]) -> str:
    parts: list[str] = []
    if cycle_detail:
        parts.append(f"cycle detected: {cycle_detail}")
    if missing:
        parts.append("missing_inputs: " + "; ".join(missing))
    return " | ".join(parts) if parts else "plan validation failed"
