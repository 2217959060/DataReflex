# DataReflex

面向固定结构化数据集的自然语言分析系统。

用户上传 **订单 / 退货 / 用户** 三表后，用中文提问；系统按 Plan-and-Execute 流程自动规划子任务、安全校验、在隔离沙箱中执行，失败可定向自愈，并输出结论与图表。模型彻底不可用且开启降级开关时，输出标注为「非分析结论」的静态诊断报告。

**v1 范围：** 只读分析；固定三表 + 六类问题；策略靠配置开关切换，不复制第二套业务代码。

## 架构简述

```
浏览器 (Vue 3 + ECharts)
    │  REST / SSE
FastAPI 单任务串行队列
    │
LangGraph 主工作流
  Intent → Plan → PlanValidate → HumanConfirm?
    → GenerateOp → SafetyCheck → Execute
    → ResultValidate / ErrorClassify → Reflect?
    → Report → FallbackReport?
    │
SQLite（任务 / Checkpoint / 审计） + Mock 或 Docker 沙箱
```

更细的模块与 ADR 见 `[docs/design.md](docs/design.md)`；字段契约见 `[docs/schema.md](docs/schema.md)`；默认值见 `[docs/defaults.md](docs/defaults.md)`。

## 固定三表与六类问题


| 逻辑表       | 样例文件                                                                       |
| --------- | -------------------------------------------------------------------------- |
| `orders`  | `[eval/datasets/fixtures/orders.csv](eval/datasets/fixtures/orders.csv)`   |
| `returns` | `[eval/datasets/fixtures/returns.csv](eval/datasets/fixtures/returns.csv)` |
| `users`   | `[eval/datasets/fixtures/users.csv](eval/datasets/fixtures/users.csv)`     |



| 问题类型     | 标签                 |
| -------- | ------------------ |
| 退货率 TopN | `return_rate_topn` |
| 渠道对比     | `channel_compare`  |
| 时间趋势     | `time_trend`       |
| 用户分层对比   | `segment_compare`  |
| 促销与非促销对比 | `promo_compare`    |
| 复购率      | `repurchase_rate`  |


超范围请求会被拒绝；自然语言约束无法转成显式条件时会要求澄清，不猜测执行。危险写操作与越权字段在执行前拦截，不进入「人工放行」。

## 环境安装

要求：**Python 3.11+**、**Node.js 18+**（前端）。Docker 仅在走真实沙箱镜像时需要；API 默认可用 Mock 沙箱演示。

```powershell
# 仓库根目录
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 建议清华源（见 docs/defaults.md D-ENV-01）
python -m pip install -U pip
pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple

# 前端
cd frontend
npm install
cd ..
```

Linux / macOS 将激活命令换成 `source .venv/bin/activate`，其余同理。

## `.env` 配置

复制示例并按需修改（勿提交真实密钥）：

```powershell
Copy-Item .env.example .env
```

常用变量（完整默认值见 `[docs/defaults.md](docs/defaults.md)` 与 `[src/data_reflex/config/settings.py](src/data_reflex/config/settings.py)`）：


| 变量                                           | 含义                                        |
| -------------------------------------------- | ----------------------------------------- |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 真实模型（演示主路径可用规则引擎 + Mock，可不配） |
| `ENABLE_LLM_OPS` | v2-1/v2-4：Graph 内调 GLM 生成/澄清/自愈/结论润色（默认 `true`；无 Key 或失败回退模板） |
| `ENABLE_PLANNER`                             | 规划开关                                      |
| `ENABLE_REFLECTOR`                           | 自愈开关（`enable_reflector`）                  |
| `ENABLE_SANDBOX`                             | 沙箱策略语义；关仅评测，仍须容器路径                        |
| `ENABLE_HUMAN_CONFIRM`                       | 计划人工确认                                    |
| `ENABLE_FALLBACK_REPORT`                     | 降级报告开关（`enable_fallback_report`）          |
| `SQLITE_PATH`                                | SQLite 路径，默认 `data/data_reflex.db`        |
| `SANDBOX_BACKEND`                            | `mock`（默认演示）或 `docker`（真实执行，需 daemon+镜像）  |
| `SANDBOX_IMAGE`                              | Docker 镜像标签，默认 `data-reflex-sandbox:3.11` |
| `EVAL_FIXED_CLOCK` / `EVAL_TIMEZONE`         | 评测固定时钟                                    |




## 启动命令

两个终端：

```powershell
# 终端 1 — API（仓库根目录）
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "src"
# 演示请勿加 --reload（改 .env 会重启并清空进行中的任务）
uvicorn data_reflex.main:app --host 127.0.0.1 --port 8001
```

```powershell
# 终端 2 — 前端
cd frontend
npm run dev
```

**打开哪个网址（不要搞混）：**


| 地址                                                 | 用途                                        |
| -------------------------------------------------- | ----------------------------------------- |
| `http://127.0.0.1:5173`（或 `http://localhost:5173`） | **前端工作台**——日常演示、上传、提问都用这个                 |
| `http://127.0.0.1:8001/docs`                       | 后端 Swagger，可单独试 API                       |
| `http://127.0.0.1:8001/`                           | **没有网页**，返回 JSON `404` 是正常现象（API 根路径未挂页面） |
| `http://127.0.0.1:8001/health`                     | 健康检查，应返回 `{"status":"ok"}`                |


前端已把 `/api` 代理到 `:8001`。若改了 `vite.config.ts`，请重启 `npm run dev`。先访问 `/health` 确认是 DataReflex（返回 `{"status":"ok"}`）；若误连到其它占用端口的服务，会出现任务一直排队、`/trace` 404。

`data/data_reflex.db` 初始可以是空表；前端成功导入/提交任务后，再用 DB Browser 刷新才能看到行。

建议首次演示：上传 `eval/datasets/fixtures/` 下 **三份 CSV**（`orders` / `returns` / `users`），提问「对比 APP 和官网 渠道销售额」，走完上传 → 提问 →（可选）计划确认 → 轨迹 / 结果表图。

质量门禁（可选）：

```powershell
ruff check src tests
black --check src tests
mypy src
pytest --cov=src/data_reflex
```



## 评测命令

分层评测集：`[eval/datasets/core.jsonl](eval/datasets/core.jsonl)`（60 条）。批跑脚本：

```powershell
python eval/run_eval.py --mode mock --max-cases 5 --strategy-matrix planner=on,off
```

- 产出：`eval/results/report.md`、`eval/results/metrics.csv`（报表含 `mode: mock`）。
- v1 策略矩阵**仅允许** `planner` / `reflector`（例如 `planner=on,off;reflector=on,off`）；传入 `sandbox` 等其它键会拒绝。
- 标记 `has_leakage` 的用例会出现在 `leakage_cases` 且不计入指标分母。



## 策略开关说明


| 开关                       | 默认  | 关闭时行为                   |
| ------------------------ | --- | ----------------------- |
| `enable_planner`         | 开   | 单步试探（`st-probe`），仍走同一主图 |
| `enable_reflector`       | 开   | 失败不进入 Reflect 自愈，直达报告   |
| `enable_sandbox`         | 开   | 仅评测语义；**仍不**在宿主机执行生成代码  |
| `enable_human_confirm`   | 开   | 跳过计划挂起；**危险拦截仍生效**      |
| `enable_fallback_report` | 开   | 关闭则模型不可用时不生成降级报告        |


开关经配置 / 任务提交时的 `strategy_flags` 注入，与评测矩阵共用同一 Graph 路径。

## 降级报告说明

当分析链路判定模型彻底不可用（或等价熔断条件），且 `enable_fallback_report=True` 时，进入 `Node_FallbackReport`：

- 输出**静态诊断**（数据概况、失败节点、影响范围、可手工排查建议、已完成子任务摘要等）。
- 文首明确标注 **「非分析结论」**，**不**生成新的可执行查询/脚本，**不**虚构分析结果。
- 关闭该开关则不写降级报告体。

实现见 `modules/report/fallback.py` 与 Graph 节点 `Node_FallbackReport`。

## 文档索引

日常优先看这三份即可；其余为契约深读，不必按链接扫完。


| 文档                                             | 内容                     |
| ---------------------------------------------- | ---------------------- |
| `[CHANGELOG.md](CHANGELOG.md)`                 | **已发布**版本记事            |
| `[docs/roadmap.md](docs/roadmap.md)`           | 规划中事项；含 v2 backlog 与面试优先排序 |
| `[docs/demo_script.md](docs/demo_script.md)`   | 15 分钟演示脚本              |
| `[docs/handoff.md](docs/handoff.md)`           | 交接、完成定义、进度快照           |
| `[docs/tasks.md](docs/tasks.md)`               | 任务清单与实现决策（T01–T45 已关门） |
| `[docs/architecture.md](docs/architecture.md)` | 架构总览与 ADR 摘要           |
| `[docs/spec.md](docs/spec.md)`                 | 需求规格                   |
| `[docs/design.md](docs/design.md)`             | 设计与 ADR 全文             |
| `[docs/schema.md](docs/schema.md)`             | 三表字段契约                 |
| `[docs/defaults.md](docs/defaults.md)`         | 默认值与限额                 |




## License

MIT（见 `pyproject.toml`）。