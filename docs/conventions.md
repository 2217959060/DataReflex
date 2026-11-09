# conventions.md

## 1. 语言版本、类型注解与执行约束

### 1.1 运行环境

- 后端语言：Python 3.11+，不支持更低版本。
- 前端：Node.js 20+，Vue 3.5+（Composition API + `<script setup lang="ts">`）。
- 所有 Python 源文件头部声明：

```python
# -*- coding: utf-8 -*-
from __future__ import annotations
```

- 采用 `src` 包布局，安装命令 `pip install -e ".[dev]"`，全部使用绝对导入（`from data_reflex.xxx import yyy`），禁止相对导入。

### 1.2 类型注解要求

1. 所有公开函数、类方法、LangGraph 节点函数必须完整标注入参与返回值类型；私有方法建议补充。
2. 类型写法：
   - 可空类型用 `str | None`，禁止 `Optional[str]`；
   - 容器用内置泛型 `list[str]`、`dict[str, int]`，不从 `typing` 导入大写泛型；
   - 复杂结构体优先用 Pydantic `BaseModel`（开启 `extra="forbid"`）；
   - Graph 全局状态用 `TypedDict`，禁止用裸 `dict[str, Any]` 传递核心业务参数。
3. 禁止可变默认参数，如 `def func(items=[])`。
4. 开启 `mypy --strict`，不允许未解决的类型告警。

### 1.3 生成代码与隔离执行约束

1. 模型生成的查询语句或脚本是**不可信内容**，禁止在业务进程内直接执行，必须经安全校验后送入隔离执行器。
2. 隔离执行器不得提供“绕过沙箱直接执行”的后门；关闭隔离仅允许在评测环境中通过显式配置开启，且必须在一次性容器内运行。
3. 生成代码的内容不得写入业务日志原文，日志只记录操作类型、长度、校验结果和错误摘要。

### 1.4 代码格式

- 遵循 PEP-8；格式化 Black；静态检查 Ruff；行宽 88 字符。
- 缩进 4 空格，禁止 Tab。
- 导入分组顺序：标准库 → 第三方库 → 本地模块，组间空一行。

## 2. 命名规范

### 2.1 模块与文件

- Python 模块与文件：`snake_case`，如 `data_import`、`plan_validator`、`error_classifier`。
- Vue 组件文件：`PascalCase`，如 `TaskTrace.vue`、`ResultTable.vue`。
- 禁止中文文件名、空格或特殊字符；文件名不得与标准库模块重名。

### 2.2 类名与异常名

- 类名：`PascalCase`，如 `DataImporter`、`SandboxExecutor`。
- 自定义异常必须以 `Error` 结尾，如 `DataImportError`、`SafetyViolationError`。

### 2.3 函数与方法

- 函数与方法：`snake_case`，如 `parse_intent`、`classify_error`、`validate_result`。
- 私有方法：单下划线前缀，如 `_build_schema_summary`；禁止双下划线前缀。

### 2.4 变量与常量

1. 普通变量：`snake_case`，如 `dataset_id`、`sub_task`、`retry_count`。
2. 全局常量：全大写加下划线，如 `MAX_RETRY_ROUNDS`、`DEFAULT_TIMEOUT_SECONDS`。
3. 布尔变量：以 `is_` / `has_` / `can_` / `should_` 开头，如 `is_in_scope`、`has_cycle`、`should_retry`、`can_self_heal`。
4. 策略开关命名统一为 `enable_xxx`，如 `enable_planner`、`enable_reflector`、`enable_sandbox`、`enable_human_confirm`，集中定义在配置模块，禁止在业务代码中散落字符串。

### 2.5 数据模型字段

- 请求/响应/状态模型字段统一 `snake_case`，与 `design.md` 定义保持一致，禁止同一含义在不同模块用不同字段名。
- 枚举值统一小写下划线，如 `syntax`、`schema_semantic`、`runtime`、`result_anomaly`。

## 3. 目录结构

```text
data-reflex/
├── src/
│   └── data_reflex/
│       ├── main.py                  # CLI 与 Uvicorn 启动入口
│       ├── config/
│       │   └── settings.py          # 集中式配置读取（策略开关、阈值、资源限制）
│       ├── api/                     # FastAPI 路由、SSE 推送、统一异常处理
│       ├── graph_engine/
│       │   ├── nodes/               # 各 Graph 节点实现
│       │   └── builder.py           # 工作流组装与 Checkpointer 绑定
│       ├── modules/
│       │   ├── data_import/         # 文件解析、结构识别、关联提示
│       │   ├── intent_parse/        # 范围判断、澄清、约束提取
│       │   ├── plan/                # 子任务拆解与依赖排序
│       │   ├── plan_validate/       # 循环检测、缺失输入校验
│       │   ├── generate_op/         # 查询语句/脚本生成
│       │   ├── safety_check/        # 危险操作与越权字段拦截
│       │   ├── executor/            # Docker 沙箱执行与资源限制
│       │   ├── error_classify/      # 错误类型识别
│       │   ├── reflect/             # 自愈决策与熔断
│       │   ├── result_validate/     # 结果合理性与约束校验
│       │   ├── report/              # 结论、表格、图表汇总
│       │   └── audit/               # 审计事件持久化
│       ├── models/                  # Pydantic 请求/响应/状态模型
│       ├── utils/                   # 日志、通用工具
│       └── exceptions/              # 自定义异常集合
├── frontend/                        # Vue 3 + Vite 前端
├── eval/                            # 评测集、策略矩阵、批量评测脚本
│   ├── datasets/
│   └── run_eval.py
├── tests/                           # 单元与集成测试（镜像 src 结构）
├── docs/                            # spec / design / conventions / tasks
├── logs/                            # 本地日志（git 忽略）
├── .env                             # 本地环境变量（git 忽略）
├── .env.example                     # 环境变量模板（仅键名）
├── .gitignore
└── pyproject.toml
```

### 目录约束

1. 业务代码全部在 `src/data_reflex/`，前端在 `frontend/`，评测在 `eval/`。
2. 路径操作统一用 `pathlib.Path`，禁止 `os.path` 字符串拼接。
3. 单个 `.py` 或 `.vue` 文件不超过 800 行，超出按职责拆分。
4. 测试文件严格镜像被测模块路径，如 `tests/modules/test_safety_check.py`。

## 4. 错误处理规范

### 4.1 自定义异常体系

<a id="D-EXC-01"></a>
> **【实现决策 D-EXC-01｜2026-10-25】**  
> **决定：** 正式基类名为 `DataReflexError`；`tasks.md` T05 写的 `DataAgentError` 保留为**同对象别名**（`DataAgentError = DataReflexError`）。原文笔误 `DatareflexError` 纠正为 `DataReflexError`。另按 T05 补齐 `ContextManageError`、`TaskQueueError`（本节原列表未列全）。  
> **原因：** `conventions` 与 `tasks` 对基类命名不一致；若只保留一侧名字，另一侧验收/文档引用会断。别名零成本兼容两边，运行时 `isinstance`/`except` 行为一致。补齐两异常是因为上下文管理与单任务队列已在设计中，缺少专用异常会迫使调用方滥用泛型 `Exception`。  
> **依据：** 包名/产品名为 DataReflex；[T05](tasks.md#T05) 验收要求类名以 `Error` 结尾且支持 `raise ... from e`；实现见 [`errors.py`](../src/data_reflex/exceptions/errors.py)。

1. 全部业务异常定义在 `src/data_reflex/exceptions/`，统一继承 `DataReflexError(Exception)`，类名以 `Error` 结尾；历史名 `DataAgentError` 为别名，新代码优先写 `DataReflexError`。
2. 标准异常集合：
   - 基础：`DataReflexError`（别名 `DataAgentError`）
   - 导入与解析：`DataImportError`、`SchemaParseError`
   - 意图与规划：`IntentParseError`、`PlanBuildError`、`PlanValidateError`
   - 生成与安全：`OpGenerateError`、`SafetyViolationError`
   - 执行与自愈：`SandboxUnavailableError`、`ExecutionTimeoutError`、`ResourceLimitError`、`SelfHealMeltedError`
   - 结果与报告：`ResultValidateError`、`ReportBuildError`
   - 上下文与队列：`ContextManageError`、`TaskQueueError`
3. 异常转换必须保留原始异常链：

```python
raise PlanValidateError("规划存在循环依赖") from e
```

### 4.2 禁止裸 except

1. 严禁 `except:` 无类型捕获。
2. 优先捕获具体异常类型；顶层兜底仅允许 `except Exception as e:`，且必须记录完整堆栈（`exc_info=True`）并转换为业务异常或更新任务状态，**严禁静默吞掉**。
3. 沙箱执行失败、模型输出格式错误、结果校验异常这三类必须显式处理，不得用宽泛捕获掩盖。

### 4.3 异常与状态

1. 阻断性异常必须写入任务状态（错误类型、错误摘要、发生节点），供审计和报告使用。
2. 可预期降级（如结果校验标记异常但继续汇总、单子任务失败不阻断其他子任务）使用 `logger.warning()` 记录，不中断主流程。
3. 自愈熔断必须抛出或记录 `SelfHealMeltedError` 等价事件，不得静默退出循环。

## 5. 日志规范

> **【实现决策｜见 [D-LOG-01](tasks.md#D-LOG-01)】** 脱敏与上下文注入由 `utils/logger.py` 的 Filter 统一兜底；详情与原因见 [T08](tasks.md#T08)。

1. 统一使用 `src/data_reflex/utils/logger.py` 封装的标准库 `logging`，禁止业务代码使用 `print()`。
2. 每个模块顶部初始化模块级 logger：

```python
from data_reflex.utils.logger import get_logger, log_exception, summarize_op

logger = get_logger(__name__)
```

3. 业务上下文（`task_id`、`dataset_id`、`sub_task_id`、`audit_trace_id`）通过 `extra` 注入，禁止用 f-string 把大段动态变量硬编码进日志模板。
4. 日志内容约束：
   - 不记录生成代码原文、用户原始数据行、敏感字段值；生成操作只用 `summarize_op(op_type, op_content)` 记类型与长度；
   - 记录操作类型、长度、校验结果、错误类型、耗时；
   - 错误日志必须带完整堆栈：优先 `log_exception(...)`，或显式 `exc_info=True`。
5. 审计事件（规划完成、执行失败、自愈触发、熔断、人工拦截、任务终止、任务完成）必须写入审计存储，不能只写日志文件。

## 6. 配置与密钥管理

1. 严禁硬编码任何模型 API Key、数据库密码、沙箱镜像凭证到源码或测试文件。
2. `.env` 存本地真实配置，必须列入 `.gitignore`；`.env.example` 仅保留键名与默认值说明。
3. 全部配置集中在 `src/data_reflex/config/settings.py`（基于 `pydantic-settings`）读取，禁止在业务代码里散落读取环境变量。
4. 核心配置项必须由配置驱动，禁止魔法数字：
   - `enable_planner: bool`
   - `enable_reflector: bool`
   - `enable_sandbox: bool`
   - `enable_human_confirm: bool`
   - `max_retry_rounds: int`
   - `max_llm_calls_per_task: int`
   - `sandbox_timeout_seconds: int`
   - `sandbox_memory_limit_mb: int`
   - `sandbox_cpu_limit: float`
   - `max_upload_files: int`
   - `max_file_size_mb: int`
5. 策略开关只允许在配置层和评测入口修改，禁止在节点内部用条件分支硬编码“某次运行关闭某策略”。

## 7. Git 提交规范

### 7.1 分支策略

- `main`：稳定主分支，时刻可构建、可测试、可演示；禁止直接在 `main` 上提交未测试代码。
- 功能分支：`feature/plan-validate`、`feature/sandbox-executor`、`feature/self-heal-loop`。
- 修复分支：`fix/result-validate-empty`、`fix/sse-reconnect`。

### 7.2 Commit Message（Conventional Commits）

格式：`<type>(<scope>): <简短描述>`

可选 `type`：`feat` | `fix` | `docs` | `style` | `refactor` | `test` | `chore`

示例：

- `feat(plan): add dependency cycle detection`
- `feat(executor): limit sandbox network and memory`
- `feat(reflect): add circuit breaker for repeated errors`
- `test(safety): cover dangerous write rejection`

### 7.3 质量门禁

每次准备提交前必须执行并全部通过：

```bash
ruff check src tests && black --check src tests && mypy src && pytest
```

评测相关改动还需确认 `eval/run_eval.py` 可在 mock 模式下跑通，不消耗真实模型调用。

## 8. 禁止事项清单

1. 禁止裸 `except:` 无类型捕获，禁止静默吞掉异常。
2. 禁止硬编码 API Key、数据库凭证、本地绝对路径。
3. 禁止使用 `print()` 输出业务日志，提交前清除调试输出。
4. 禁止公开函数或 Graph 节点缺少类型注解，禁止绕过 `mypy --strict`。
5. 禁止单文件超过 800 行，模块必须单一职责。
6. 禁止使用相对导入。
7. 禁止在业务代码写死重试次数、超时、内存上限、并发度等魔法数字，全部从配置读取。
8. 禁止在业务进程内直接执行模型生成的代码；必须经安全校验并送入隔离执行器。
9. 禁止在隔离执行器中提供绕过沙箱的后门；关闭隔离仅允许评测环境显式配置，且必须在一次性容器内运行。
10. 禁止生成或执行删除、更新、覆盖、批量写入等写操作；v1 仅允许只读分析。
11. 禁止未经安全校验直接访问未授权字段。
12. 禁止在日志、审计记录、前端展示中输出生成代码原文、原始数据行或敏感字段值。
13. 禁止在业务代码中散落读取环境变量或散落策略开关字符串；策略开关必须集中在配置层。
14. 禁止在单元或集成测试中调用真实付费模型接口；必须使用 mock 隔离。
15. 禁止引入 `pyproject.toml` 或 `package.json` 未声明的第三方依赖。
16. 禁止为消融实验复制出第二套业务代码；必须通过配置开关切换，保证同一测试集和同一指标定义。
17. 禁止在 `main` 分支直接提交未通过质量门禁的代码。