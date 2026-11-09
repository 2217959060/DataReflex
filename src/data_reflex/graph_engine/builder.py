# -*- coding: utf-8 -*-
"""组装 DataReflex 主工作流：一张 LangGraph 状态机（T32 / ADR-001）。

上游：API 队列 ``api/queue.py`` 提交任务并 ``invoke/stream`` 本图。
本文件：注册节点 + 条件边 + Checkpointer（可暂停/续跑）。
下游：各 ``graph_engine/nodes/*`` 写 ``next_node``，边函数据此跳转。

成功主路径：
  START → intent_parse → plan → plan_validate → human_confirm
        → generate_op → safety_check → execute → result_validate
        → report → END

常见旁路：
  - 超范围/需澄清：intent_parse → report
  - 关人审或已确认：plan_validate 可直达 generate_op
  - 执行失败自愈：execute → error_classify → reflect → generate_op（回环）
  - 多子任务：result_validate 后若还有 pending → 再 generate_op
  - 降级：report → fallback_report → END

阅读顺序：本文件（边）→ 各 Node → 对应 ``modules/*`` 业务实现。

See ``tasks.md`` T32 / D-GRAPH-01.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from data_reflex.graph_engine.nodes.error_classify import NodeErrorClassify
from data_reflex.graph_engine.nodes.execute import NodeExecute
from data_reflex.graph_engine.nodes.fallback_report import NodeFallbackReport
from data_reflex.graph_engine.nodes.generate_op import NodeGenerateOp
from data_reflex.graph_engine.nodes.human_confirm import NodeHumanConfirmPlan
from data_reflex.graph_engine.nodes.intent_parse import NodeIntentParse
from data_reflex.graph_engine.nodes.plan import NodePlan
from data_reflex.graph_engine.nodes.plan_validate import NodePlanValidate
from data_reflex.graph_engine.nodes.reflect import NodeReflect
from data_reflex.graph_engine.nodes.report import NodeReport
from data_reflex.graph_engine.nodes.result_validate import NodeResultValidate
from data_reflex.graph_engine.nodes.safety_check import NodeSafetyCheck
from data_reflex.graph_engine.state import GraphState
from data_reflex.models.schemas import PlanResp, SubTaskResult

Route = str

# 子任务已终态：不再因「还有 pending」而回到 generate_op
_DONE_STATUSES = frozenset({"success", "failed", "terminated"})


@dataclass
class MainGraphNodes:
    """可注入的节点集合；测试可替换 Mock 执行器 / 确认门等。"""

    intent_parse: NodeIntentParse
    plan: NodePlan
    plan_validate: NodePlanValidate
    human_confirm: NodeHumanConfirmPlan
    generate_op: NodeGenerateOp
    safety_check: NodeSafetyCheck
    execute: NodeExecute
    result_validate: NodeResultValidate
    error_classify: NodeErrorClassify
    reflect: NodeReflect
    report: NodeReport
    fallback_report: NodeFallbackReport


def default_nodes(**overrides: Any) -> MainGraphNodes:
    """生产默认节点；``overrides`` 按字段名替换单个节点（供单测）。"""
    base = MainGraphNodes(
        intent_parse=NodeIntentParse(),
        plan=NodePlan(),
        plan_validate=NodePlanValidate(),
        human_confirm=NodeHumanConfirmPlan(),
        generate_op=NodeGenerateOp(),
        safety_check=NodeSafetyCheck(),
        execute=NodeExecute(),
        result_validate=NodeResultValidate(),
        error_classify=NodeErrorClassify(),
        reflect=NodeReflect(),
        report=NodeReport(),
        fallback_report=NodeFallbackReport(),
    )
    for key, value in overrides.items():
        if not hasattr(base, key):
            raise TypeError(f"unknown node override: {key}")
        setattr(base, key, value)
    return base


@contextmanager
def open_sqlite_checkpointer(conn_string: str = ":memory:") -> Iterator[SqliteSaver]:
    """打开 SQLite Checkpointer（人工确认挂起后续跑靠它）。"""
    with SqliteSaver.from_conn_string(conn_string) as saver:
        saver.setup()
        yield saver


def build_main_graph(
    *,
    nodes: MainGraphNodes | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> Any:
    """编译主图：注册 12 个节点 + 条件边，返回可 invoke 的 CompiledGraph。

    ``checkpointer`` 默认内存版；生产应由调用方传入 SqliteSaver。
    节点通过写状态字段 ``next_node`` 表达意图，边函数再映射到真实跳转。
    """
    bundle = nodes or default_nodes()
    cp: BaseCheckpointSaver[Any] = checkpointer or MemorySaver()

    # LangGraph's add_node overloads are stricter than our BaseNode wrappers;
    # runtime wiring is covered by test_builder_main.
    graph = StateGraph(GraphState)

    # --- 节点注册（名字 = 边上用的字符串 id）---
    graph.add_node("intent_parse", _as_runnable(bundle.intent_parse))  # type: ignore[call-overload]
    graph.add_node("plan", _as_runnable(bundle.plan))  # type: ignore[call-overload]
    graph.add_node("plan_validate", _as_runnable(bundle.plan_validate))  # type: ignore[call-overload]
    graph.add_node("human_confirm", _as_runnable(bundle.human_confirm))  # type: ignore[call-overload]
    graph.add_node("generate_op", _as_runnable(bundle.generate_op))  # type: ignore[call-overload]
    graph.add_node("safety_check", _as_runnable(bundle.safety_check))  # type: ignore[call-overload]
    graph.add_node("execute", _as_runnable(bundle.execute))  # type: ignore[call-overload]
    graph.add_node("result_validate", _as_runnable(bundle.result_validate))  # type: ignore[call-overload]
    graph.add_node("error_classify", _as_runnable(bundle.error_classify))  # type: ignore[call-overload]
    graph.add_node("reflect", _as_runnable(bundle.reflect))  # type: ignore[call-overload]
    graph.add_node("report", _as_runnable(bundle.report))  # type: ignore[call-overload]
    graph.add_node("fallback_report", _as_runnable(bundle.fallback_report))  # type: ignore[call-overload]

    # --- 边：固定边用 add_edge；分叉用 conditional_edges ---
    graph.add_edge(START, "intent_parse")
    # 范围内且无需澄清 → plan；否则直接出报告（拒绝/澄清问句）
    graph.add_conditional_edges(
        "intent_parse",
        _route_intent,
        {"plan": "plan", "report": "report"},
    )
    graph.add_edge("plan", "plan_validate")
    # 人审开着 → human_confirm；关掉或无需确认 → generate_op；计划非法 → report
    graph.add_conditional_edges(
        "plan_validate",
        _route_next(
            "human_confirm",
            "generate_op",
            "report",
            default="report",
        ),
        {
            "human_confirm": "human_confirm",
            "generate_op": "generate_op",
            "report": "report",
        },
    )
    # waiting_confirm → end（挂起等 API 恢复）；通过 → generate_op；拒绝 → report
    graph.add_conditional_edges(
        "human_confirm",
        _route_human_confirm,
        {"generate_op": "generate_op", "report": "report", "end": END},
    )
    graph.add_conditional_edges(
        "generate_op",
        _route_next("safety_check", "error_classify", "report", default="report"),
        {
            "safety_check": "safety_check",
            "error_classify": "error_classify",
            "report": "report",
        },
    )
    graph.add_conditional_edges(
        "safety_check",
        _route_next("execute", "report", default="report"),
        {"execute": "execute", "report": "report"},
    )
    # 成功 → result_validate；失败 → error_classify（进入自愈链）
    graph.add_conditional_edges(
        "execute",
        _route_next("result_validate", "error_classify", "report", default="report"),
        {
            "result_validate": "result_validate",
            "error_classify": "error_classify",
            "report": "report",
        },
    )
    # 还有未完成子任务时回到 generate_op，否则去 report
    graph.add_conditional_edges(
        "result_validate",
        _route_after_result_validate,
        {"generate_op": "generate_op", "report": "report"},
    )
    # 自愈入口：可修 → reflect；否则 report
    graph.add_conditional_edges(
        "error_classify",
        _route_next("reflect", "report", default="report"),
        {"reflect": "reflect", "report": "report"},
    )
    # 自愈回环：should_retry → generate_op；熔断 → report
    graph.add_conditional_edges(
        "reflect",
        _route_next("generate_op", "report", default="report"),
        {"generate_op": "generate_op", "report": "report"},
    )
    graph.add_conditional_edges(
        "report",
        _route_next("fallback_report", "end", default="end"),
        {"fallback_report": "fallback_report", "end": END},
    )
    graph.add_edge("fallback_report", END)

    return graph.compile(checkpointer=cp)


def _as_runnable(node: Any) -> Callable[[GraphState], dict[str, Any]]:
    """把 BaseNode 包成 LangGraph 可调用函数（入状态，出状态增量）。"""

    def _run(state: GraphState) -> dict[str, Any]:
        result: dict[str, Any] = node.run(state)
        return result

    _run.__name__ = getattr(node, "name", type(node).__name__)
    return _run


def _route_next(*allowed: str, default: str = "report") -> Callable[[GraphState], str]:
    """通用路由：读 ``state['next_node']``，不在允许集合则走 default。"""
    allowed_set = set(allowed)

    def _route(state: GraphState) -> str:
        raw = state.get("next_node")
        if isinstance(raw, str) and raw in allowed_set:
            return raw
        if "end" in allowed_set and raw in {None, "", "end"}:
            return "end"
        return default

    return _route


def _route_intent(state: GraphState) -> Literal["plan", "report"]:
    """意图后分流：显式 next_node 优先；否则看 in_scope / need_clarification。"""
    raw = state.get("next_node")
    if raw == "plan":
        return "plan"
    if raw == "report":
        return "report"
    intent = state.get("intent")
    if intent is None:
        return "report"
    in_scope = getattr(intent, "in_scope", None)
    need_clarification = getattr(intent, "need_clarification", None)
    if isinstance(intent, Mapping):
        in_scope = intent.get("in_scope")
        need_clarification = intent.get("need_clarification")
    if in_scope and not need_clarification:
        return "plan"
    return "report"


def _route_human_confirm(state: GraphState) -> Literal["generate_op", "report", "end"]:
    """人审分流：挂起优先于残留的 next_node（PlanValidate 可能留下 human_confirm）。"""
    # Prefer interrupt over leftover next_node from PlanValidate ("human_confirm").
    if state.get("status") == "waiting_confirm":
        return "end"
    raw = state.get("next_node")
    if raw == "generate_op":
        return "generate_op"
    if raw == "report":
        return "report"
    return "report"


def _route_after_result_validate(state: GraphState) -> Literal["generate_op", "report"]:
    """校验后：还有子任务未做完则继续生成，否则收束到报告。"""
    raw = state.get("next_node")
    if raw == "generate_op":
        return "generate_op"
    # Successful validation: continue remaining sub-tasks before final report.
    if raw == "report" and _has_pending_subtasks(state):
        return "generate_op"
    if raw == "report":
        return "report"
    if _has_pending_subtasks(state):
        return "generate_op"
    return "report"


def _has_pending_subtasks(state: GraphState) -> bool:
    """计划中是否仍有未进入终态的子任务。"""
    plan_raw = state.get("plan")
    if plan_raw is None:
        return False
    plan = (
        plan_raw
        if isinstance(plan_raw, PlanResp)
        else PlanResp.model_validate(plan_raw)
    )
    if not plan.sub_tasks:
        return False
    results = list(state.get("sub_task_results") or [])
    done = {
        r.sub_task_id
        for r in results
        if isinstance(r, SubTaskResult) and r.status in _DONE_STATUSES
    }
    return any(t.task_id not in done for t in plan.sub_tasks)


def make_thread_config(task_id: str) -> dict[str, Any]:
    """LangGraph 线程配置：用 task_id 当 thread_id，与 Checkpointer 对齐。"""
    return {"configurable": {"thread_id": task_id}}
