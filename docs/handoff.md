# handoff.md

## 项目是什么、给谁用

**DataReflex**：面向固定结构化数据集（订单、退货、用户三表）的自然语言分析系统，本科毕业设计与 AI 应用开发岗求职项目。用户上传数据并用自然语言提问，系统自动规划子任务、在隔离环境中执行、失败定向自愈，输出结论与图表；模型彻底不可用时输出明确标注“非分析结论”的静态诊断报告。v1 只做只读分析，固定六类问题：退货率 TopN、渠道对比、时间趋势、用户分层对比、促销与非促销对比、复购率。

## 技术栈

Python 3.11+、LangGraph、FastAPI + SSE、SQLite、Docker SDK、Pydantic v2、Langfuse（可 no-op 降级）、Vue 3 + Vite、Ruff/Black/mypy strict/pytest。

## 完成定义（已锁定）

<a id="D-DONE-01"></a>
> **【实现决策 D-DONE-01｜2026-10-25】**  
> **决定：** v1 关门条件定为「演示完成」；真实 GLM-4 评测为「实验完成」可选加分，不阻塞 T45。  
> **原因：** T45 通过 ≠ 论文可用数字；若不拆两级，「必须跑真实模型」会把 Docker/额度/网络不确定性绑进主路径，工期与演示风险不可控。Mock 先跑通路径与指标管道，真实数字可后补。  
> **依据：** 工期评估（演示约 5–6 周，真实评测另加 1–2 周）；[risk.md](risk.md#risk-summary) 评测策略「Mock 模式先跑通」。

v1 交付标准定为 **演示完成**，不以真实模型论文数字为关门条件：

| 级别 | 是否 v1 关门 | 含义 |
| :--- | :--- | :--- |
| **演示完成（v1 必达）** | 是 | `tasks.md` T01–T45 验收通过；Mock LLM +（Mock 或真实）Docker 跑通成功 / 自愈 / 降级三条主路径；Mock 模式评测报表可导出且标注 `mode: mock`；前端可完成上传、提问、计划确认、轨迹与结果展示。 |
| **实验完成（可选加分）** | 否 | 在演示完成之上，用真实 GLM-4 对限定用例集跑评测，产出可区分于 mock 的真实模式报表；供论文/简历数字，不阻塞 v1 合并与演示。 |

预计工期口径：演示完成约 **5–6 周**（全职单人）；实验完成另加约 **1–2 周**。

## 实现决策补注规范（强制）

凡对文档歧义自行理解并落地的选择，**必须**在源文档对应处追加醒目补注（不要只写在对话里），格式固定为：

> **【实现决策 D-xxx｜YYYY-MM-DD】**  
> **决定：** …  
> **原因：** …（可直接回答「当初为什么采取这个决策？」）  
> **依据：** …（用 Markdown 链接指向源文档章节 / 任务锚点，见下）

**依据链接约定：**

1. **章节标题**：`[design.md §3.6](design.md#36-安全校验模块)`（锚点 = 标题 slug，中文标题一般保留汉字）。
2. **任务条目**：在 `tasks.md` 对应任务行上方放显式锚点 `<a id="T25"></a>`，依据写 `[T25](tasks.md#T25)`。
3. **决策正文**：决策块上方放 `<a id="D-SAFE-NODE-01"></a>`，交叉引用写 `[D-SAFE-NODE-01](tasks.md#D-SAFE-NODE-01)`。
4. **ADR / 风险分级**：`design.md` 用短锚点 `[ADR-003](design.md#ADR-003)`；`risk.md` 用 `[risk-p0](risk.md#risk-p0)` / `risk-p1` / `risk-p2` / `risk-summary`。
5. **依据行必须可点**：每条完整决策的「依据」至少含一个 Markdown 链接；「见 D-xxx」指针也要链到决策锚点。
6. 无法落到标题/锚点时，仍可写纯文本路径，但不优先。

**Cursor / VS Code 跳转限制（已知）：**

- 仓库内仍用 `<a id="D-xxx"></a>` + `…md#D-xxx`（GitHub / 多数 Markdown 预览可跳）。
- **Cursor 聊天里的蓝色文件链**、以及编辑器源码里 **Ctrl/Cmd+Click**，对 `#片段` 支持不完整：常把 `docs/tasks.md#D-REPRO-01` 整段当路径打开，找不到锚点就报 `Unable to open …#…`（不是文件缺失）。
- **同文件**请优先写 `[D-REPRO-01](#D-REPRO-01)`；跨文件仍写 `tasks.md#D-REPRO-01`。
- 聊天链失效时：打开 [`docs/tasks.md`](tasks.md) → `Ctrl/Cmd+F` 搜 `D-REPRO-01`；或开 Markdown Preview 再点链接。

已落盘决策索引（随任务追加，回顾以各源文档正文为准）：

| ID | 源文档位置 | 主题 |
| :--- | :--- | :--- |
| [D-SCHEMA-01](schema.md#D-SCHEMA-01) | [schema.md](schema.md) 文首 | 三表字段契约 |
| [D-DEFAULTS-01](defaults.md#D-DEFAULTS-01)/[02](defaults.md#D-DEFAULTS-02)、[D-ENV-01](defaults.md#D-ENV-01) | [defaults.md](defaults.md) 文首 | 默认值集中 / 沙箱镜像 / 清华源 |
| [D-DONE-01](#D-DONE-01) | [本文「完成定义」](#完成定义已锁定) | 演示完成 vs 实验完成 |
| [D-EXC-01](conventions.md#D-EXC-01) | [conventions.md §4.1](conventions.md#41-自定义异常体系) | 异常基类命名 |
| [D-LINT-01](tasks.md#D-LINT-01)、[D-MYPY-01](tasks.md#D-MYPY-01) | [T03](tasks.md#T03) | UP009 / 第三方 mypy ignore |
| [D-CFG-01](tasks.md#D-CFG-01) | [T04](tasks.md#T04) | Settings `extra=ignore` |
| [D-MODEL-01](design.md#D-MODEL-01)/[02](design.md#D-MODEL-02) | [design §3.3](design.md#33-意图解析模块) / [§3.9](design.md#39-结果校验与报告模块) | `question_tag` / SubTaskResult·ChartSpec |
| [D-LOG-01](tasks.md#D-LOG-01) | [T08](tasks.md#T08) + [conventions §5](conventions.md#5-日志规范) | 结构化日志与脱敏 |
| [D-STORE-01](tasks.md#D-STORE-01) | [T09](tasks.md#T09) + [design §2](design.md#2-模块划分与职责) | SQLite 审计存储 |
| [D-IMPORT-01](tasks.md#D-IMPORT-01) | [T10](tasks.md#T10) + [design §3.1](design.md#31-数据导入模块) | 导入边界与落盘 |
| [D-SAFE-01](tasks.md#D-SAFE-01) | [T11](tasks.md#T11) + [design §3.6](design.md#36-安全校验模块) | SQL/Python AST 安全校验 |
| [D-EXEC-01](tasks.md#D-EXEC-01) | [T12](tasks.md#T12) + [design §3.7](design.md#37-隔离执行器) | 沙箱 Protocol / Mock 四态 |
| [D-DOCKER-01](tasks.md#D-DOCKER-01) | [T13](tasks.md#T13) + [design §3.7](design.md#37-隔离执行器) | Docker 一次性容器隔离 |
| [D-ERRCLS-01](tasks.md#D-ERRCLS-01) | [T14](tasks.md#T14) + [design §3.8](design.md#38-错误分类与自愈模块) | 执行错误分类与指纹 |
| [D-RESULT-01](tasks.md#D-RESULT-01) | [T15](tasks.md#T15) + [design §3.9](design.md#39-结果校验与报告模块) | 结构化结果校验 |
| [D-REPORT-01](tasks.md#D-REPORT-01) | [T16](tasks.md#T16) + [design §3.9](design.md#39-结果校验与报告模块) | 报告汇总与图表替代 |
| [D-TRACE-01](tasks.md#D-TRACE-01) | [T17](tasks.md#T17) + [ADR-004](design.md#ADR-004) | Langfuse 追踪与 no-op |
| [D-CTX-01](tasks.md#D-CTX-01) | [T18](tasks.md#T18) + [design §3.2](design.md#32-上下文管理模块) | 上下文与新话题规则 |
| [D-NODE-01](tasks.md#D-NODE-01) | [T19](tasks.md#T19) + [design §4](design.md#4-graph-节点设计) | BaseNode 计时/审计/重试 |
| [D-INTENT-01](tasks.md#D-INTENT-01) | [T20](tasks.md#T20) + [design §3.3](design.md#33-意图解析模块) | 意图解析规则引擎 |
| [D-PLAN-01](tasks.md#D-PLAN-01) | [T21](tasks.md#T21) + [design §3.4](design.md#34-任务规划模块) | 六类固定规划模板 |
| [D-PLANVAL-01](tasks.md#D-PLANVAL-01) | [T22](tasks.md#T22) + [design §4](design.md#4-graph-节点设计) | 计划校验与 next_node |
| [D-CONFIRM-01](tasks.md#D-CONFIRM-01) | [T23](tasks.md#T23) + [defaults §2](defaults.md#2-限额与超时) | 人工确认幂等与超时 |
| [D-GENOP-01](tasks.md#D-GENOP-01) | [T24](tasks.md#T24) + [design §3.5](design.md#35-操作生成模块) | 操作生成模板与格式重试 |
| [D-SAFE-NODE-01](tasks.md#D-SAFE-NODE-01) | [T25](tasks.md#T25) + [design §3.6](design.md#36-安全校验模块) | SafetyCheck 节点路由与白名单 |
| [D-EXEC-NODE-01](tasks.md#D-EXEC-NODE-01) | [T26](tasks.md#T26) + [design §3.7](design.md#37-隔离执行器) | Execute 节点路由与资源失败 |
| [D-ERRCLS-NODE-01](tasks.md#D-ERRCLS-NODE-01) | [T27](tasks.md#T27) + [design §3.8](design.md#38-错误分类与自愈模块) | ErrorClassify 指纹与路由 |
| [D-REFLECT-01](tasks.md#D-REFLECT-01) | [T28](tasks.md#T28) + [design §3.8](design.md#38-错误分类与自愈模块) | Reflect 熔断优先级与回环 |
| [D-RESULT-NODE-01](tasks.md#D-RESULT-NODE-01) | [T29](tasks.md#T29) + [design §3.9](design.md#39-结果校验与报告模块) | ResultValidate 可自愈回环 |
| [D-REPORT-NODE-01](tasks.md#D-REPORT-NODE-01) | [T30](tasks.md#T30) + [design §3.9](design.md#39-结果校验与报告模块) | Report 保底摘要与 Fallback 路由 |
| [D-FALLBACK-01](tasks.md#D-FALLBACK-01) | [T31](tasks.md#T31) + [design §3.10](design.md#310-降级报告模块) | 静态诊断降级报告 |
| [D-GRAPH-01](tasks.md#D-GRAPH-01) | [T32](tasks.md#T32) + [design §4](design.md#4-graph-节点设计) | 主工作流组装与 Checkpointer |
| [D-FLAG-01](tasks.md#D-FLAG-01) | [T33](tasks.md#T33) + [defaults §1](defaults.md#1-策略开关默认值) | 策略开关单图分支 |
| [D-API-01](tasks.md#D-API-01) | [T34](tasks.md#T34) + [design §3.11](design.md#311-任务调度与-api-事件) | FastAPI / 串行队列 / SSE |
| [D-FE-01](tasks.md#D-FE-01) | [T35](tasks.md#T35) + [ADR-013](design.md#ADR-013) | Vue 3 最小前端 |
| [D-INT-01](tasks.md#D-INT-01) | [T36](tasks.md#T36) + [D-GRAPH-01](tasks.md#D-GRAPH-01) | 主工作流集成测试 |
| [D-SAFE-INT-01](tasks.md#D-SAFE-INT-01) | [T37](tasks.md#T37) + [D-DOCKER-01](tasks.md#D-DOCKER-01) | 安全与沙箱集成测试 |
| [D-ABLATION-01](tasks.md#D-ABLATION-01) | [T38](tasks.md#T38) + [D-FLAG-01](tasks.md#D-FLAG-01) | 策略开关消融集成测试 |
| [D-GATE-01](tasks.md#D-GATE-01) | [T45](tasks.md#T45) | 质量门禁收尾（禁 print / 无宿主机执行） |
| [D-DOC-01](roadmap.md#D-DOC-01) | [roadmap.md](roadmap.md) | CHANGELOG / roadmap 分册，少交叉链接 |
| [D-V2-01](roadmap.md#D-V2-01) | [roadmap.md](roadmap.md) | v2 backlog + 面试优先排序 |
| [D-V2-LLM-01](roadmap.md#D-V2-LLM-01) | [roadmap.md](roadmap.md) | V2-1 Graph LLM 开关与模板降级 |

## 实现契约文档

| 文档 | 用途 |
| :--- | :--- |
| [`schema.md`](schema.md) | 三表列名、类型、Join 键、六类问题字段依赖 |
| [`defaults.md`](defaults.md) | 策略开关、超时限额、敏感字段、沙箱镜像、ECharts、温度/时钟、Windows 验收等价命令 |
| [`eval/datasets/fixtures/`](../eval/datasets/fixtures/) | 固定样例 `orders.csv` / `returns.csv` / `users.csv` |
| [`eval/datasets/core.jsonl`](../eval/datasets/core.jsonl) | 分层评测核心集（60 条；见 [D-EVALSET-01](tasks.md#D-EVALSET-01)） |

## 当前进度

| 阶段 | 任务 | 状态 |
| :--- | :--- | :--- |
| 开工前契约 | `schema.md` / `defaults.md` / 完成定义 / fixtures | 已完成 |
| 一、环境骨架 | T01–T04 | 已完成（已阶段性验收） |
| 二、数据模型 | T05–T07 | 已完成（已阶段性验收） |
| 三、工具层 | T08–T18 | **已完成**（T08–T18） |
| 四、Agent 节点 | T19–T31 | **已完成**（T19–T31） |
| 五、Graph 编排 | T32–T35 | **已完成**（T32–T35） |
| 六、测试 | T36–T39 | **已完成**（T36–T39；覆盖率门禁 D-COV-01） |
| 七、评测脚本 | T40–T42 | **已完成**（T40–T42；D-EVALSET-01 / D-EVAL-01 / D-REPRO-01） |
| 八、文档收尾 | T43–T45 | **已完成**（T43–T45；D-README-01 / D-ARCH-01 / D-GATE-01） |

进度 **45/45 任务**（100%）。v1 **演示完成**关门条件已满足：核心能力 + 评测 + [`README.md`](../README.md) + [`docs/architecture.md`](architecture.md) + 质量门禁。可选加分：真实 Docker 沙箱联调、真实 GLM-4 评测（实验完成，不阻塞 v1）。

基准文档已锁定；默认值以 `defaults.md` 为准；三表契约以 `schema.md` 为准。pip 使用清华源（D-ENV-01）。异常基类以 `DataReflexError` 为准，`DataAgentError` 为其别名（D-EXC-01）。

## 下一步

`tasks.md` T01–T45 与 **v1.5 实验完成**（CHANGELOG **1.5.0**）均已关闭；**V2-1～V2-5、V2-7、V2-9** 已落地（见 [`roadmap.md`](roadmap.md) / D-V2-*）。演示脚本见 [`demo_script.md`](demo_script.md)。有余力可看 V2-6；默认不做满 V2-8。对歧义的自行决策仍须按上文「实现决策补注规范」回写源文档。

## 最重要的三条约束

1. **宿主机绝对禁止执行模型生成代码**：必须经安全校验后送入一次性 Docker 容器；关闭隔离仅限评测且仍用容器；Docker 不可用返回错误，不降级宿主机。
2. **v1 固定三表 + 六类问题，只读分析**：危险写操作和越权字段直接拦截；超范围请求拒绝；自然语言约束无法转显式条件时必须澄清，不猜测执行。字段以 `schema.md` 为准。
3. **所有策略通过配置开关切换，不复制第二套业务代码**：评测与主流程共用同一代码路径；指标必须区分模拟模式与真实模型；总模型调用上限优先于自愈重试上限，连续相同错误指纹两次熔断。
