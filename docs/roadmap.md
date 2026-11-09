# Roadmap

本文件只记录**尚未做完 / 规划中**的事项（面向读者：想知道「下一步做什么」）。

已发布内容见仓库根目录 [`CHANGELOG.md`](../CHANGELOG.md)。  
**阅读约定：** 本文自洽可读；不与 `tasks.md`、ADR、`architecture.md` 做网状交叉引用。

<a id="D-DOC-01"></a>
> **【实现决策 D-DOC-01｜2026-11-04】**  
> **决定：** 版本史用根目录 `CHANGELOG.md`；前瞻计划用本文；与其它文档只保留「README 索引 + handoff 下一步」入口。  
> **原因：** 契约/任务/ADR 链接已经过密。  
> **依据：** 交接可读性；演示完成 vs 实验完成两级口径。

<a id="D-V15-01"></a>
> **【实现决策 D-V15-01｜2026-11-04】**  
> **决定：** 用户口头「v1.5」= 本文件 **实验完成** 轨道。本阶段「真实」优先指 **Docker 沙箱对 fixtures 真执行**；Graph 意图/规划/生成仍为规则+模板。GLM Key 用于 `scripts/smoke_llm.py` 连通性，不宣称「评测 SQL 由 GLM 生成」。  
> **原因：** 现网代码无 LLM 节点；先交付真数字执行与可区分的 `mode: real` 报表。  
> **依据：** 完成定义「实验完成」；`OpGenerator` 模板路径；宿主机禁执行。

<a id="D-V2-01"></a>
> **【实现决策 D-V2-01｜2026-11-06】**  
> **决定：** v2  backlog 落在本文（不新开 `T46+` 任务树）；条目用 `V2-x` 编号。另设「面试表现优先」排序，与「工程完整度」排序可不同——求职演示优先可讲清的智能闭环与结果可信，而非先做多用户部署。  
> **原因：** v1/v1.5 已关门；后续按需开工，避免与已关闭 tasks 双轨。  
> **依据：** D-DOC-01；v1.5 Docker 真跑暴露的过滤/冗余表问题；求职叙事「LLM + 沙箱 + 安全」。

---

## 状态总览

| 版本目标 | 状态 | 一句话 |
| :--- | :--- | :--- |
| **v1.0 演示完成** | 已完成 | T01–T45；Mock 可演示全链路 |
| **v1.5 实验完成** | **已完成**（CHANGELOG 1.5.0） | Docker 真执行 + real 评测 + GLM 冒烟 |
| **v2.x** | 进行中 | **V2-1～V2-5、V2-7、V2-9 已落地**；其余 backlog 待做 |

---

## v1.5 — 实验完成（已关闭）

| ID | 事项 | 状态 |
| :--- | :--- | :--- |
| R1 | 演示脚本 [`demo_script.md`](demo_script.md) | **完成** |
| R2 | Docker 镜像 + `SANDBOX_BACKEND=docker` + CSV 装载 | **完成** |
| R3 | `scripts/smoke_llm.py`（当前默认 `glm-4-flash-250414`） | **完成** |
| R4 | `--mode real` 评测 | **完成** |
| R5 | Mock vs Real 可信边界 | **完成**（见下） |

### 可信边界（R5）

| 模式 | 结果数字含义 | 需要 |
| :--- | :--- | :--- |
| `SANDBOX_BACKEND=mock` / `--mode mock` | 演示用罐头行 / 模板路径指标 | 无 Docker、无 Key |
| `SANDBOX_BACKEND=docker` / `--mode real` | 容器内对 CSV 真跑模板 SQL/Python | Docker + 镜像 |
| `scripts/smoke_llm.py` | 仅证明 API 连通 | Key；**不**改变 Graph 生成路径 |

---

## v2.x — 可改进 backlog

仍建议守住三条硬约束：无宿主机执行生成代码；危险操作不可因「智能」旁路；评测区分 mock / live。

### 条目总表

| ID | 方向 | 状态 | 改什么 | 预期效果 |
| :--- | :--- | :--- | :--- | :--- |
| **V2-1** | Graph 接 GLM | **完成**（默认 `ENABLE_LLM_OPS=true`；无 Key 回退模板） | GenerateOp / 澄清 / Reflect 可选调 GLM；失败回退模板；仍经 Safety + Docker | 面试可讲「LLM+沙箱」闭环 |
| **V2-2** | 约束落地 | **完成**（见 D-V2-CONSTRAINT-01） | 问句过滤写入 SQL；TopN 解析加固；图表纵轴优先金额列 | 结果对齐字面意图 |
| **V2-3** | 规划去冗余 | **完成**（见 D-V2-DEDUP-01） | 中间步不进报告；冗余 compare 复用上一步结果 | 报告更干净 |
| **V2-4** | 人话报告摘要 | **完成**（见 D-V2-CONCLUSION-01） | 六类模板结论 + 可选 GLM 润色（数字校验） | 演示更像分析 |
| **V2-5** | 多轮指代 | **完成**（见 D-V2-FOLLOWUP-01） | 前端携带 1–2 轮；同上/排除继承约束；实体指代澄清 | 对话式 BI 骨架 |
| **V2-6** | 可配置 Schema | 待做 | 少写死三表六类 | 更通用 |
| **V2-7** | 观测与成本 | **完成**（见 D-V2-OBS-01） | 任务根 Trace + LLM generation + flush；Cloud | 论文可观测 |
| **V2-8** | 产品化 | 待做 | 鉴权、部署、多用户 | 可对外试用 |
| **V2-9** | UI 标明 sandbox | **完成**（见 D-V2-SANDBOX-UI-01） | 页头徽章读 `/health.sandbox_backend` | 少误判 |

<a id="D-V2-LLM-01"></a>
> **【实现决策 D-V2-LLM-01｜2026-11-06】**  
> **决定：** (1) 新增 `enable_llm_ops`（现默认 **true**；无 Key 时行为等同关闭）；(2) LLM 仅用于 GenerateOp 文本、澄清问句润色、Reflect `fix_hint`、报告结论润色（V2-4）；(3) 任何失败/无 Key/格式门不通过 → **模板降级**；(4) 生成代码仍只进 Safety + 沙箱，宿主机不 `eval`/`exec`；(5) 调用计入 `llm_calls_used`，与既有熔断上限共用。  
> **原因：** 面试需要「LLM Agent」叙事；默认开启便于演示，但 CI/Mock 不依赖真实 API。  
> **依据：** roadmap V2-1；handoff 三条硬约束；D-V2-01。

> 说明：演示需配置 `LLM_API_KEY`；要强制纯模板可在 `.env` 设 `ENABLE_LLM_OPS=false` 并**重启** uvicorn。

<a id="D-V2-CONSTRAINT-01"></a>
> **【实现决策 D-V2-CONSTRAINT-01｜2026-11-07】**  
> **决定（V2-2 验收）：**  
> (1) 意图已抽出的显式约束必须进入模板 SQL：`channel IN (...)`、`user_segment IN (...)`、`is_promo=`、`top_n=`（及既有时间粒度）；  
> (2) TopN 解析支持「前 5 / 前5 / Top5」等空格变体，避免默认 `top_n=10` 把全库商品刷出来；  
> (3) 前端柱状图纵轴优先 `amount_sum` / `return_rate` 等指标列，勿误选 `order_count` 充当「销售额」；  
> (4) 双表一模一样（agg≈compare）归 **V2-3**，本项不改规划拓扑。  
> **原因：** 演示暴露「对比 APP/官网却出四渠道」「Top5 却出 7 行」「图纵轴像订单量」——意图层已有约束字符串，缺的是落地到 SQL/展示。  
> **依据：** roadmap V2-2；intent `extract_constraints`；求职现场可讲「意图→约束→SQL」。

<a id="D-V2-DEDUP-01"></a>
> **【实现决策 D-V2-DEDUP-01｜2026-11-07】**  
> **决定（V2-3）：**  
> (1) 计划仍保持 ≥2 子任务（不影响 planner 验收）；中间步 `expected_output.report=False`；  
> (2) 渠道/促销/时间趋势的 compare 步设 `reuse_result=True`，Execute **复制**上一步行集，不再二次进沙箱；  
> (3) Report 只展示 `report!=False` 的成功表，并按计划 `chart` 自动生成柱/折线请求。  
> **原因：** agg 与 compare 模板 SQL 同构时会出现「两张一模一样的表」。  
> **依据：** roadmap V2-3；演示反馈。

<a id="D-V2-CONCLUSION-01"></a>
> **【实现决策 D-V2-CONCLUSION-01｜2026-11-07】**  
> **决定（V2-4）：**  
> (1) Report 对六类 `question_tag` 用结果表生成中文结论草稿（规则模板，无 Key 也可演示）；  
> (2) `ENABLE_LLM_OPS=true` 且有 Key 时，用受限 prompt 润色草稿；润色结果须保留草稿中的关键数字，否则回退草稿；  
> (3) `summary` = 中文结论 + 原机器审计行（`task=… successfully…`）；前端人话区只展示中文行。  
> **原因：** 面试官扫一眼报告就能听到结论，不只看表；数字保真优先于文采。  
> **依据：** roadmap V2-4；D-V2-LLM-01 降级策略。

<a id="D-V2-FOLLOWUP-01"></a>
> **【实现决策 D-V2-FOLLOWUP-01｜2026-11-07】**  
> **决定（V2-5）：**  
> (1) 会话上下文由**前端**携带最近 1–2 轮（`TaskSubmitReq.conversation_context`），不做服务端 session；(2) 同标签或可解析增量指代（同上/再按/排除）→ 继承 `question_tag` 并合并上轮约束（本轮同名键覆盖，`exclude=` 追加）；(3) 「刚才那个渠道」等需读结果行的实体指代 → **澄清**，不猜测；(4) 轮次只摘要，不回灌整表。  
> **原因：** 接通真实 UI 多轮即可演示对话式 BI；实体消解留给后续，避免幻觉。  
> **依据：** roadmap V2-5；ADR-018；defaults §3 / D-CTX-01。

<a id="D-V2-SANDBOX-UI-01"></a>
> **【实现决策 D-V2-SANDBOX-UI-01｜2026-11-08】**  
> **决定（V2-9）：**  
> (1) `GET /health` 返回 `sandbox_backend`（`mock`|`docker`），与运行时 Settings 一致；(2) 前端页头展示正式文案——Mock「演示占位数据」、Docker「容器内对 CSV 真实计算」；(3) 不做 daemon/镜像探测，不在每个任务卡重复标注；改 `.env` 后仍须重启 API。  
> **原因：** 面试官扫一眼即可区分罐头数与真算，避免误判可信口径。  
> **依据：** roadmap V2-9；R5 可信边界。

<a id="D-V2-OBS-01"></a>
> **【实现决策 D-V2-OBS-01｜2026-11-08】**  
> **决定（V2-7）：**  
> (1) 托管用 **Langfuse Cloud**（`LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`HOST`）；无 Key → no-op，不阻断主流程；(2) 每任务一根根 Trace，节点 Span / LLM `generation` 经 `trace_context` 挂接；任务结束 `flush`；(3) generation 只记模型名、message 条数与 usage（若有），不回灌完整 prompt；(4) `/health.tracing_enabled` + `scripts/smoke_langfuse.py`。  
> **原因：** 面试可截 Trace 讲可观测与成本；不绑自托管以免抬高演示门槛。  
> **依据：** roadmap V2-7；T17 / D-TRACE-01；ADR-004。

---

## 面试表现优先排序

目标：**短时间最大化「能讲清、能演示、差异化」**（AI 应用开发岗），不是先追求产品完整度。

| 次序 | ID | 面试价值（为什么先做） | 建议体量 |
| :---: | :--- | :--- | :--- |
| 1 | **V2-1** | 叙事从「规则模板系统」升级为「LLM Agent + 安全沙箱」；最容易被追问也最好答 | 中高 |
| 2 | **V2-2** | 现场一问「为何 APP/官网题却出四渠道」——改完可正面展示意图→约束→SQL | 中 |
| 3 | **V2-4** | 面试官扫一眼报告就能听到结论，不只看表 | 低～中 |
| 4 | **V2-3** | 避免「两张表一模一样」被当成 bug；显得规划有设计 | 低～中 |
| 5 | **V2-9** | 演示时主动指出 mock/docker，显得你懂边界与可信口径 | 极低 |
| 6 | **V2-5** | 多轮加分项；做好了像对话 BI，做不稳反而暴露幻觉 | 中 |
| 7 | **V2-7** | 有 Langfuse 截图可撑「可观测」；无则口头讲审计/SSE 也可 | 中 |
| 8 | **V2-6** | 故事大但工期长；面试前不如把 1～5 做透 | 很高 |
| 9 | **V2-8** | 鉴权部署难加分到「Agent 能力」本身；有余力再做 | 高 |

### 面试向开工切片（建议）

1. **一周内可演示：** V2-9 → V2-3/V2-4 → V2-2（结果可信 + 好讲）。  
2. **两周叙事升级：** V2-1（Graph 接 GLM，保留 Safety + Docker + 降级）。  
3. **有余力再加分：** V2-5、V2-7；**默认不做满：** V2-6、V2-8。

### 面试话术锚点（改完后可怎么讲）

- 「生成代码不进宿主机，只进一次性容器；关隔离也不降级宿主机。」  
- 「模型不可用走降级报告，且标注非分析结论。」  
- 「mock / docker / live 评测口径分开，不拿罐头数充论文。」  
- （V2-1 后）「LLM 负责生成与解释，安全校验与沙箱负责执行边界。」

---

## 刻意不做（近期）

- 为路线图再开 `T46+` 任务树（用本文 `V2-x` 即可）  
- 把 Mock 数字写成真实评测结论  
- 无 Key / 无 Docker 时宣称实验完成  
- 为接 GLM 而放开宿主机 `eval`/`exec`
