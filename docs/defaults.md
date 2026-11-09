# defaults.md — v1 实现默认值契约

> 配置项集中在 `src/data_reflex/config/settings.py` 读取；本文件锁定默认值与环境约定，避免节点内魔法数字。  
> 字段与三表契约见 [`schema.md`](schema.md)。

<a id="D-DEFAULTS-01"></a>
> **【实现决策 D-DEFAULTS-01｜2026-10-25】**  
> **决定：** 将超时、限额、敏感字段、沙箱镜像、评测时钟、图表库、Windows 验收等价命令集中写入本文件；代码只读配置，禁止节点内散落魔法数字。  
> **原因：** `tasks.md`/`design.md` 多处引用限额但未给统一默认值表；若不集中锁定，各模块会各自硬编码，策略消融与复现评测会失真。  
> **依据：** [risk.md](risk.md#risk-summary) 已确认的人工确认超时语义、沙箱边界、敏感字段脱敏要求；开发机约定见 [§9](defaults.md#9-windows--powershell-环境约定)。

<a id="D-DEFAULTS-02"></a>
> **【实现决策 D-DEFAULTS-02｜2026-10-25】**  
> **决定：** 默认沙箱镜像标签定为 `data-reflex-sandbox:3.11`（配置键 `sandbox_image`），基础层仍为 `python:3.11-slim` 预构建缓存。  
> **原因：** 运行时直接拉公共 `python:3.11-slim` 无法保证预装 `pandas`/`duckdb` 与禁网离线可用；预构建本地标签可复现，拉取失败应显式报错而非静默降级。  
> **依据：** [本文 §5](defaults.md#5-沙箱镜像) 与 [ADR-003](design.md#ADR-003)（宿主机禁止执行生成代码；不可用返回 `SandboxUnavailableError`）。

<a id="D-ENV-01"></a>
> **【实现决策 D-ENV-01｜2026-10-25】**  
> **决定：** 本机开发默认使用清华 PyPI 镜像安装依赖（`pip` index-url → `https://pypi.tuna.tsinghua.edu.cn/simple`）。  
> **原因：** 默认境外 PyPI 在本环境多次超时/卡住，导致 T02 `pip install -e ".[dev]"` 无法验收；换国内镜像后安装与 `import data_reflex` 通过。  
> **依据：** 用户明确要求更换国内源；镜像只影响获取速度，不改变依赖版本约束（仍以 [`pyproject.toml`](../pyproject.toml) 为准）；[T02](tasks.md#T02)。

## 1. 策略开关默认值

| 配置键 | 默认 | 说明 |
| :--- | :--- | :--- |
| `enable_planner` | `True` | 关闭时走单步试探执行（见 [D-FLAG-01](tasks.md#D-FLAG-01)） |
| `enable_reflector` | `True` | 关闭时失败不自愈（见 [D-FLAG-01](tasks.md#D-FLAG-01)） |
| `enable_sandbox` | `True` | 关闭仅评测；仍须一次性容器（见 [D-DOCKER-01](tasks.md#D-DOCKER-01)） |
| `enable_human_confirm` | `True` | 关闭则跳过计划确认；危险拦截仍生效（见 [D-FLAG-01](tasks.md#D-FLAG-01)） |
| `enable_fallback_report` | `True` | 模型彻底不可用时输出静态诊断 |
| `enable_llm_ops` | `True` | v2-1/v2-4：Graph 内调 GLM（GenerateOp/澄清/Reflect/结论润色）；无 Key 或失败仍走模板 |

## 2. 限额与超时

| 配置键 | 默认 | 说明 |
| :--- | :--- | :--- |
| `max_retry_rounds` | `3` | 自愈轮次上限 |
| `max_llm_calls_per_task` | `20` | 单任务模型调用上限；优先于自愈轮次 |
| `plan_confirm_timeout_seconds` | `300` | 计划确认超时，超时按拒绝 |

> **【实现决策｜见 [D-CONFIRM-01](tasks.md#D-CONFIRM-01)】** 超时由 `PlanConfirmGate` 消费；详情见 [T23](tasks.md#T23)。
| `sandbox_timeout_seconds` | `60` | 单次容器执行超时 |
| `sandbox_memory_limit_mb` | `512` | 容器内存上限 |
| `sandbox_cpu_limit` | `1.0` | 容器 CPU 上限 |
| `max_upload_files` | `5` | 单次导入文件数上限 |
| `max_file_size_mb` | `20` | 单文件大小上限（MB） |

## 3. 新话题规则 `new_topic_rule`

固定规则（不可在节点内硬编码变体）：

1. `dataset_id` 与上一轮不同 → 清空上下文（`context_cleared=True`）。
2. 同数据集下：若当前请求映射到的六类问题标签与最近一轮标签不同，且无法解析对上轮结果的指代（如「再按…」「排除…」「同上」）→ 判定为新话题并隔离上轮上下文。
3. 同标签或可解析指代 → 保留最近 1–2 轮计划、结果、约束。
4. 指代无法解析 → `clarification_required=True`，不猜测执行。

> **【实现决策｜见 [D-CTX-01](tasks.md#D-CTX-01)】** 规则仅在 `ContextManager` 实现；词表与轮次键见 [T18](tasks.md#T18)。

## 4. 敏感字段名单

英文：`phone`、`mobile`、`email`、`id_card`、`address`  
中文同义：`手机`、`手机号`、`电话`、`邮箱`、`身份证`、`身份证号`、`地址`

日志与审计只记字段名不记值；错误信息截断数据值；前端按脱敏展示。

## 5. 沙箱镜像

| 项 | 约定 |
| :--- | :--- |
| 基础镜像 | `python:3.11-slim` |
| 运行时标签 | `data-reflex-sandbox:3.11`（见上 D-DEFAULTS-02） |
| 预装 | `pandas`；SQL 执行用 `duckdb` 或标准库/`sqlite3` 只读分析 |
| 不预装 | `matplotlib`（图表由前端渲染，容器内不画图） |
| 网络 | 禁用 |
| 数据挂载 | 只读 |
| 不可用 | 返回 `SandboxUnavailableError`，不降级宿主机 |

镜像名由配置项 `sandbox_image` 指定，预构建并本地缓存；拉取失败返回明确错误。

> **【实现决策｜见 [D-DOCKER-01](tasks.md#D-DOCKER-01)】** 运行时由 `DockerSandboxExecutor` 消费本表约定：缺镜像/缺 daemon → `SandboxUnavailableError`；`enable_sandbox=False` 仍用一次性容器。详情与原因见 [T13](tasks.md#T13)。

## 6. 前端图表

| 项 | 约定 |
| :--- | :--- |
| 库 | Apache ECharts |
| v1 类型 | 柱状图、折线图、表格 |
| 不支持类型 | 说明原因并给替代，不伪造 |

> **【实现决策｜见 [D-REPORT-01](tasks.md#D-REPORT-01)】** 后端 `ReportBuilder` 仅产出 `bar|line|table` 的 `ChartSpec`；不支持类型与空数据行为见 [T16](tasks.md#T16)。  
> **【实现决策｜见 [D-FE-01](tasks.md#D-FE-01)】** 前端 ECharts 渲染 bar/line；table 规格用 HTML 表；见 [T35](tasks.md#T35)。

## 7. 模型温度与评测时钟

| 场景 | 约定 |
| :--- | :--- |
| 演示 / 交互 | `temperature=0.1`（可配置） |
| 关键评测 | `temperature=0`，写入报表 |
| 评测固定时钟 | `2025-10-01T00:00:00+08:00`（`Asia/Shanghai`） |
| 相对时间 | 「上个月」等相对评测时钟解析，避免随真实日期漂移 |

> **【实现决策｜见 [D-REPRO-01](tasks.md#D-REPRO-01)】** `eval/run_eval.py` 将上述时钟/温度写入 `report.md`；指标与泄漏排除见 [T42](tasks.md#T42)。

## 8. 本地数据落盘路径

| 项 | 约定 |
| :--- | :--- |
| SQLite | `sqlite_path` 默认 `data/data_reflex.db` |
| 导入数据集副本 | `data/datasets/{dataset_id}/`（与库文件同属 `data/`；见 [D-IMPORT-01](tasks.md#D-IMPORT-01)） |
| CSV 编码探测顺序 | `utf-8-sig` → `utf-8` → `gb18030` → `gbk`（未指定 `file_encoding` 时） |

> **【实现决策｜见 [D-IMPORT-01](tasks.md#D-IMPORT-01)】** 数据集落盘与编码探测顺序的原因与依据写在 [T10](tasks.md#T10)；此处仅锁定路径/顺序，避免执行器与导入模块各写一套。

## 9. Windows / PowerShell 环境约定

本仓库主开发环境为 **Windows 10/11 + PowerShell**；Docker Desktop 必须可用（Linux 容器模式）。

| Unix 验收写法（tasks 原文） | Windows PowerShell 等价 |
| :--- | :--- |
| `test -d path` | `Test-Path -Path path -PathType Container` |
| `. .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| `python3.11 -m venv .venv` | `py -3.11 -m venv .venv`（或已在 PATH 的 `python -m venv .venv`） |
| `grep -q 'pat' file` | `Select-String -Path file -Pattern 'pat' -Quiet` |
| `test -f path` | `Test-Path -Path path -PathType Leaf` |

路径一律 `pathlib.Path`；挂载前转绝对路径。中文路径、空格路径需有测试覆盖。

<a id="质量门禁"></a>
质量门禁在 PowerShell 中可写为：

```powershell
ruff check src tests; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
black --check src tests; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
mypy src; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
pytest --cov=src/data_reflex
```

> **【实现决策｜见 [D-COV-01](tasks.md#D-COV-01)】** `fail_under=85` 写在 `pyproject.toml`；范围 `src/data_reflex`；详情见 [T39](tasks.md#T39)。
