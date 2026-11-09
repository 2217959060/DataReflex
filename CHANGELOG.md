# Changelog

本文件只记录**已经发生**的版本变更（面向读者：想知道「现在这版有什么」）。

规划中的事项见 [`docs/roadmap.md`](docs/roadmap.md)。本文尽量自洽，不要求先读完 `tasks.md` / ADR。

格式大致遵循 [Keep a Changelog](https://keepachangelog.com/)；版本号语义：

| 段 | 含义（本项目约定） |
| :--- | :--- |
| **MAJOR**（如 2.0） | 产品边界大变（问题集/Schema/执行模型等） |
| **MINOR**（如 1.5） | 在 v1 边界内增强（真沙箱、真模型连通等） |
| **PATCH**（如 1.0.1） | 修复与文档小改，不改能力边界 |

---

## [Unreleased] — v2.x

### Added — V2-1 Graph LLM（完成）

- `ENABLE_LLM_OPS` / `enable_llm_ops`：Graph 可选调用 GLM（默认现为 `true`；无 Key 仍模板降级）
- `modules/llm/`：OpenAI 兼容客户端 + 提示词；单测用 `StaticLlmClient`
- GenerateOp：LLM 生成 SQL/Python → 格式门校验 → 失败回退模板；计入 `llm_calls_used`
- Reflect：重试时可 LLM 润色 `fix_hint`（失败则用分类器原文）
- Intent：模糊约束澄清问句可 LLM 润色（失败则用规则文案）

### Changed — V2-2 约束落地（完成）

- TopN 解析支持「前 5」等空格变体，避免误用默认 `top_n=10`
- 模板 SQL 写入 `channel IN` / `user_segment IN` / `is_promo` / exclude；compare 步复用带过滤的聚合
- LLM 若丢掉上述约束或 `LIMIT`，回退模板（D-V2-CONSTRAINT-01）
- 前端柱状图纵轴优先 `amount_sum` / `return_rate`，避免「销售额」图画出订单量

### Changed — V2-3 规划去冗余（完成）

- 中间步 `report=False`，最终报告只展示叶子结果表
- 渠道/促销/时间 compare 步 `reuse_result`：Execute 复制上一步，不再二次进沙箱
- Report 按计划 `chart` 自动生成柱/折线（D-V2-DEDUP-01）

### Added — V2-4 人话报告摘要（完成）

- `modules/report/conclusion.py`：六类 `question_tag` 中文结论模板（渠道对比、退货 TopN、趋势、分层、促销、复购）
- `ENABLE_LLM_OPS=true` 时可 GLM 润色结论；数字保真校验失败则回退模板（D-V2-CONCLUSION-01）
- 前端任务摘要优先展示中文结论，不再只用「已处理完毕」占位

### Added — V2-5 多轮指代（完成）

- `TaskSubmitReq.conversation_context`：前端携带最近 1–2 轮摘要（标签/约束/问句）
- Intent：同标签合并上轮约束；「同上 / 排除」可追问；「刚才那个渠道」等实体指代澄清（D-V2-FOLLOWUP-01）
- Trace 返回 `intent`；提问区提示可追问示例
- 修复：「排除 APP」不再误抽成 `channel IN ('APP')`；合并时从 IN 列表剔除；LLM SQL 须体现 exclude

### Added — V2-9 UI 标明 sandbox（完成）

- `GET /health` 返回 `sandbox_backend`（`mock`|`docker`）
- 页头实时徽章：Mock「演示占位数据」/ Docker「容器内对 CSV 真实计算」（D-V2-SANDBOX-UI-01）

### Added — V2-7 Langfuse 实接（完成）

- 任务级根 Trace + 节点 Span 父子挂接；结束 `flush`（D-V2-OBS-01）
- LLM `chat` 记为 `generation`（模型 / usage；不回灌完整 prompt）
- `/health.tracing_enabled`；`scripts/smoke_langfuse.py`

### Notes

- **决策 D-V2-LLM-01**：LLM 只产出文本；执行仍经 Safety + Docker/Mock；无 Key / 关闭开关 / 调用失败一律模板降级。
- **决策 D-V2-CONSTRAINT-01**：显式约束必须进 SQL。
- **决策 D-V2-DEDUP-01**：去冗余不砍掉 ≥2 子任务拓扑，只藏中间表 + 复用结果。
- **决策 D-V2-CONCLUSION-01**：结论以模板为底，LLM 润色须保留关键数字。
- **决策 D-V2-FOLLOWUP-01**：多轮由前端带上下文；不做服务端 session / 结果行实体消解。
- **决策 D-V2-SANDBOX-UI-01**：页头展示运行时沙箱模式；不做 daemon 探测。
- **决策 D-V2-OBS-01**：Langfuse Cloud；无 Key no-op；generation 不回灌全文 prompt。
- 默认 `ENABLE_LLM_OPS=true`；有 `LLM_API_KEY` 才会真调 GLM，否则自动模板降级。强制关：`.env` 设 `false` 后重启 API。
- 改后端后需**重启 API**；前端热更新即可。

---

## [1.5.0] — 2026-11-05

**实验完成（v1.5）**：Docker 真执行 + `mode: real` 评测 + GLM 连通冒烟。

### Added

- `docs/demo_script.md`：15 分钟演示脚本（R1）
- `sandbox/Dockerfile` + `sandbox/README.md`：镜像 `data-reflex-sandbox:3.11`
- `SANDBOX_BACKEND=mock|docker` 与 `modules/executor/factory.py`
- Docker SQL/Python runner：从 `/sandbox/data/*.csv` 装载后再执行模板 op
- `eval/run_eval.py --mode real`：DockerSandboxExecutor + fixtures（报表 `mode: real`）
- `scripts/smoke_llm.py`：OpenAI 兼容接口连通冒烟（当前默认模型 `glm-4-flash-250414`）

### Changed

- 默认 / 示例 `LLM_MODEL` 为 `glm-4-flash-250414`（需与智谱资源包适用模型一致）

### Notes

- Graph 意图/规划/生成仍为规则+模板；真数字来自 Docker 执行，不是「GLM 写 SQL」。
- 冒烟成功标准：HTTP 成功且返回 `choices`；个别模型 `reply` 可能为空，仍算连通通过。

---

## [1.0.0] — 2026-11-03

**演示完成（v1 关门）**：`tasks.md` T01–T45 全部验收通过。

### Added

- Plan-and-Execute 主链路（意图 → 规划 → 确认 → 生成 → 安全 → 执行 → 校验/自愈 → 报告 / 降级）
- 固定三表（orders / returns / users）与六类只读分析问题
- FastAPI + 单任务串行队列 + SSE；Vue 3 最小前端（上传、提问、确认、轨迹与结果）
- Mock LLM / 规则模板路径 + Mock 沙箱（默认可无 Docker、无 API Key 演示）
- Docker 一次性容器执行器实现（需本机 Docker 与预构建镜像才走真实执行）
- SQLite 任务 / Checkpoint / 审计；策略开关（planner / reflector / human_confirm / sandbox / fallback 等）
- 评测集与脚本：`eval/datasets/core.jsonl`、`run_eval.py`；Mock 报表标注 `mode: mock`
- 质量门禁：ruff / black / mypy / pytest（覆盖率门槛）；禁止业务 `print(`、裸 `except:`、宿主机执行生成代码

### Notes

- 默认演示下的结果数字来自 **Mock / 模板路径**，不是「真实 GLM + 真沙箱」金标准。
- 「实验完成」见 **1.5.0**。
