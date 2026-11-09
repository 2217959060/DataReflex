# -*- coding: utf-8 -*-
"""LangGraph 运行时状态：``TaskState`` + 路由/暂存键（T07 冻结字段之外）。

``builder`` 条件边主要读 ``next_node``；各节点往本结构写增量。
``fix_hint`` 供 GenerateOp / 自愈使用；正式任务字段仍以 ``models.state.TaskState`` 为准。
"""

from __future__ import annotations

from typing import Any, NotRequired

from data_reflex.models.schemas import (
    ErrorClassifyResp,
    ExecuteResp,
    GenerateOpResp,
    ReflectResp,
    ResultValidateResp,
    SafetyCheckResp,
    TableSchema,
)
from data_reflex.models.state import TaskState


class GraphState(TaskState, total=False):
    """在 TaskState 上叠加边路由与节点暂存字段。"""

    next_node: NotRequired[str]  # 条件边跳转目标
    current_op: NotRequired[GenerateOpResp | None]
    generated_ops: NotRequired[dict[str, GenerateOpResp]]
    execute_resp: NotRequired[ExecuteResp | None]
    safety_check: NotRequired[SafetyCheckResp | None]
    error_classify: NotRequired[ErrorClassifyResp | None]
    reflect: NotRequired[ReflectResp | None]
    result_validate: NotRequired[ResultValidateResp | None]
    fix_hint: NotRequired[str | None]  # 自愈提示，给下一轮 GenerateOp
    model_unavailable: NotRequired[bool]
    structured_constraints: NotRequired[list[dict[str, Any]]]
    chart_requests: NotRequired[list[dict[str, Any]]]
    failures: NotRequired[list[dict[str, Any]]]
    table_schemas: NotRequired[list[TableSchema]]
    allowed_columns: NotRequired[list[str]]
    failed_node: NotRequired[str]
