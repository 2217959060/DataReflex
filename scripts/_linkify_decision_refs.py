# -*- coding: utf-8 -*-
"""Rewrite 依据 / 见 D-xxx lines to Markdown jump links."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# Exact old → new replacements for tasks.md 依据 lines (inner text after **依据：**).
TASKS_YIJU: dict[str, str] = {
    "`conventions.md` 编码头要求；配置在 `pyproject.toml` `[tool.ruff.lint]`。": (
        "[conventions.md §1.1](conventions.md#11-运行环境) 编码头要求；"
        "配置在 [`pyproject.toml`](../pyproject.toml) `[tool.ruff.lint]`；"
        "[T03](tasks.md#T03)。"
    ),
    "T03 mypy strict 验收；T10 引入 pandas/openpyxl；与既有第三方 ignore 列表同一策略。": (
        "[T03](tasks.md#T03) mypy strict 验收；[T10](tasks.md#T10) 引入 pandas/openpyxl；"
        "与既有第三方 ignore 列表同一策略。"
    ),
    "T04/T06 验收语义；`defaults.md` 为默认值唯一来源。": (
        "[T04](tasks.md#T04)/[T06](tasks.md#T06) 验收语义；"
        "[defaults.md](defaults.md) 为默认值唯一来源（[D-DEFAULTS-01](defaults.md#D-DEFAULTS-01)）。"
    ),
    "`conventions.md` §5、§1.3；`risk.md` P1-5；验收条文明确点名 `op_content` 原文不得出现。": (
        "[conventions.md §5](conventions.md#5-日志规范)、"
        "[§1.3](conventions.md#13-生成代码与隔离执行约束)；"
        "[risk.md P1-5](risk.md#risk-p1)；"
        "[T08](tasks.md#T08) 验收点名 `op_content` 原文不得出现。"
    ),
    "`design.md` ADR-007 / ADR-016；`risk.md` P0-9；T09 验收「并发写无状态错乱」。": (
        "[ADR-007](design.md#ADR-007) / [ADR-016](design.md#ADR-016)；"
        "[risk.md P0-9](risk.md#risk-p0)；"
        "[T09](tasks.md#T09) 验收「并发写无状态错乱」。"
    ),
    "`spec.md` FR-01；`risk.md` P1-4 / P2-9；`schema.md` §5 Join；`defaults.md` 限额与敏感字段。": (
        "[spec.md FR-01](spec.md#fr-01-数据文件导入与结构识别)；"
        "[risk.md P1-4](risk.md#risk-p1) / [P2-9](risk.md#risk-p2)；"
        "[schema.md §5](schema.md#5-join-键与提示) Join；"
        "[defaults.md](defaults.md) 限额与敏感字段。"
    ),
    "`risk.md` P0-2；`design.md` §3.6 `violation_type` 三值；T11 验收点名注释/大小写/拼接/`SELECT *`/危险 Python。": (
        "[risk.md P0-2](risk.md#risk-p0)；"
        "[design.md §3.6](design.md#36-安全校验模块) `violation_type` 三值；"
        "[T11](tasks.md#T11) 验收点名注释/大小写/拼接/`SELECT *`/危险 Python。"
    ),
    "`design.md` §3.7；`risk.md` P0-1 / ADR-003；T12 验收四种结果；T13 将复用同一 Protocol。": (
        "[design.md §3.7](design.md#37-隔离执行器)；"
        "[risk.md P0-1](risk.md#risk-p0) / [ADR-003](design.md#ADR-003)；"
        "[T12](tasks.md#T12) 验收四种结果；[T13](tasks.md#T13) 将复用同一 Protocol。"
    ),
    "`risk.md` P0-1/P0-4/P1-7；`defaults.md` §5；D-EXEC-01 Protocol；T13 验收条文。": (
        "[risk.md P0-1/P0-4](risk.md#risk-p0) / [P1-7](risk.md#risk-p1)；"
        "[defaults.md §5](defaults.md#5-沙箱镜像)；"
        "[D-EXEC-01](tasks.md#D-EXEC-01) Protocol；[T13](tasks.md#T13) 验收条文。"
    ),
    "`design.md` §3.8；`risk.md` 自愈熔断摘要；T26「资源超限直接标记失败」；T14 验收「同错同指纹 / 异错异指纹」。": (
        "[design.md §3.8](design.md#38-错误分类与自愈模块)；"
        "[risk.md 自愈熔断摘要](risk.md#risk-summary)；"
        "[T26](tasks.md#T26)「资源超限直接标记失败」；"
        "[T14](tasks.md#T14) 验收「同错同指纹 / 异错异指纹」。"
    ),
    "`design.md` §3.9 anomaly 四值；`risk.md` P0-3/P1-3；`spec.md` FR 结果校验；T15 验收点名四类异常 + 合法空。": (
        "[design.md §3.9](design.md#39-结果校验与报告模块) anomaly 四值；"
        "[risk.md P0-3](risk.md#risk-p0) / [P1-3](risk.md#risk-p1)；"
        "[spec.md FR-07](spec.md#fr-07-结果合理性与安全校验)；"
        "[T15](tasks.md#T15) 验收点名四类异常 + 合法空。"
    ),
    "`design.md` §3.9 ReportReq/Resp + D-MODEL-02；`defaults.md` §6；`risk.md` P2-10 / P0-7；T16 验收点名 partial_success、替代说明、空数据。": (
        "[design.md §3.9](design.md#39-结果校验与报告模块) ReportReq/Resp + "
        "[D-MODEL-02](design.md#D-MODEL-02)；"
        "[defaults.md §6](defaults.md#6-前端图表)；"
        "[risk.md P2-10](risk.md#risk-p2) / [P0-7](risk.md#risk-p0)；"
        "[T16](tasks.md#T16) 验收点名 partial_success、替代说明、空数据。"
    ),
    "`design.md` ADR-004 / ADR-014；`risk.md` P2-1；`handoff` Langfuse 可 no-op；T17 验收；依赖 `langfuse>=2.50` 实装为 5.x。": (
        "[ADR-004](design.md#ADR-004) / [ADR-014](design.md#ADR-014)；"
        "[risk.md P2-1](risk.md#risk-p2)；"
        "[handoff 完成定义](handoff.md#完成定义已锁定) Langfuse 可 no-op；"
        "[T17](tasks.md#T17) 验收；依赖 `langfuse>=2.50` 实装为 5.x。"
    ),
    "`defaults.md` §3；`design.md` §3.2 / ADR-018；`risk.md` P1-1；`schema.md` §7；T18 验收。": (
        "[defaults.md §3](defaults.md#3-新话题规则-new_topic_rule)；"
        "[design.md §3.2](design.md#32-上下文管理模块) / "
        "[ADR-018](design.md#ADR-018)；"
        "[risk.md P1-1](risk.md#risk-p1)；"
        "[schema.md §7](schema.md#7-六类问题与字段依赖)；"
        "[T18](tasks.md#T18) 验收。"
    ),
    "`risk.md` P0-5 / P1-5；`design.md` §4 Node_Audit；`conventions.md` §4.2；T19 验收。": (
        "[risk.md P0-5](risk.md#risk-p0) / [P1-5](risk.md#risk-p1)；"
        "[design.md §4](design.md#4-graph-节点设计) Node_Audit；"
        "[conventions.md §4.2](conventions.md#42-禁止裸-except)；"
        "[T19](tasks.md#T19) 验收。"
    ),
    "`schema.md` §7–§8；`design.md` §3.3；`risk.md` P1-3；D-CTX-01；T20 验收。": (
        "[schema.md §7](schema.md#7-六类问题与字段依赖)–"
        "[§8](schema.md#8-超范围判定)；"
        "[design.md §3.3](design.md#33-意图解析模块)；"
        "[risk.md P1-3](risk.md#risk-p1)；"
        "[D-CTX-01](tasks.md#D-CTX-01)；[T20](tasks.md#T20) 验收。"
    ),
    "`design.md` §3.4 / ADR-002；`schema.md` §7；T21 验收；D-INTENT-01 无 LLM 策略一致。": (
        "[design.md §3.4](design.md#34-任务规划模块) / "
        "[ADR-002](design.md#ADR-002)；"
        "[schema.md §7](schema.md#7-六类问题与字段依赖)；"
        "[T21](tasks.md#T21) 验收；"
        "[D-INTENT-01](tasks.md#D-INTENT-01) 无 LLM 策略一致。"
    ),
    "`design.md` §3.4 / §4 条件边；`schema.md` 三表；T22 验收；D-PLAN-01 模板依赖边。": (
        "[design.md §3.4](design.md#34-任务规划模块) / "
        "[§4](design.md#4-graph-节点设计) 条件边；"
        "[schema.md](schema.md) 三表；"
        "[T22](tasks.md#T22) 验收；"
        "[D-PLAN-01](tasks.md#D-PLAN-01) 模板依赖边。"
    ),
    "`defaults.md` §1–§2；`design.md` §4 HumanConfirm 边；`ConfirmReq.idempotency_key`；T23 验收。": (
        "[defaults.md §1](defaults.md#1-策略开关默认值)–"
        "[§2](defaults.md#2-限额与超时)；"
        "[design.md §4](design.md#4-graph-节点设计) HumanConfirm 边；"
        "`ConfirmReq.idempotency_key`；[T23](tasks.md#T23) 验收。"
    ),
}

# Pointer one-liners in tasks.md (full line match after indent).
TASKS_POINTERS: dict[str, str] = {
    "  - > **【实现决策｜见 D-ENV-01】** 安装改用清华 PyPI 镜像，否则境外源超时无法通过本条验收（详情与原因写在 `defaults.md`）。": (
        "  - > **【实现决策｜见 [D-ENV-01](defaults.md#D-ENV-01)】** 安装改用清华 PyPI 镜像，否则境外源超时无法通过本条验收"
        "（详情与原因写在 [defaults.md](defaults.md)）。"
    ),
    "  - > **【实现决策｜见 D-EXC-01】** 正式基类 `DataReflexError`，`DataAgentError` 为别名；详情与原因写在 `conventions.md` §4.1。": (
        "  - > **【实现决策｜见 [D-EXC-01](conventions.md#D-EXC-01)】** 正式基类 `DataReflexError`，`DataAgentError` 为别名；"
        "详情与原因写在 [conventions.md §4.1](conventions.md#41-自定义异常体系)。"
    ),
    "  - > **【实现决策｜见 D-MODEL-01 / D-MODEL-02】** 增补 `question_tag`、`SubTaskResult`、`ChartSpec` 最小字段；详情与原因写在 `design.md` §3.3 / §3.9。": (
        "  - > **【实现决策｜见 [D-MODEL-01](design.md#D-MODEL-01) / [D-MODEL-02](design.md#D-MODEL-02)】** "
        "增补 `question_tag`、`SubTaskResult`、`ChartSpec` 最小字段；详情与原因写在 "
        "[design.md §3.3](design.md#33-意图解析模块) / [§3.9](design.md#39-结果校验与报告模块)。"
    ),
    "  - 完成：目录与 `.gitignore` 已就绪；Windows 下用 `Test-Path` / `Select-String` 验收通过（见 `defaults.md`）。": (
        "  - 完成：目录与 `.gitignore` 已就绪；Windows 下用 `Test-Path` / `Select-String` 验收通过"
        "（见 [defaults.md §9](defaults.md#9-windows--powershell-环境约定)）。"
    ),
}

OTHER_YIJU: dict[str, dict[str, str]] = {
    "defaults.md": {
        "`risk.md` 已确认的人工确认超时语义、沙箱边界、敏感字段脱敏要求；开发机为 Windows + PowerShell，Unix 验收写法需有等价映射。": (
            "[risk.md](risk.md#risk-summary) 已确认的人工确认超时语义、沙箱边界、敏感字段脱敏要求；"
            "开发机约定见 [§9](defaults.md#9-windows--powershell-环境约定)。"
        ),
        "本文 §5 与 ADR-003（宿主机禁止执行生成代码；不可用返回 `SandboxUnavailableError`）。": (
            "[本文 §5](defaults.md#5-沙箱镜像) 与 [ADR-003](design.md#ADR-003)"
            "（宿主机禁止执行生成代码；不可用返回 `SandboxUnavailableError`）。"
        ),
        "用户明确要求更换国内源；镜像只影响获取速度，不改变依赖版本约束（仍以 `pyproject.toml` 为准）。": (
            "用户明确要求更换国内源；镜像只影响获取速度，不改变依赖版本约束"
            "（仍以 [`pyproject.toml`](../pyproject.toml) 为准）；[T02](tasks.md#T02)。"
        ),
    },
    "conventions.md": {
        "包名/产品名为 DataReflex；T05 验收要求类名以 `Error` 结尾且支持 `raise ... from e`；实现见 `src/data_reflex/exceptions/errors.py`。": (
            "包名/产品名为 DataReflex；[T05](tasks.md#T05) 验收要求类名以 `Error` 结尾且支持 `raise ... from e`；"
            "实现见 [`errors.py`](../src/data_reflex/exceptions/errors.py)。"
        ),
    },
    "schema.md": {
        "`risk.md` P0-6（支持范围已确认）+ 开工前「实现契约」缺口评估；样例规模与枚举取值以可演示、可分层评测为准，而非生产真实分布。": (
            "[risk.md P0-6](risk.md#risk-p0)（支持范围已确认）+ "
            "[handoff 实现契约](handoff.md#实现契约文档)；样例规模与枚举取值以可演示、可分层评测为准，而非生产真实分布。"
        ),
    },
    "design.md": {
        "`defaults.md` §3 新话题规则；`schema.md` §7 标签枚举；实现见 `models/schemas.py`。": (
            "[defaults.md §3](defaults.md#3-新话题规则-new_topic_rule) 新话题规则；"
            "[schema.md §7](schema.md#7-六类问题与字段依赖) 标签枚举；"
            "实现见 [`schemas.py`](../src/data_reflex/models/schemas.py)。"
        ),
        "`risk.md` P2-10（图表仅柱/折/表）；`defaults.md` §6 ECharts；部分成功与终止需保留已完成结果（P0-7 / P1-9）。实现见 `models/schemas.py`。": (
            "[risk.md P2-10](risk.md#risk-p2)（图表仅柱/折/表）；"
            "[defaults.md §6](defaults.md#6-前端图表) ECharts；"
            "部分成功与终止需保留已完成结果（[P0-7](risk.md#risk-p0) / [P1-9](risk.md#risk-p1)）。"
            "实现见 [`schemas.py`](../src/data_reflex/models/schemas.py)。"
        ),
    },
    "handoff.md": {
        "工期评估（演示约 5–6 周，真实评测另加 1–2 周）；`risk.md` 评测策略「Mock 模式先跑通」。": (
            "工期评估（演示约 5–6 周，真实评测另加 1–2 周）；"
            "[risk.md](risk.md#risk-summary) 评测策略「Mock 模式先跑通」。"
        ),
    },
}

# Cross-doc pointer replacements (substring → linked). Keys are unique enough.
CROSS_POINTERS: list[tuple[str, str]] = [
    (
        "> **【实现决策｜见 D-STORE-01】** 持久化实现选用标准库 `sqlite3` + 进程锁串行写、`TaskState` JSON 整包、审计时间有序；详情与原因见 `tasks.md` T09。",
        "> **【实现决策｜见 [D-STORE-01](tasks.md#D-STORE-01)】** 持久化实现选用标准库 `sqlite3` + 进程锁串行写、`TaskState` JSON 整包、审计时间有序；详情与原因见 [T09](tasks.md#T09)。",
    ),
    (
        "> **【实现决策｜见 D-IMPORT-01】** 超限整单拒绝 vs 坏文件跳过、重复列命名、公式转义、单 sheet、Join 与脱敏样本值；详情与原因见 `tasks.md` T10。",
        "> **【实现决策｜见 [D-IMPORT-01](tasks.md#D-IMPORT-01)】** 超限整单拒绝 vs 坏文件跳过、重复列命名、公式转义、单 sheet、Join 与脱敏样本值；详情与原因见 [T10](tasks.md#T10)。",
    ),
    (
        "> **【实现决策｜见 D-CTX-01】** 固定规则 `dataset_or_label_change_without_anaphora`；轮次键与指代词表；`question_tag` 经 `manage(..., question_tag=)` 可选参数传入（不改 Req）；详情与原因见 `tasks.md` T18。",
        "> **【实现决策｜见 [D-CTX-01](tasks.md#D-CTX-01)】** 固定规则 `dataset_or_label_change_without_anaphora`；轮次键与指代词表；`question_tag` 经 `manage(..., question_tag=)` 可选参数传入（不改 Req）；详情与原因见 [T18](tasks.md#T18)。",
    ),
    (
        "> **【实现决策｜见 D-INTENT-01】** v1 规则引擎解析（无 LLM）；超范围/澄清/显式约束词表见 `tasks.md` T20；实现 `modules/intent_parse` + `Node_IntentParse`。",
        "> **【实现决策｜见 [D-INTENT-01](tasks.md#D-INTENT-01)】** v1 规则引擎解析（无 LLM）；超范围/澄清/显式约束词表见 [T20](tasks.md#T20)；实现 `modules/intent_parse` + `Node_IntentParse`。",
    ),
    (
        "> **【实现决策｜见 D-PLAN-01】** 六类固定两步模板、无 LLM；`question_tag` 可选参数；失败写 `PlanResp.error_msg`。详情见 `tasks.md` T21。",
        "> **【实现决策｜见 [D-PLAN-01](tasks.md#D-PLAN-01)】** 六类固定两步模板、无 LLM；`question_tag` 可选参数；失败写 `PlanResp.error_msg`。详情见 [T21](tasks.md#T21)。",
    ),
    (
        "> **【实现决策｜见 D-PLANVAL-01】** 环/缺失输入/超范围校验与 `next_node` 路由；详情见 `tasks.md` T22。",
        "> **【实现决策｜见 [D-PLANVAL-01](tasks.md#D-PLANVAL-01)】** 环/缺失输入/超范围校验与 `next_node` 路由；详情见 [T22](tasks.md#T22)。",
    ),
    (
        "> **【实现决策｜见 D-GENOP-01】** 模板生成 + 格式重试 1 次 + 同批继续；详情见 `tasks.md` T24。",
        "> **【实现决策｜见 [D-GENOP-01](tasks.md#D-GENOP-01)】** 模板生成 + 格式重试 1 次 + 同批继续；详情见 [T24](tasks.md#T24)。",
    ),
    (
        "> **【实现决策｜见 D-SAFE-01】** sqlglot/Python AST、`SELECT *` 一律未授权、解析失败默认拒绝；详情与原因见 `tasks.md` T11。",
        "> **【实现决策｜见 [D-SAFE-01](tasks.md#D-SAFE-01)】** sqlglot/Python AST、`SELECT *` 一律未授权、解析失败默认拒绝；详情与原因见 [T11](tasks.md#T11)。",
    ),
    (
        "> **【实现决策｜见 D-EXEC-01 / D-DOCKER-01】** Protocol + Mock 四态见 T12；Docker 一次性容器、禁网/限额/只读挂载、不可用抛 `SandboxUnavailableError`、关闭隔离仍用容器见 T13。",
        "> **【实现决策｜见 [D-EXEC-01](tasks.md#D-EXEC-01) / [D-DOCKER-01](tasks.md#D-DOCKER-01)】** Protocol + Mock 四态见 [T12](tasks.md#T12)；Docker 一次性容器、禁网/限额/只读挂载、不可用抛 `SandboxUnavailableError`、关闭隔离仍用容器见 [T13](tasks.md#T13)。",
    ),
    (
        "> **【实现决策｜见 D-ERRCLS-01】** 错误类型优先级、指纹归一化、`resource_killed` 不可自愈；详情与原因见 `tasks.md` T14。",
        "> **【实现决策｜见 [D-ERRCLS-01](tasks.md#D-ERRCLS-01)】** 错误类型优先级、指纹归一化、`resource_killed` 不可自愈；详情与原因见 [T14](tasks.md#T14)。",
    ),
    (
        "> **【实现决策｜见 D-RESULT-01】** 结构化约束白名单、合法空默认、NL 约束跳过；详情与原因见 `tasks.md` T15。",
        "> **【实现决策｜见 [D-RESULT-01](tasks.md#D-RESULT-01)】** 结构化约束白名单、合法空默认、NL 约束跳过；详情与原因见 [T15](tasks.md#T15)。",
    ),
    (
        "> **【实现决策｜见 D-REPORT-01】** `chart_requests` 经 Builder 可选参数传入（不改 ReportReq）；partial_success / 不支持类型替代 / 空数据不伪造；详情与原因见 `tasks.md` T16。",
        "> **【实现决策｜见 [D-REPORT-01](tasks.md#D-REPORT-01)】** `chart_requests` 经 Builder 可选参数传入（不改 ReportReq）；partial_success / 不支持类型替代 / 空数据不伪造；详情与原因见 [T16](tasks.md#T16)。",
    ),
    (
        "> **【实现决策｜见 D-CONFIRM-01】** `PlanConfirmGate` 幂等确认 + 超时即拒绝；`next_node` 路由；详情见 `tasks.md` T23。",
        "> **【实现决策｜见 [D-CONFIRM-01](tasks.md#D-CONFIRM-01)】** `PlanConfirmGate` 幂等确认 + 超时即拒绝；`next_node` 路由；详情见 [T23](tasks.md#T23)。",
    ),
    (
        "> **【实现决策｜见 D-NODE-01】** 横切能力收敛于 `graph_engine/nodes/base.py`（计时 / 审计 / 重试 1 次 / 错误截断）；各业务节点只实现 `execute` 增量。详情见 `tasks.md` T19。",
        "> **【实现决策｜见 [D-NODE-01](tasks.md#D-NODE-01)】** 横切能力收敛于 `graph_engine/nodes/base.py`（计时 / 审计 / 重试 1 次 / 错误截断）；各业务节点只实现 `execute` 增量。详情见 [T19](tasks.md#T19)。",
    ),
    (
        "> **【实现决策｜见 D-TRACE-01】** 封装于 `utils/tracing.py`：`trace`/`span` context manager；双密钥门禁；异常吞掉；详情见 `tasks.md` T17。",
        "> **【实现决策｜见 [D-TRACE-01](tasks.md#D-TRACE-01)】** 封装于 `utils/tracing.py`：`trace`/`span` context manager；双密钥门禁；异常吞掉；详情见 [T17](tasks.md#T17)。",
    ),
    (
        "> **【实现决策｜见 D-CTX-01】** 实现见 `modules/context/manager.py`；规则与轮次契约详情见 `tasks.md` T18。",
        "> **【实现决策｜见 [D-CTX-01](tasks.md#D-CTX-01)】** 实现见 `modules/context/manager.py`；规则与轮次契约详情见 [T18](tasks.md#T18)。",
    ),
    (
        "> **【实现决策｜见 D-LOG-01】** 脱敏与上下文注入由 `utils/logger.py` 的 Filter 统一兜底；详情与原因见 `tasks.md` T08。",
        "> **【实现决策｜见 [D-LOG-01](tasks.md#D-LOG-01)】** 脱敏与上下文注入由 `utils/logger.py` 的 Filter 统一兜底；详情与原因见 [T08](tasks.md#T08)。",
    ),
    (
        "> **【实现决策｜见 D-CONFIRM-01】** 超时由 `PlanConfirmGate` 消费；详情见 `tasks.md` T23。",
        "> **【实现决策｜见 [D-CONFIRM-01](tasks.md#D-CONFIRM-01)】** 超时由 `PlanConfirmGate` 消费；详情见 [T23](tasks.md#T23)。",
    ),
    (
        "> **【实现决策｜见 D-CTX-01】** 规则仅在 `ContextManager` 实现；词表与轮次键见 `tasks.md` T18。",
        "> **【实现决策｜见 [D-CTX-01](tasks.md#D-CTX-01)】** 规则仅在 `ContextManager` 实现；词表与轮次键见 [T18](tasks.md#T18)。",
    ),
    (
        "> **【实现决策｜见 D-DOCKER-01】** 运行时由 `DockerSandboxExecutor` 消费本表约定：缺镜像/缺 daemon → `SandboxUnavailableError`；`enable_sandbox=False` 仍用一次性容器。详情与原因见 `tasks.md` T13。",
        "> **【实现决策｜见 [D-DOCKER-01](tasks.md#D-DOCKER-01)】** 运行时由 `DockerSandboxExecutor` 消费本表约定：缺镜像/缺 daemon → `SandboxUnavailableError`；`enable_sandbox=False` 仍用一次性容器。详情与原因见 [T13](tasks.md#T13)。",
    ),
    (
        "> **【实现决策｜见 D-REPORT-01】** 后端 `ReportBuilder` 仅产出 `bar|line|table` 的 `ChartSpec`；不支持类型与空数据行为见 `tasks.md` T16。",
        "> **【实现决策｜见 [D-REPORT-01](tasks.md#D-REPORT-01)】** 后端 `ReportBuilder` 仅产出 `bar|line|table` 的 `ChartSpec`；不支持类型与空数据行为见 [T16](tasks.md#T16)。",
    ),
    (
        "> **【实现决策｜见 D-IMPORT-01】** 数据集落盘与编码探测顺序的原因与依据写在 `tasks.md` T10；此处仅锁定路径/顺序，避免执行器与导入模块各写一套。",
        "> **【实现决策｜见 [D-IMPORT-01](tasks.md#D-IMPORT-01)】** 数据集落盘与编码探测顺序的原因与依据写在 [T10](tasks.md#T10)；此处仅锁定路径/顺序，避免执行器与导入模块各写一套。",
    ),
]

# Insert HTML anchors before full decision headers in non-tasks docs.
ANCHOR_INSERTS: list[tuple[str, str, str]] = [
    (
        "defaults.md",
        "> **【实现决策 D-DEFAULTS-01｜2026-10-25】**",
        '<a id="D-DEFAULTS-01"></a>\n> **【实现决策 D-DEFAULTS-01｜2026-10-25】**',
    ),
    (
        "defaults.md",
        "> **【实现决策 D-DEFAULTS-02｜2026-10-25】**",
        '<a id="D-DEFAULTS-02"></a>\n> **【实现决策 D-DEFAULTS-02｜2026-10-25】**',
    ),
    (
        "defaults.md",
        "> **【实现决策 D-ENV-01｜2026-10-25】**",
        '<a id="D-ENV-01"></a>\n> **【实现决策 D-ENV-01｜2026-10-25】**',
    ),
    (
        "conventions.md",
        "> **【实现决策 D-EXC-01｜2026-10-25】**",
        '<a id="D-EXC-01"></a>\n> **【实现决策 D-EXC-01｜2026-10-25】**',
    ),
    (
        "schema.md",
        "> **【实现决策 D-SCHEMA-01｜2026-10-25】**",
        '<a id="D-SCHEMA-01"></a>\n> **【实现决策 D-SCHEMA-01｜2026-10-25】**',
    ),
    (
        "design.md",
        "> **【实现决策 D-MODEL-01｜2026-10-25】**",
        '<a id="D-MODEL-01"></a>\n> **【实现决策 D-MODEL-01｜2026-10-25】**',
    ),
    (
        "design.md",
        "> **【实现决策 D-MODEL-02｜2026-10-25】**",
        '<a id="D-MODEL-02"></a>\n> **【实现决策 D-MODEL-02｜2026-10-25】**',
    ),
    (
        "handoff.md",
        "> **【实现决策 D-DONE-01｜2026-10-25】**",
        '<a id="D-DONE-01"></a>\n> **【实现决策 D-DONE-01｜2026-10-25】**',
    ),
]


def apply_yiju(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    n = 0
    for old, new in mapping.items():
        needle = f"> **依据：** {old}"
        repl = f"> **依据：** {new}"
        if needle not in text:
            # tasks.md uses 4-space indent before >
            needle2 = f"    > **依据：** {old}"
            repl2 = f"    > **依据：** {new}"
            if needle2 in text:
                text = text.replace(needle2, repl2, 1)
                n += 1
            else:
                print(f"MISS 依据: {old[:60]}...")
            continue
        text = text.replace(needle, repl, 1)
        n += 1
    return text, n


def ensure_adr_and_risk_anchors() -> None:
    import re

    design = DOCS / "design.md"
    lines = design.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        m = re.match(r"^(### (ADR-\d+)：)", line)
        if m:
            aid = m.group(2)
            prev = out[-1] if out else ""
            if f'<a id="{aid}"></a>' not in prev:
                out.append(f'<a id="{aid}"></a>\n')
        out.append(line)
    design.write_text("".join(out), encoding="utf-8")
    print("design.md: ADR short anchors ensured")

    risk = DOCS / "risk.md"
    rtext = risk.read_text(encoding="utf-8")
    inserts = [
        ("risk-p0", "## P0 阻断级：不先定清楚，开发或演示会翻车"),
        ("risk-p1", "## P1 高风险：会明显拖慢进度或影响可信度"),
        ("risk-p2", "## P2 中风险：不致命，但会影响体验或论文严谨性"),
        ("risk-summary", "## 已确认的关键决策摘要"),
    ]
    for aid, heading in inserts:
        marker = f'<a id="{aid}"></a>'
        if marker in rtext:
            continue
        if heading not in rtext:
            print(f"MISS risk section: {heading}")
            continue
        rtext = rtext.replace(heading, f"{marker}\n{heading}", 1)
    risk.write_text(rtext, encoding="utf-8")
    print("risk.md: section anchors ensured")


def main() -> None:
    ensure_adr_and_risk_anchors()
    tasks = (DOCS / "tasks.md").read_text(encoding="utf-8")
    tasks, n1 = apply_yiju(tasks, TASKS_YIJU)
    n2 = 0
    for old, new in TASKS_POINTERS.items():
        if old not in tasks:
            print(f"MISS pointer: {old[:50]}...")
            continue
        tasks = tasks.replace(old, new, 1)
        n2 += 1
    # Align already-linked GENOP / SAFE-NODE ADR slugs to short anchors.
    tasks = tasks.replace(
        "(design.md#adr-002规划范式选-plan-and-execute不选纯-react)",
        "(design.md#ADR-002)",
    )
    (DOCS / "tasks.md").write_text(tasks, encoding="utf-8")
    print(f"tasks.md: {n1} 依据, {n2} pointers")

    for fname, mapping in OTHER_YIJU.items():
        path = DOCS / fname
        text = path.read_text(encoding="utf-8")
        text, n = apply_yiju(text, mapping)
        path.write_text(text, encoding="utf-8")
        print(f"{fname}: {n} 依据")

    # Anchors (idempotent)
    for fname, old, new in ANCHOR_INSERTS:
        path = DOCS / fname
        text = path.read_text(encoding="utf-8")
        if f'<a id="{old.split("D-")[1].split("｜")[0]}"></a>' in text or new.split("\n")[0] in text:
            # already anchored if id line present
            aid = new.split('"')[1]
            if f'<a id="{aid}"></a>' in text:
                print(f"{fname}: anchor {aid} exists")
                continue
        if old not in text:
            print(f"MISS anchor target in {fname}: {old}")
            continue
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        print(f"{fname}: added anchor for {old}")

    # Cross pointers across all docs
    n3 = 0
    for path in DOCS.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        orig = text
        for old, new in CROSS_POINTERS:
            if old in text:
                text = text.replace(old, new, 1)
                n3 += 1
        if text != orig:
            path.write_text(text, encoding="utf-8")
    print(f"cross pointers applied: {n3}")


if __name__ == "__main__":
    main()
