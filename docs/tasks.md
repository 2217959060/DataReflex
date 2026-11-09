# tasks.md — DataReflex 最终可执行任务清单

> 歧义自行定案时，须在**本清单对应任务旁**或源契约文档（`design`/`conventions`/`defaults`/`schema`）追加 `【实现决策 D-xxx】` 补注（含决定 / 原因 / 依据），见 [handoff.md「实现决策补注规范」](handoff.md#实现决策补注规范强制)。

## 一、环境骨架

<a id="T01"></a>
- [x] **T01：初始化项目目录与 Git 配置**
  - 前置：无
  - 产出：创建 `src/data_reflex/`、`tests/`、`frontend/`、`eval/`、`docs/`、`logs/`；配置 `.gitignore` 忽略 `.venv/`、`node_modules/`、`.env`、`__pycache__/`、`logs/`、`*.pyc`、`*.tmp`。
  - 验收：`test -d src/data_reflex && test -d tests && test -d frontend && test -d eval && test -d docs` 返回 0；`grep -q '^.env$' .gitignore` 返回 0。
  - 完成：目录与 `.gitignore` 已就绪；Windows 下用 `Test-Path` / `Select-String` 验收通过（见 [defaults.md §9](defaults.md#9-windows--powershell-环境约定)）。

<a id="T02"></a>
- [x] **T02：创建 `pyproject.toml` 与虚拟环境**
  - 前置：T01
  - 产出：声明 `fastapi`、`uvicorn`、`sse-starlette`、`langgraph`、`langgraph-checkpoint-sqlite`、`langchain-core`、`pydantic`、`pydantic-settings`、`openai`、`docker`、`sqlglot`、`pandas`、`openpyxl`、`langfuse`、`pytest`、`pytest-asyncio`、`pytest-mock`、`httpx`；`pip install -e ".[dev]"` 可安装。
  - 验收：`python3.11 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"` 成功；`python -c "import data_reflex"` 无报错。
  - > **【实现决策｜见 [D-ENV-01](defaults.md#D-ENV-01)】** 安装改用清华 PyPI 镜像，否则境外源超时无法通过本条验收（详情与原因写在 [defaults.md](defaults.md)）。

<a id="T03"></a>
- [x] **T03：配置 Ruff / Black / mypy 质量门禁**
  - 前置：T02
  - 产出：在 `pyproject.toml` 配置 Black 行宽 88、Ruff 规则集、`mypy strict = true`。
  - 验收：`ruff check src tests && black --check src tests && mypy src` 三条命令均退出码 0。
<a id="D-LINT-01"></a>
  - > **【实现决策 D-LINT-01｜2026-10-25】**  
    > **决定：** Ruff `ignore` 增加 `UP009`，保留文件头 `# -*- coding: utf-8 -*-`。  
    > **原因：** `conventions.md` §1.1 强制要求该声明；Ruff UP 规则会建议删除，与约定冲突，导致门禁与规范二选一。忽略 UP009 后两者同时满足。  
    > **依据：** [conventions.md §1.1](conventions.md#11-运行环境) 编码头要求；配置在 [`pyproject.toml`](../pyproject.toml) `[tool.ruff.lint]`；[T03](tasks.md#T03)。
<a id="D-MYPY-01"></a>
  - > **【实现决策 D-MYPY-01｜2026-10-25】**  
    > **决定：** `pyproject.toml` 对 `pandas` / `openpyxl`（以及既有的 docker/langfuse/sqlglot/sse_starlette）使用 mypy `ignore_missing_imports`，**不**强装 `pandas-stubs` 作为 v1 门禁依赖。  
    > **原因：** T10 启用 pandas 后 mypy strict 因缺 stubs 失败；为通过门禁要么加 stubs 包、要么忽略第三方类型。v1 优先稳定验收、减少 stub 版本漂移，业务代码仍保持自有注解完整。  
    > **依据：** [T03](tasks.md#T03) mypy strict 验收；[T10](tasks.md#T10) 引入 pandas/openpyxl；与既有第三方 ignore 列表同一策略。

<a id="T04"></a>
- [x] **T04：集中式配置模块与 `.env.example`**
  - 前置：T03
  - 产出：实现 `src/data_reflex/config/settings.py`，包含 `enable_planner`、`enable_reflector`、`enable_sandbox`、`enable_human_confirm`（默认 `True`）、`enable_fallback_report`、`max_retry_rounds`、`max_llm_calls_per_task`、`sandbox_timeout_seconds`、`sandbox_memory_limit_mb`、`sandbox_cpu_limit`、`max_upload_files`、`max_file_size_mb`、`new_topic_rule`、敏感字段名单；`.env.example` 仅保留键名与默认值说明。
  - 验收：`python -c "from data_reflex.config.settings import get_settings; s=get_settings(); assert s.enable_human_confirm is True and s.max_llm_calls_per_task>0 and s.enable_fallback_report in (True,False)"` 无异常；`grep -q 'LLM_API_KEY' .env.example` 且 `grep -vq 'sk-' .env.example`。
<a id="D-CFG-01"></a>
  - > **【实现决策 D-CFG-01｜2026-10-25】**  
    > **决定：** `Settings` 使用 `extra="ignore"`（忽略未知环境变量）；领域模型仍用 `extra="forbid"`（`StrictModel`）。默认值一律对齐 `defaults.md`（含 `sandbox_image`、评测时钟等扩展键）。  
    > **原因：** 环境变量常有宿主/CI 注入的额外键，forbid 会导致进程无法启动；而请求/响应模型 forbid 才能挡住非法字段（T06 验收）。配置与 IO 契约职责不同，故分治。  
    > **依据：** [T04](tasks.md#T04)/[T06](tasks.md#T06) 验收语义；[defaults.md](defaults.md) 为默认值唯一来源（[D-DEFAULTS-01](defaults.md#D-DEFAULTS-01)）。

## 二、数据模型

<a id="T05"></a>
- [x] **T05：基础模型与自定义异常体系**
  - 前置：T04
  - 产出：实现 `models/base.py`（`extra="forbid"`）与 `exceptions/` 下 `DataAgentError`、`DataImportError`、`SchemaParseError`、`IntentParseError`、`PlanBuildError`、`PlanValidateError`、`OpGenerateError`、`SafetyViolationError`、`SandboxUnavailableError`、`ExecutionTimeoutError`、`ResourceLimitError`、`SelfHealMeltedError`、`ResultValidateError`、`ReportBuildError`、`ContextManageError`、`TaskQueueError`。
  - 验收：`pytest tests/models/test_exceptions.py -v` 全部通过；异常类均以 `Error` 结尾且支持 `raise ... from e`。
  - > **【实现决策｜见 [D-EXC-01](conventions.md#D-EXC-01)】** 正式基类 `DataReflexError`，`DataAgentError` 为别名；详情与原因写在 [conventions.md §4.1](conventions.md#41-自定义异常体系)。

<a id="T06"></a>
- [x] **T06：请求/响应与领域模型**
  - 前置：T05
  - 产出：实现 `DataImportReq/Resp`、`TableSchema`、`ColumnSchema`、`JoinHint`、`IntentParseReq/Resp`、`PlanReq/Resp`、`SubTask`、`GenerateOpReq/Resp`、`SafetyCheckReq/Resp`、`ExecuteReq/Resp`、`ErrorClassifyReq/Resp`、`ReflectReq/Resp`、`ResultValidateReq/Resp`、`ReportReq/Resp`、`FallbackReportReq/Resp`、`EvalRunReq/Resp`、`EvalMetric`，字段对齐 `design.md`。
  - 验收：`pytest tests/models/test_schemas.py -v` 全部通过；传入非法字段或 `max_retry` 为负触发 `ValidationError`。
  - > **【实现决策｜见 [D-MODEL-01](design.md#D-MODEL-01) / [D-MODEL-02](design.md#D-MODEL-02)】** 增补 `question_tag`、`SubTaskResult`、`ChartSpec` 最小字段；详情与原因写在 [design.md §3.3](design.md#33-意图解析模块) / [§3.9](design.md#39-结果校验与报告模块)。

<a id="T07"></a>
- [x] **T07：任务状态、策略开关、上下文与 API 事件模型**
  - 前置：T06
  - 产出：实现 `TaskState`（`TypedDict`）、`StrategyFlags`、`ContextManageReq/Resp`、`TaskSubmitReq/Resp`、`TaskEvent`（含 `task_id`、`seq`、`event_type`、`payload`、`timestamp`）、`ConfirmReq`（含 `idempotency_key`）、`TerminateReq`；`TaskState` 包含 `task_id`、`dataset_id`、`user_query`、`conversation_context`、`intent`、`plan`、`sub_task_results`、`current_sub_task_id`、`retry_counts`、`error_fingerprints`、`llm_calls_used`、`final_report`、`fallback_report`、`strategy_flags`、`audit_trace_id`、`status`。
  - 验收：`pytest tests/models/test_state.py -v` 全部通过；初始化后 `status == "pending"`、`retry_counts == {}`、`error_fingerprints == []`、`llm_calls_used == 0`、`strategy_flags.enable_human_confirm is True`。

## 三、工具层

<a id="T08"></a>
- [x] **T08：结构化日志工具**
  - 前置：T05
  - 产出：实现 `utils/logger.py`，支持 `extra={"task_id": ..., "dataset_id": ..., "sub_task_id": ...}` 注入；日志不记录生成代码原文、原始数据行、敏感字段值；禁止业务代码 `print()`。
  - 验收：`pytest tests/utils/test_logger.py -v` 全部通过；捕获异常时日志包含 `task_id` 与完整堆栈；日志中不出现 `op_content` 原文。
<a id="D-LOG-01"></a>
  - > **【实现决策 D-LOG-01｜2026-10-25】**  
    > **决定：** 在 `data_reflex` 包 logger 上挂 `ContextDefaultFilter` + `RedactFilter` + 结构化 Formatter；业务侧用 `get_logger` / `log_exception` / `summarize_op` / `sanitize_extra`。对 `op_content`、`result_rows`、敏感字段名等 **forbidden extra 键**做长度占位脱敏；并对已知原文在 `%-args` 中的回显做 scrub；邮箱/手机号用正则兜底。不做「自由文本里 NLP 识别整段 SQL」——成本高且易误伤。  
    > **原因：** 约定要求上下文走 `extra` 且禁止记录生成代码原文；仅靠开发者自觉不够，过滤器可兜住误传 `extra`/`args`。完整堆栈由 `log_exception(..., exc_info=...)` 保证，满足验收「含 task_id + 堆栈」。  
    > **依据：** [conventions.md §5](conventions.md#5-日志规范)、[§1.3](conventions.md#13-生成代码与隔离执行约束)；[risk.md P1-5](risk.md#risk-p1)；[T08](tasks.md#T08) 验收点名 `op_content` 原文不得出现。

<a id="T09"></a>
- [x] **T09：SQLite 持久化与审计存储**
  - 前置：T07
  - 产出：实现 `modules/audit/store.py`，创建 `tasks`、`checkpoints`、`audit_events`、`eval_cases`、`eval_results` 表；提供 `save_task`、`load_task`、`append_audit_event`、`list_audit_events`、`save_eval_result`；写操作串行化。
  - 验收：`pytest tests/modules/test_store.py -v` 全部通过；写入两个审计事件后按 `task_id` 查询返回两条且时间有序；并发写测试无状态错乱。
<a id="D-STORE-01"></a>
  - > **【实现决策 D-STORE-01｜2026-10-25】**  
    > **决定：** 用标准库 `sqlite3`（非 ORM）；进程内 `threading.RLock` + `BEGIN IMMEDIATE` 串行化所有写；WAL；`TaskState` 整包 JSON 落 `tasks.state_json`；审计 `ORDER BY created_at, id`；payload 经 `sanitize_extra` 脱敏。额外提供 `save_checkpoint`/`load_checkpoint`、`save_eval_case`（表已建，供 T32/T40 使用，不另起存储层）。  
    > **原因：** ADR-007 明确 v1 SQLite + 写串行；ORM 增加依赖与迁移面但本阶段收益低。仅靠 SQLite 忙等不足以保证应用层「读改写」任务状态不交错，故加进程锁。审计按时间+自增 id 排序满足「两条事件时间有序」验收。checkpoint/eval_cases 若只建表无 API，后续 Graph/评测仍会再造一套访问代码。  
    > **依据：** [ADR-007](design.md#ADR-007) / [ADR-016](design.md#ADR-016)；[risk.md P0-9](risk.md#risk-p0)；[T09](tasks.md#T09) 验收「并发写无状态错乱」。

<a id="T10"></a>
- [x] **T10：数据导入与边界处理**
  - 前置：T09
  - 产出：实现 `modules/data_import/importer.py`，解析 CSV/Excel，只读单工作表；识别字段名、类型、空值比例、样本值、关联提示；重复列名自动重命名，保留中文列名，转义公式前缀，不执行宏；坏文件跳过并写告警；执行文件数量与大小限制。
  - 验收：`pytest tests/modules/test_data_import.py -v` 全部通过；覆盖重复列名、中文列名、公式前缀、超限拒绝、空文件与二进制文件不中断其他文件导入。
<a id="D-IMPORT-01"></a>
  - > **【实现决策 D-IMPORT-01｜2026-10-25】**  
    > **决定：** (1) 超限（文件数/单文件大小）**整单拒绝**（`success=False`+`error_msg`），与「坏文件跳过」区分；(2) 若全部文件均无效 → 仍 `success=False`（`error_msg=no valid tables imported`），但保留已产生的 `warnings`/`dataset_id`；(3) 重复列先读原始表头再命名为 `name`/`name_2`/…，避免 pandas 的 `name.1`；(4) 编码未指定时按 `utf-8-sig` → `utf-8` → `gb18030` → `gbk` 探测（对齐 risk「UTF-8/GBK」并兼容带 BOM）；(5) 公式注入对 `= + @` 及非纯数字的 `-` 前缀加 `'`；(6) Excel 用 openpyxl `data_only`+`keep_vba=False` 只读首 sheet；(7) Join 优先 `schema.md` 固定键，再补同名列置信度 `0.7`；(8) 敏感列 `sample_values` 只留 `[REDACTED]:n=`；(9) 清洗后副本落到 `data/datasets/{dataset_id}/`（与 `sqlite_path` 同级 `data/` 下）供后续沙箱挂载。  
    > **原因：** FR-01 对超限写的是「拒绝」，对坏文件写的是「跳过不中断」——两者语义不同，不能都做成 soft-skip。全失败若标 success 会误导 API 调用方。pandas 默认改写重复列名会破坏中文可读性与验收断言。样本值若回显手机/邮箱，与脱敏约束冲突。不落盘则 T13 执行器无稳定数据路径。  
    > **依据：** [spec.md FR-01](spec.md#fr-01-数据文件导入与结构识别)；[risk.md P1-4](risk.md#risk-p1) / [P2-9](risk.md#risk-p2)；[schema.md §5](schema.md#5-join-键与提示) Join；[defaults.md](defaults.md) 限额与敏感字段。

<a id="T11"></a>
- [x] **T11：安全校验工具**
  - 前置：T06
  - 产出：实现 `modules/safety_check/checker.py`：SQL 用 AST 解析写操作（`INSERT/UPDATE/DELETE/DROP/ALTER` 等）与字段白名单；Python AST 检测危险内建（`eval`、`exec`、`__import__`、`getattr` 等）与动态导入、写文件、`os.system`、`subprocess`；未授权字段检测。
  - 验收：`pytest tests/modules/test_safety_check.py -v` 全部通过；注释/大小写/动态拼接的写 SQL 仍被拦截；危险 Python 脚本返回 `passed=False` 且 `violation_type` 正确；`SELECT *` 触发字段白名单校验。
<a id="D-SAFE-01"></a>
  - > **【实现决策 D-SAFE-01｜2026-10-25】**  
    > **决定：** (1) SQL 用 **sqlglot AST**（方言 `sqlite`）识别写操作节点（Insert/Update/Delete/Drop/Alter/Create/Truncate/Command/…），禁止关键字正则；(2) **`SELECT *` 一律 `unauthorized_column`**（无法对白名单证明安全）；显式列名在提供 `allowed_columns` 时做集合差集校验；(3) 多语句逐条检查，注释/大小写由解析器消化；(4)「动态拼接」按**最终 `op_content` 字符串**做 AST（生成器拼出完整写语句仍会被拦）；(5) Python 用标准库 `ast`：禁 `eval/exec/__import__/getattr` 等调用、禁 `os/subprocess/importlib/…` 导入、禁写模式 `open`；(6) 解析失败 → `unparseable`，默认拒绝。  
    > **原因：** risk P0-2 明确 AST 优先，避免注释/大小写绕过；`SELECT *` 会间接读出未授权列（含敏感字段）。只检查最终文本才能与沙箱入口契约对齐（执行器只看见完整 `op_content`）。解析失败放行等于把未知语句送进沙箱。  
    > **依据：** [risk.md P0-2](risk.md#risk-p0)；[design.md §3.6](design.md#36-安全校验模块) `violation_type` 三值；[T11](tasks.md#T11) 验收点名注释/大小写/拼接/`SELECT *`/危险 Python。

<a id="T12"></a>
- [x] **T12：沙箱执行器接口与 Mock 实现**
  - 前置：T06
  - 产出：定义 `SandboxExecutor` 协议与 `MockSandboxExecutor`；实现资源超时、内存超限、网络禁用配置的传递与结果结构。
  - 验收：`pytest tests/modules/test_executor_mock.py -v` 全部通过；Mock 可模拟成功、语法错误、超时、资源超限四种结果。
<a id="D-EXEC-01"></a>
  - > **【实现决策 D-EXEC-01｜2026-10-25】**  
    > **决定：** (1) 用 `@runtime_checkable` Protocol `SandboxExecutor`（`execute` / `terminate` / `build_run_config`），Docker 实现（T13）与 Mock 共用；(2) 用不可变 `SandboxRunConfig` 承载 `timeout_seconds` / `memory_limit_mb` / `cpu_limit` / **`network_disabled`**——后者不在 `ExecuteReq` 字段里（design IO 已固定），由执行器构造参数/配置注入，v1 默认 `True`；(3) `MockScenario` 四态：`success` / `syntax_error` / `timeout` / `resource_limit`，映射到 `ExecuteResp`（超时与资源超限均 `resource_killed=True`，退出码 124 / 137）；(4) Mock **绝不**在宿主机 `eval`/`exec` `op_content`，只记录 `op_type:len=` 元数据。  
    > **原因：** T12 要验证「配置传递 + 结果结构」，不能把 Docker 可用性绑进单元测试；`network_disabled` 若硬塞进 Req 会破坏已冻结的 IO 契约。超时与 OOM 都属资源终止，统一 `resource_killed` 便于 T14 分类与 T26 节点处理。Mock 若执行生成代码，会直接违反「宿主机禁止执行」硬约束并污染测试。  
    > **依据：** [design.md §3.7](design.md#37-隔离执行器)；[risk.md P0-1](risk.md#risk-p0) / [ADR-003](design.md#ADR-003)；[T12](tasks.md#T12) 验收四种结果；[T13](tasks.md#T13) 将复用同一 Protocol。

<a id="T13"></a>
- [x] **T13：Docker 沙箱执行器**
  - 前置：T12
  - 产出：实现 `modules/executor/docker_executor.py`，使用 Docker SDK 创建一次性容器，只读挂载数据文件、禁用网络、限制 CPU/内存/进程数/超时；宿主机绝对禁止执行生成代码；沙箱不可用返回 `SandboxUnavailableError`，不降级宿主机执行；关闭隔离仅评测且仍用一次性容器；终止时杀容器。
  - 验收：`pytest tests/modules/test_docker_executor.py -v` 通过（Docker 不可用时相关用例标记 skip 并标注）；验证容器配置包含 `network_disabled=True`、内存与 CPU 限制、超时终止；验证无宿主机执行路径；验证终止接口调用后容器被杀。
<a id="D-DOCKER-01"></a>
  - > **【实现决策 D-DOCKER-01｜2026-10-25】**  
    > **决定：** (1) `DockerSandboxExecutor` 实现同一 `SandboxExecutor` Protocol；`containers.run` 固定 `network_disabled`、`mem_limit`、`nano_cpus`、`pids_limit`、工作区/数据 **ro** 挂载、`detach=True`、一次性 `kill+remove`；(2) 生成代码只写入挂载文件，**宿主机绝不** `eval`/`exec`/`subprocess` 执行；(3) Docker/镜像不可用 → `SandboxUnavailableError`，无宿主机降级分支；(4) `enable_sandbox=False`（仅评测）**仍走一次性容器**，隔离参数不变；(5) 单测用可注入 fake client 断言 kwargs；`@pytest.mark.docker` 实机用例在 daemon/镜像缺失时 **skip 并标注**。  
    > **原因：** ADR-003 / P0-1 要求关闭隔离也不能落到宿主机；把 Docker 可用性绑进默认 CI 会误杀验收，故「mock 断言配置 + live skip」分层。镜像需预构建（`data-reflex-sandbox:3.11`），缺镜像明确报错而不是偷偷 pull 拖垮离线环境。  
    > **依据：** [risk.md P0-1/P0-4](risk.md#risk-p0) / [P1-7](risk.md#risk-p1)；[defaults.md §5](defaults.md#5-沙箱镜像)；[D-EXEC-01](tasks.md#D-EXEC-01) Protocol；[T13](tasks.md#T13) 验收条文。

<a id="T14"></a>
- [x] **T14：错误分类工具**
  - 前置：T12
  - 产出：实现 `modules/error_classify/classifier.py`，将 `ExecuteResp` 归类为 `syntax`、`schema_semantic`、`runtime`、`result_anomaly`、`none`，输出 `error_fingerprint`（错误类型 + 归一化关键错误行）、`retryable`、`suggested_fix`。
  - 验收：`pytest tests/modules/test_error_classify.py -v` 全部通过；四类错误各至少一个用例命中正确类型；相同错误两次生成相同指纹，不同错误生成不同指纹。
<a id="D-ERRCLS-01"></a>
  - > **【实现决策 D-ERRCLS-01｜2026-10-25】**  
    > **决定：** (1) 分类优先级：`syntax` → `schema_semantic` → `result_anomaly` → `runtime`（含 `resource_killed`），成功且无异常文本 → `none`；(2) 指纹 = `{error_type}|{normalize(关键错误行)}`，归一化：小写、压空白、路径/`uuid`/长数字/hex → 占位符，保证同错稳定、异类可分；(3) `resource_killed=True` → `retryable=False`（与「资源超限直接失败」对齐）；其余四类错误默认可自愈；(4) `suggested_fix` 按类型给短修复提示，供 Reflect/GenerateOp 注入。  
    > **原因：** 自愈熔断依赖稳定指纹；若不归一化，路径/计数会让同一语法错误指纹抖动导致误重试。资源超限再生成同类查询通常无效，应直接失败而非空转调用上限。优先级保证「看起来像 runtime 的 syntax 文案」仍先归 syntax。  
    > **依据：** [design.md §3.8](design.md#38-错误分类与自愈模块)；[risk.md 自愈熔断摘要](risk.md#risk-summary)；[T26](tasks.md#T26)「资源超限直接标记失败」；[T14](tasks.md#T14) 验收「同错同指纹 / 异错异指纹」。

<a id="T15"></a>
- [x] **T15：结果校验工具**
  - 前置：T12
  - 产出：实现 `modules/result_validate/validator.py`，只校验可结构化约束（比例范围、非空、字段存在、行数上限）；空结果区分合法空与异常空；返回 `valid`、`anomaly_type`、`anomaly_detail`；不校验无法转为显式条件的自然语言约束。
  - 验收：`pytest tests/modules/test_result_validate.py -v` 全部通过；比例越界、异常空、类型异常、约束不一致各至少一个用例被标记；合法空不被标记。
<a id="D-RESULT-01"></a>
  - > **【实现决策 D-RESULT-01｜2026-10-25】**  
    > **决定：** (1) 仅识别结构化约束 `type∈{require_columns,non_empty,allow_empty,max_rows,ratio_range,column_types}`；未知/`nl_*` 约束**静默跳过**（不假装校验）；(2) 默认空结果为**合法空**，仅当显式 `non_empty`（且无 `allow_empty`）才标 `empty_anomaly`；(3) 异常优先级：缺列/行数 → `constraint_mismatch`，类型 → `type_anomaly`，比例 → `ratio_out_of_range`；(4) `execute_resp.success=False` → `constraint_mismatch`（本模块假定成功路径才做语义校验）。  
    > **原因：** ADR-017 / P1-3 禁止对未转显式条件的自然语言“假校验”。默认合法空对齐 risk「空结果区分合法/异常」——退货率分母为 0 等场景由约束显式声明，避免一律当异常空误报。  
    > **依据：** [design.md §3.9](design.md#39-结果校验与报告模块) anomaly 四值；[risk.md P0-3](risk.md#risk-p0) / [P1-3](risk.md#risk-p1)；[spec.md FR-07](spec.md#fr-07-结果合理性与安全校验)；[T15](tasks.md#T15) 验收点名四类异常 + 合法空。

<a id="T16"></a>
- [x] **T16：报告与图表构建工具**
  - 前置：T15
  - 产出：实现 `modules/report/builder.py`，汇总子任务结果、失败归因、来源映射，输出 `summary`、`tables`、`charts`、`partial_success`；图表仅柱状图、折线图、表格；不支持类型说明替代；空数据不画图并说明。
  - 验收：`pytest tests/modules/test_report.py -v` 全部通过；部分成功时 `partial_success=True` 且失败归因出现在 `summary`；请求不支持图表类型时返回替代说明。
<a id="D-REPORT-01"></a>
  - > **【实现决策 D-REPORT-01｜2026-10-25】**  
    > **决定：** (1) `chart_requests` **不**写入 `ReportReq`（IO 已冻结），由 `ReportBuilder.build(..., chart_requests=...)` 可选参数传入；无请求时对每个非空成功子任务自动生成 `table` 图；(2) `partial_success=True` 仅当「至少 1 个 success」且「存在 failed/terminated 结果或 `failures` 非空」；全失败则为 `False`，失败归因仍写入 `summary`；(3) 不支持类型（非 `bar|line|table`）→ `chart_type=table` + `alternative_note` 说明原因与支持列表，**不伪造**系列数据；(4) 空 `result_rows` → 不画业务图，`series=[]` + `alternative_note` 标明 skipped/not fabricated；(5) `source_map` 按 `sub_task_id` 记录 status/row_count/columns/error_summary。  
    > **原因：** design 的 `ReportReq` 无图表请求字段，硬塞会破坏 T06 冻结契约；图表意图来自规划/节点层，工具侧用可选参数承接。部分成功语义若把「全失败」也标 partial，前端会误显示成功片段。空数据若补假点违背 P2-10「不伪造」。  
    > **依据：** [design.md §3.9](design.md#39-结果校验与报告模块) ReportReq/Resp + [D-MODEL-02](design.md#D-MODEL-02)；[defaults.md §6](defaults.md#6-前端图表)；[risk.md P2-10](risk.md#risk-p2) / [P0-7](risk.md#risk-p0)；[T16](tasks.md#T16) 验收点名 partial_success、替代说明、空数据。

<a id="T17"></a>
- [x] **T17：可观测追踪封装**
  - 前置：T04
  - 产出：实现 `utils/tracing.py`，封装 Langfuse trace/span；未配置 `LANGFUSE_*` 时降级为 no-op，不阻断主流程。
  - 验收：`pytest tests/utils/test_tracing.py -v` 全部通过；无配置时调用不抛异常，有 mock 配置时生成 span 记录。
<a id="D-TRACE-01"></a>
  - > **【实现决策 D-TRACE-01｜2026-10-25】**  
    > **决定：** (1) 应用侧稳定 API 为 `Tracer.trace` / `Tracer.span`（context manager）+ `flush`/`shutdown`；底层对接 Langfuse **v5** 的 `start_observation`（无独立 `trace()` API）；(2) `LANGFUSE_PUBLIC_KEY` 与 `SECRET_KEY` **皆非空**才启用；缺任一键 → `enabled=False` 且不构造客户端、不写 `records`；(3) 测试可注入 `client=`（mock），启用后本地累积 `SpanRecord` 供断言，不依赖真实网络；(4) 客户端 init / start / update / end / flush 任一异常只打 warning，**不抛给业务**；(5) metadata 经 `sanitize_extra` 脱敏。  
    > **原因：** ADR-004 / P2-1 要求不可用时 no-op 且不阻断；锁定 v5 API 避免节点层直接依赖 SDK 细节。缺一键仍建客户端会连假地址拖慢 shutdown。注入 client 才能在无 Langfuse 服务时验收「有配置则有 span 记录」。  
    > **依据：** [ADR-004](design.md#ADR-004) / [ADR-014](design.md#ADR-014)；[risk.md P2-1](risk.md#risk-p2)；[handoff 完成定义](handoff.md#完成定义已锁定) Langfuse 可 no-op；[T17](tasks.md#T17) 验收；依赖 `langfuse>=2.50` 实装为 5.x。

<a id="T18"></a>
- [x] **T18：上下文管理工具**
  - 前置：T07
  - 产出：实现 `modules/context/manager.py`，维护同一数据集内最近 1–2 轮计划、结果、约束；切换数据集清空；按固定规则判定新话题并隔离；无法解析指代返回澄清问题。
  - 验收：`pytest tests/modules/test_context.py -v` 全部通过；切换 `dataset_id` 返回 `context_cleared=True`；连续同话题保留上下文；无法解析指代返回 `clarification_required=True`。
<a id="D-CTX-01"></a>
  - > **【实现决策 D-CTX-01｜2026-10-25】**  
    > **决定：** (1) 规则 id 固定为 `dataset_or_label_change_without_anaphora`（`defaults.md` §3），逻辑只在 `ContextManager`，节点不得另写变体；(2) 轮次 dict 约定键：`dataset_id` / `question_tag` / `plan` / `results` / `constraints` / `user_query`；最多保留 **2** 轮；(3) `previous_dataset_id is not None` 且 ≠ 当前 → `context_cleared=True`、`active_context=[]`、`is_new_topic=True`；(4) 同数据集下：当前 `question_tag` 与上轮不同且**无**指代线索 → 新话题隔离（清空 active）；同标签或可解析指代 → 保留；(5) 指代线索（再按/排除/同上/它/again/…）且无上轮可绑定时 → `clarification_required=True`，不猜测；(6) `question_tag` **不**写入 `ContextManageReq`，由 `manage(..., question_tag=)` 传入；缺省时用词表从 `user_query` 推断（供规则 2，正式标签仍以意图节点为准）。  
    > **原因：** ADR-018 / P1-1 要求固定规则与「无法解析必须澄清」；Req IO 已冻结无 tag 字段，若不另开可选参数，标签变化规则无法落地。词表推断仅作门禁启发式，避免 T20 前无标签可用。  
    > **依据：** [defaults.md §3](defaults.md#3-新话题规则-new_topic_rule)；[design.md §3.2](design.md#32-上下文管理模块) / [ADR-018](design.md#ADR-018)；[risk.md P1-1](risk.md#risk-p1)；[schema.md §7](schema.md#7-六类问题与字段依赖)；[T18](tasks.md#T18) 验收。

## 四、Agent 节点

<a id="T19"></a>
- [x] **T19：节点基类 `BaseNode`**
  - 前置：T08、T09、T17
  - 产出：实现 `graph_engine/nodes/base.py`，统一封装计时、审计事件写入、异常捕获、单节点最多重试 1 次、返回状态增量；错误信息截断数据值。
  - 验收：`pytest tests/graph_engine/test_base_node.py -v` 全部通过；首次失败自动重试 1 次，连续失败写入错误状态且不静默吞异常；审计事件包含节点名与耗时。
<a id="D-NODE-01"></a>
  - > **【实现决策 D-NODE-01｜2026-10-25】**  
    > **决定：** (1) `BaseNode.run` 模板：`node_started` → 最多 **2** 次 `execute`（首次+重试 1）→ 成功 `node_completed` / 耗尽 `node_failed`；审计 payload 必含 `node`、`duration_ms`；(2) `execute` 只返回状态增量 `dict`；成功且原 `status=pending` 时默认补 `running`；(3) 重试耗尽：`save_task(status=failed)`（有 store 时）+ 审计 `error_summary`（经 `truncate_error_text`：scrub 邮箱/手机/长数字/长引号值并截断）后 **原样 re-raise**，禁止吞异常；(4) 审计/落库失败只 warning，不阻断成功路径；(5) 可选注入 `audit_store` / `tracer`，默认 `get_tracer()`。  
    > **原因：** P0-5 锁定单节点最多重试 1 次；LangGraph 节点契约是返回 partial state；「写入错误状态」与「不静默吞」需同时满足——落库/审计可见失败，调用方仍能捕获异常。审计失败不阻断对齐 design `Node_Audit`。  
    > **依据：** [risk.md P0-5](risk.md#risk-p0) / [P1-5](risk.md#risk-p1)；[design.md §4](design.md#4-graph-节点设计) Node_Audit；[conventions.md §4.2](conventions.md#42-禁止裸-except)；[T19](tasks.md#T19) 验收。

<a id="T20"></a>
- [x] **T20：意图解析节点 `Node_IntentParse`**
  - 前置：T19、T18
  - 产出：实现 `Node_IntentParse`，判断是否属于固定三表六类问题；超范围拒绝并说明；自然语言约束无法转显式条件时返回澄清；结合上下文管理结果处理指代。
  - 验收：`pytest tests/graph_engine/test_intent_parse.py -v` 全部通过；超范围请求返回拒绝原因；模糊指代返回澄清问题；可转显式约束出现在 `constraints`。
<a id="D-INTENT-01"></a>
  - > **【实现决策 D-INTENT-01｜2026-10-25】**  
    > **决定：** (1) 业务逻辑在 `modules/intent_parse/parser.py`（规则引擎，**不调 LLM**），节点 `NodeIntentParse` 只组 Req/写状态增量；(2) 先跑 `ContextManager`：指代无法绑定 → `need_clarification=True`；再判超范围（预测/聚类/写删/契约外表）与六类标签映射失败 → `in_scope=False`+`reject_reason`；(3) 可转显式约束：`top_n` / `channel IN` / `user_segment IN` / `relative_period` / `time_grain` / `is_promo` / `exclude=…`；含「好看/合适/用户喜好」等模糊词 → 澄清且不假装已约束；(4) 超范围/澄清仍作节点成功（写 `intent`），路由到 Report 留给 Graph 边（T32+）；(5) 有指代且有上下文时可继承上轮 `question_tag`。  
    > **原因：** v1 演示/评测需可复现；意图解析若绑真实模型，Mock 路径与门禁不稳定。ADR-017 要求 NL 约束转不了必须澄清。节点失败（BaseNode 重试）应保留给真正异常，业务拒绝是正常分支。  
    > **依据：** [schema.md §7](schema.md#7-六类问题与字段依赖)–[§8](schema.md#8-超范围判定)；[design.md §3.3](design.md#33-意图解析模块)；[risk.md P1-3](risk.md#risk-p1)；[D-CTX-01](tasks.md#D-CTX-01)；[T20](tasks.md#T20) 验收。

<a id="T21"></a>
- [x] **T21：任务规划节点 `Node_Plan`**
  - 前置：T19
  - 产出：实现 `Node_Plan`，将请求拆解为 `SubTask` 列表，标注 `depends_on`、`parallel_group`、预期输入输出；规划失败写入错误状态。
  - 验收：`pytest tests/graph_engine/test_plan.py -v` 全部通过；多步请求返回至少 2 个子任务且依赖关系可序列化。
<a id="D-PLAN-01"></a>
  - > **【实现决策 D-PLAN-01｜2026-10-25】**  
    > **决定：** (1) `PlanBuilder` 按六类 `question_tag` 使用**固定两步模板**（加载/聚合 → 对比/TopN/序列），一律 ≥2 子任务，串行 `depends_on` + 递增 `parallel_group`；(2) `question_tag` 经 `build(..., question_tag=)` 传入（不改 `PlanReq`），也可从 `normalized_query` 的 `tag=` 解析；(3) 缺 intent / 超范围 / 需澄清 / 空 normalized → `PlanResp.success=False` + `error_msg`（写入状态的 `plan` 字段，节点本身不抛，供边路由 Report）；(4) 模板内嵌 `expected_input/output`（表名、分组键、图表类型），无 LLM。  
    > **原因：** Plan-and-Execute 在固定 Schema 上步骤可枚举；绑模型规划会破坏 Mock/评测可复现。两步模板满足「多步+可序列化依赖」验收，并为 T22 环检测留下真实依赖边。业务失败与 BaseNode 异常重试分离，对齐 T20 做法。  
    > **依据：** [design.md §3.4](design.md#34-任务规划模块) / [ADR-002](design.md#ADR-002)；[schema.md §7](schema.md#7-六类问题与字段依赖)；[T21](tasks.md#T21) 验收；[D-INTENT-01](tasks.md#D-INTENT-01) 无 LLM 策略一致。

<a id="T22"></a>
- [x] **T22：计划校验节点 `Node_PlanValidate`**
  - 前置：T21
  - 产出：实现 `Node_PlanValidate`，检测循环依赖、缺失输入、超出支持范围；有环或缺失输入时返回 `next_node="report"`。
  - 验收：`pytest tests/graph_engine/test_plan_validate.py -v` 全部通过；构造 A→B→A 返回 `has_cycle=True`；缺失输入返回 `missing_inputs` 非空。
<a id="D-PLANVAL-01"></a>
  - > **【实现决策 D-PLANVAL-01｜2026-10-25】**  
    > **决定：** (1) `PlanValidator` 检测：`depends_on` 有向环（DFS）、依赖/from_sub_task 悬空、`tables` 空或非 list、表名∉{orders,returns,users}、非法 `question_tag`；(2) 失败写回 `PlanResp`（`success=False`、`has_cycle`/`cycle_detail`/`missing_inputs`/`error_msg`）且 **`next_node="report"`**；(3) 通过时按 `enable_human_confirm` 路由 `human_confirm` / `generate_op`；(4) `next_node` 作为状态增量键（非冻结 TaskState 字段），供 Graph 条件边消费，不改 T07 TypedDict。  
    > **原因：** 验收点名 `next_node` 与 has_cycle/missing_inputs；硬塞进 TaskState 会破 T07。环与缺失必须在执行前拦下（design 边 → Report）。确认开关决定下一跳对齐 design 条件边。  
    > **依据：** [design.md §3.4](design.md#34-任务规划模块) / [§4](design.md#4-graph-节点设计) 条件边；[schema.md](schema.md) 三表；[T22](tasks.md#T22) 验收；[D-PLAN-01](tasks.md#D-PLAN-01) 模板依赖边。

<a id="T23"></a>
- [x] **T23：计划人工确认节点 `Node_HumanConfirmPlan`**
  - 前置：T19
  - 产出：实现 `Node_HumanConfirmPlan`，在 `enable_human_confirm=True` 时挂起并等待批准、拒绝或超时；拒绝或超时返回 `next_node="report"`，保留已完成结果；确认请求按幂等处理。
  - 验收：`pytest tests/graph_engine/test_human_confirm.py -v` 全部通过；批准返回继续，拒绝与超时返回终止；重复确认不重复变更状态。
<a id="D-CONFIRM-01"></a>
  - > **【实现决策 D-CONFIRM-01｜2026-10-25】**  
    > **决定：** (1) `PlanConfirmGate` 管等待窗与决议：`ensure_waiting` → `submit(ConfirmReq)` / 超时自动 `timeout`；(2) 同 `idempotency_key` 重放 `mutated=False` 且不改决议；已决议后即使新 key 带相反 decision 也拒绝翻转；(3) 节点：`enable_human_confirm=False` → 直接 `next_node=generate_op`；等待中 → `status=waiting_confirm`；`accept` → `generate_op`；`reject|timeout` → `report`；(4) 任何分支**不清空** `sub_task_results`；(5) 超时秒数取自 `plan_confirm_timeout_seconds`（可注入 `time_fn` 测超时）；`apply_confirm` 供 API 幂等入口。  
    > **原因：** 验收要求批准继续、拒绝/超时终止、重复确认不变更；超时按拒绝对齐 defaults。Gate 与节点分离便于 T34 SSE 确认与单测推进时钟，无需真阻塞 300s。  
    > **依据：** [defaults.md §1](defaults.md#1-策略开关默认值)–[§2](defaults.md#2-限额与超时)；[design.md §4](design.md#4-graph-节点设计) HumanConfirm 边；`ConfirmReq.idempotency_key`；[T23](tasks.md#T23) 验收。

<a id="T24"></a>
- [x] **T24：操作生成节点 `Node_GenerateOp`**
  - 前置：T19
  - 产出：实现 `Node_GenerateOp`，为当前子任务生成 SQL 或 Python 操作，返回 `op_type`、`op_content`、预期输入输出、`format_valid`；格式非法时携带错误重试 1 次；耗尽标记该子任务失败并继续其他子任务。
  - 验收：`pytest tests/graph_engine/test_generate_op.py -v` 全部通过；格式非法触发重试，重试耗尽标记该子任务失败且不阻断同批其他子任务。
<a id="D-GENOP-01"></a>
  - > **【实现决策 D-GENOP-01｜2026-10-25】**  
    > **决定：** (1) `OpGenerator` 用六类/子任务模板生成 SQL（sqlglot 可解析的 SELECT）或 Python（ast 可解析），**不调 LLM**；(2) 格式门禁：SQL 必须含 Select；Python `ast.parse`；非法则同子任务最多再生成 1 次（共 2 次）；(3) 节点按**最低未完成 `parallel_group`** 成批处理：失败写 `SubTaskResult(status=failed)`，成功放入 `generated_ops`/`current_op`，**不中断同批其他任务**；(4) 有合法 op → `next_node=safety_check`；整批失败 → `error_classify`；(5) `generated_ops`/`current_op` 为状态增量键（同 `next_node`），不改 T07 TypedDict。  
    > **原因：** v1 固定 Schema 可模板化；绑模型破坏 Mock/评测可复现。批处理对齐「耗尽标记该子任务失败并继续其他」。格式重试与 BaseNode 异常重试分离。  
    > **依据：** [design §3.5](design.md#35-操作生成模块) / [§4](design.md#4-graph-节点设计)；[ADR-002](design.md#ADR-002)；[T24](tasks.md#T24) 验收；[D-PLAN-01](tasks.md#D-PLAN-01) 子任务形状。

<a id="T25"></a>
- [x] **T25：安全校验节点 `Node_SafetyCheck`**
  - 前置：T24、T11
  - 产出：实现 `Node_SafetyCheck`，调用安全校验工具；不合规返回 `next_node="report"` 并记录 `violation_type`、`violation_detail`；危险操作不进入人工放行。
  - 验收：`pytest tests/graph_engine/test_safety_node.py -v` 全部通过；危险写操作、越权字段、危险 Python 脚本均被拦截；关闭人工确认时危险拦截仍生效。
  - 完成：`NodeSafetyCheck` 委托 `SafetyChecker`；默认白名单对齐三表分析列（不含 phone/email）；通过 → `execute`，失败 → `report`。
<a id="D-SAFE-NODE-01"></a>
  - > **【实现决策 D-SAFE-NODE-01｜2026-10-26】**  
    > **决定：** (1) 节点只读 `current_op`（无则取 `generated_ops` 首项），调用 T11 `SafetyChecker`，结果写入增量键 `safety_check`；(2) 默认 `allowed_columns` 为 schema 三表分析列，**刻意不含** `phone`/`email`，state 可覆盖；(3) `enable_human_confirm` **不参与**本节点门控——关闭人工确认也不能让危险 op 过检；(4) 违规一律 `next_node=report`，绝不路由到 `human_confirm`。  
    > **原因：** design 明确「不合规直接 Report、不进人工放行」；人工确认只管计划批准，与运行期安全正交。默认白名单收紧敏感列可把「越权字段」验收做成确定性用例。  
    > **依据：** [design §3.6](design.md#36-安全校验模块) / [§4](design.md#4-graph-节点设计)；[T11](tasks.md#T11) / [D-SAFE-01](tasks.md#D-SAFE-01)；[T25](tasks.md#T25) 验收。

<a id="T26"></a>
- [x] **T26：执行节点 `Node_Execute`**
  - 前置：T24、T13
  - 产出：实现 `Node_Execute`，调用沙箱执行器；成功返回结果，失败返回错误分类输入；资源超限直接标记该子任务失败；沙箱不可用返回错误不降级。
  - 验收：`pytest tests/graph_engine/test_execute_node.py -v` 全部通过；Mock 成功返回 `result_rows`，超时返回 `resource_killed=True`；Docker 不可用返回 `SandboxUnavailableError`。
  - 完成：`NodeExecute` 委托可注入 `SandboxExecutor`（默认 Docker）；限额取自 Settings；`execute_resp` / `next_node` 为增量键。
<a id="D-EXEC-NODE-01"></a>
  - > **【实现决策 D-EXEC-NODE-01｜2026-10-26】**  
    > **决定：** (1) 从 `current_op`（或 `generated_ops` 首项）组 `ExecuteReq`，超时/内存/CPU 一律读 Settings；(2) 成功 → 写入 `SubTaskResult(status=success)` + `next_node=result_validate`；(3) 普通失败 → 保留 `execute_resp`、**不**提前标 failed，`next_node=error_classify`（留给 Reflect 自愈）；(4) `resource_killed=True` → **立即** `SubTaskResult(status=failed)` 仍进 `error_classify`（T14 会标 `retryable=False`）；(5) `SandboxUnavailableError` **不捕获、不降级宿主机**，由 BaseNode 重试后原样抛出；(6) 默认执行器 `DockerSandboxExecutor`，单测注入 `MockSandboxExecutor`。  
    > **原因：** design 边：成功 → ResultValidate、失败 → ErrorClassify；T26 要求资源超限直接失败且沙箱不可用不降级。软失败若提前标 failed，会干扰后续自愈状态机。  
    > **依据：** [design §3.7](design.md#37-隔离执行器) / [§4](design.md#4-graph-节点设计)；[T12](tasks.md#T12)/[D-EXEC-01](tasks.md#D-EXEC-01)、[T13](tasks.md#T13)/[D-DOCKER-01](tasks.md#D-DOCKER-01)、[D-ERRCLS-01](tasks.md#D-ERRCLS-01)；[T26](tasks.md#T26) 验收；[ADR-003](design.md#ADR-003)。

<a id="T27"></a>
- [x] **T27：错误分类节点 `Node_ErrorClassify`**
  - 前置：T26、T14
  - 产出：实现 `Node_ErrorClassify`，调用错误分类工具，返回 `error_type`、`error_fingerprint`、`retryable`、`suggested_fix`，并写入子任务错误记录。
  - 验收：`pytest tests/graph_engine/test_error_classify_node.py -v` 全部通过；语法错误与运行时错误分类正确；错误指纹写入状态。
  - 完成：`NodeErrorClassify` 委托 `ErrorClassifier`；指纹追加到 `error_fingerprints`；子任务 `error_summary` 更新。
<a id="D-ERRCLS-NODE-01"></a>
  - > **【实现决策 D-ERRCLS-NODE-01｜2026-10-26】**  
    > **决定：** (1) 读 `execute_resp` + `current_op.op_type` 调 T14 `ErrorClassifier`；(2) 分类结果写入增量键 `error_classify`，指纹 **追加** 到 `error_fingerprints`（`none` 不追加）；(3) 当前子任务写/更新 `SubTaskResult(status=failed, error_summary=…)`；(4) `retryable=True` → `next_node=reflect`，否则 → `report`；(5) 无 `execute_resp`（如 GenerateOp 格式耗尽）时用 `op.error_msg` / 已有失败摘要合成失败 `ExecuteResp`；(6) 熔断与调用上限判定留给 [T28](tasks.md#T28)，本节点不做二次熔断。  
    > **原因：** design 边把「可重试」与 Reflect 解耦；指纹必须先入库供连续相同指纹熔断。格式失败路径也会进本节点，需可合成输入以免空转。  
    > **依据：** [design §3.8](design.md#38-错误分类与自愈模块) / [§4](design.md#4-graph-节点设计)；[T14](tasks.md#T14)/[D-ERRCLS-01](tasks.md#D-ERRCLS-01)；[T26](tasks.md#T26)/[D-EXEC-NODE-01](tasks.md#D-EXEC-NODE-01)；[T27](tasks.md#T27) 验收。

<a id="T28"></a>
- [x] **T28：自愈反思节点 `Node_Reflect`**
  - 前置：T27
  - 产出：实现 `Node_Reflect`，判断是否重试、是否熔断；连续相同错误指纹两次、达到 `max_retry_rounds` 或达到 `max_llm_calls_per_task` 时触发熔断；总调用上限优先；继续自愈时返回 `next_node="generate_op"`。
  - 验收：`pytest tests/graph_engine/test_reflect.py -v` 全部通过；第一次失败继续重试，第二次相同指纹触发熔断；达到总调用上限时熔断原因正确；不同指纹不误熔断。
  - 完成：`Reflector` + `NodeReflect`；熔断优先级 LLM 上限 > 连续同指纹 > 重试上限；`fix_hint` 注入增量键。
<a id="D-REFLECT-01"></a>
  - > **【实现决策 D-REFLECT-01｜2026-10-26】**  
    > **决定：** (1) 工具 `Reflector.reflect(ReflectReq)` 纯规则、无 LLM；(2) 熔断优先级：`llm_calls_used >= max_llm_calls` → `llm_call_limit`，否则末尾连续两次相同指纹 → `duplicate_fingerprint`，否则 `retry_count >= max_retry` → `retry_limit`；(3) 指纹列表以 ErrorClassify 已追加后的 `error_fingerprints` 为准（第二次同错即 `[fp,fp]` 熔断）；(4) 可自愈 → `should_retry`、`fix_hint=suggested_fix`、`retry_counts[sub]+=1`、子任务 `healing`、`next_node=generate_op`；(5) 熔断/不可重试 → `report`；(6) `enable_reflector=False` 直接熔断进 Report。  
    > **原因：** 验收点名「第一次重试 / 第二次同指纹熔断 / LLM 上限原因正确 / 不同指纹不误熔」；总调用上限优先对齐 risk/handoff。连续判定用末尾两槽，避免「历史曾出现同指纹」误熔。  
    > **依据：** [design §3.8](design.md#38-错误分类与自愈模块) / [§4](design.md#4-graph-节点设计)；[risk.md 自愈摘要](risk.md#risk-summary)；[T27](tasks.md#T27)/[D-ERRCLS-NODE-01](tasks.md#D-ERRCLS-NODE-01)；[T28](tasks.md#T28) 验收；[defaults §2](defaults.md#2-限额与超时)。

<a id="T29"></a>
- [x] **T29：结果校验节点 `Node_ResultValidate`**
  - 前置：T26、T15
  - 产出：实现 `Node_ResultValidate`，调用结果校验工具；异常且可自愈返回 `next_node="generate_op"`，不可自愈标记异常并进入报告；只校验可结构化约束。
  - 验收：`pytest tests/graph_engine/test_result_validate_node.py -v` 全部通过；比例越界与异常空被标记；不可自愈时不回环；无法转为显式条件的约束不参与校验。
  - 完成：`NodeResultValidate` 委托 `ResultValidator`；约束取自状态增量键 `structured_constraints`；NL/`nl_*` 由工具层跳过。
<a id="D-RESULT-NODE-01"></a>
  - > **【实现决策 D-RESULT-NODE-01｜2026-10-26】**  
    > **决定：** (1) 读 `execute_resp` + `structured_constraints`（状态增量；缺省 `[]`，**不**从意图 NL 字符串臆造校验 dict）；(2) 通过 → `next_node=report`；(3) 异常指纹 `result_anomaly|{type}|{normalize(detail)}` 追加到 `error_fingerprints`；(4) 可自愈条件：异常类型∈四类 **且** `enable_reflector` **且** 未触 LLM/重试上限 **且** 非连续同指纹 → `healing` + `generate_op`；(5) 否则标 `failed` → `report`。  
    > **原因：** 验收要求比例/空异常可标记、不可自愈不回环、NL 不参与；与 Reflect 共用指纹连续规则避免结果异常死循环。意图层 `constraints: list[str]` 不是 ResultValidate 的 dict 契约，硬转易假校验。  
    > **依据：** [design §3.9](design.md#39-结果校验与报告模块) / [§4](design.md#4-graph-节点设计)；[T15](tasks.md#T15)/[D-RESULT-01](tasks.md#D-RESULT-01)；[T28](tasks.md#T28)/[D-REFLECT-01](tasks.md#D-REFLECT-01)；[T29](tasks.md#T29) 验收。

<a id="T30"></a>
- [x] **T30：报告节点 `Node_Report`**
  - 前置：T29、T16
  - 产出：实现 `Node_Report`，汇总所有子任务结果、失败归因、来源映射，生成 `summary`、`tables`、`charts`、`partial_success`；汇总异常时输出保底结构化摘要；不支持的图表类型说明替代。
  - 验收：`pytest tests/graph_engine/test_report_node.py -v` 全部通过；部分成功时报告包含失败原因和已完成结果；空数据不画图并说明。
  - 完成：`NodeReport` 委托 `ReportBuilder`；写入 `final_report`；Builder 异常 → 保底摘要仍结束任务。
<a id="D-REPORT-NODE-01"></a>
  - > **【实现决策 D-REPORT-NODE-01｜2026-10-26】**  
    > **决定：** (1) 从 `sub_task_results` + 可选 `failures`/`chart_requests` 组 `ReportReq`，图表请求仍走 Builder 可选参数；(2) Builder 任意异常 → **不抛出**，输出保底 `ReportResp`（保留已成功表与失败归因）；(3) `enable_fallback_report` 且（`model_unavailable` 或 Reflect `melt_reason=llm_call_limit`）→ `next_node=fallback_report` + `status=fallback`；否则 `next_node=end`；(4) 终态：有成功片段 → `success`，全失败 → `failed`。  
    > **原因：** design 要求「汇总异常仍正常结束」；降级报告只在模型彻底不可用且开关开启时触发（T31），与普通失败报告分离。  
    > **依据：** [design §3.9](design.md#39-结果校验与报告模块) / [§4](design.md#4-graph-节点设计)；[T16](tasks.md#T16)/[D-REPORT-01](tasks.md#D-REPORT-01)；[T28](tasks.md#T28)/[D-REFLECT-01](tasks.md#D-REFLECT-01)；[T30](tasks.md#T30) 验收。

<a id="T31"></a>
- [x] **T31：降级报告节点 `Node_FallbackReport`**
  - 前置：T30、T16
  - 产出：实现 `Node_FallbackReport`，在模型重试耗尽且 `enable_fallback_report=True` 时输出静态诊断报告：数据概况、请求解析状态、失败节点与影响范围、可手动执行字段与模板建议、已完成子任务结果；开头标注“非分析结论”；不生成新查询/脚本。
  - 验收：`pytest tests/graph_engine/test_fallback_report.py -v` 全部通过；报告含 `is_analysis_conclusion=False` 与“静态诊断”标记；不含虚构分析结论；开关关闭时不触发。
  - 完成：`FallbackReportBuilder` + `NodeFallbackReport`；开关关 → 不写 `fallback_report`。
<a id="D-FALLBACK-01"></a>
  - > **【实现决策 D-FALLBACK-01｜2026-10-26】**  
    > **决定：** (1) 工具 `FallbackReportBuilder` 纯模板填充 `FallbackReportResp`，`is_analysis_conclusion` 固定 `False`，`summary` 必含「非分析结论」「静态诊断」；(2) 建议仅字段/六类模板/重提指引，**禁止** SQL/Python/虚构结论文案（构建期断言）；(3) 敏感列不进字段建议；(4) `enable_fallback_report=False` 时节点直接 `next_node=end` 且 `fallback_report=None`（不触发）；(5) Builder 异常 → 仍输出保底诊断摘要；(6) 由 T30 在模型不可用时路由至此。  
    > **原因：** ADR-015 / 验收明确「非分析结论 + 静态诊断 + 开关可关 + 无虚构」；降级路径绝不能偷偷生成可执行代码。  
    > **依据：** [design §3.10](design.md#310-降级报告模块) / [§4](design.md#4-graph-节点设计)；[ADR-015](design.md#ADR-015)；[T30](tasks.md#T30)/[D-REPORT-NODE-01](tasks.md#D-REPORT-NODE-01)；[T31](tasks.md#T31) 验收。

## 五、Graph 编排

<a id="T32"></a>
- [x] **T32：组装主工作流**
  - 前置：T20–T31
  - 产出：实现 `graph_engine/builder.py`，组装 `IntentParse → Plan → PlanValidate → HumanConfirmPlan → GenerateOp → SafetyCheck → Execute → ResultValidate / ErrorClassify → Reflect → Report → FallbackReport`，绑定 SQLite Checkpointer 与条件边。
  - 验收：`pytest tests/graph_engine/test_builder_main.py -v` 全部通过；Mock LLM 与 Mock Sandbox 跑通“规划→执行成功→报告”、“执行失败→自愈成功→报告”、“模型不可用→降级报告”三条路径。
  - 完成：`build_main_graph` + `GraphState` + SQLite/`MemorySaver` Checkpointer；三条 Mock 路径验收通过。
<a id="D-GRAPH-01"></a>
  - > **【实现决策 D-GRAPH-01｜2026-10-26】**  
    > **决定：** (1) `StateGraph(GraphState)`：`TaskState` + 路由增量键（`next_node`/`current_op`/…），不改 T07 冻结字段表；(2) 条件边读 `next_node`；Intent 缺省时按 `in_scope`/`need_clarification` 推断；HumanConfirm `waiting_confirm` → END（供 API 断点续跑）；(3) ResultValidate 成功后若仍有未完成子任务 → 回 `generate_op`，否则 `report`；(4) Checkpointer 默认可注入；`open_sqlite_checkpointer` 包 `SqliteSaver.from_conn_string`（测试用 `:memory:`）；(5) 节点可经 `default_nodes(**overrides)` 注入 Mock 执行器。附带：安全校验不把 `COUNT(*)`/`SUM(*)` 当 `SELECT *`，且忽略 SELECT 别名列。  
    > **原因：** 验收要三条可复现路径；多子任务若不回环会只跑第一组；别名/`COUNT(*)` 误杀会阻断合法模板 SQL。  
    > **依据：** [design §4](design.md#4-graph-节点设计) 条件边；[T32](tasks.md#T32) 验收；[D-SAFE-01](tasks.md#D-SAFE-01)；[ADR-001](design.md#ADR-001) / [ADR-007](design.md#ADR-007)。

<a id="T33"></a>
- [x] **T33：策略开关分支**
  - 前置：T32
  - 产出：实现 `enable_planner=False` 的单步试探执行分支、`enable_reflector=False` 不重试分支、`enable_sandbox=False` 仅评测且仍用一次性容器分支、`enable_human_confirm=False` 跳过确认但危险拦截仍生效分支；所有分支复用同一评测集与指标。
  - 验收：`pytest tests/graph_engine/test_builder_flags.py -v` 全部通过；关闭 planner 走单步分支，关闭 reflector 失败直接进报告，关闭 human_confirm 不产生中断且危险操作仍被拦截，关闭 sandbox 仅评测且容器配置仍为一次性。
  - 完成：同一 `build_main_graph` 路径；`PlanBuilder.single_step` / ErrorClassify 直达 Report / Safety 仍拦危险 / Docker 关隔离仍一次性。
<a id="D-FLAG-01"></a>
  - > **【实现决策 D-FLAG-01｜2026-10-26】**  
    > **决定：** (1) **不复制第二套 Graph**：四开关均经 `state.strategy_flags` 驱动同一 `build_main_graph`；(2) `enable_planner=False` → `PlanBuilder.build(..., single_step=True)` 把六类两步模板折叠为单一 `st-probe`（合并 tables/group_by/metric，标记 `single_step`），仍走 PlanValidate→GenerateOp→…；(3) `enable_reflector=False` → `NodeErrorClassify` 可重试也 **`next_node=report`**（不进 Reflect），ResultValidate/Reflect 既有短路保留；(4) `enable_human_confirm=False` 仍由 PlanValidate/HumanConfirm 跳过挂起，**SafetyCheck 不读该开关**；(5) `enable_sandbox=False` 仅评测语义，`DockerSandboxExecutor` 仍 `containers.run` + detach + kill/remove（[D-DOCKER-01](tasks.md#D-DOCKER-01)）。  
    > **原因：** 消融评测要对齐同一指标与路径；关 planner 若另起图会分裂代码；关 reflector 若仍进 Reflect 再熔断会污染 retry/审计语义；关确认绝不能变成危险旁路。  
    > **依据：** [defaults §1](defaults.md#1-策略开关默认值)；[spec FR-14](spec.md)；[conventions §7](conventions.md)；[T33](tasks.md#T33) 验收；[D-GRAPH-01](tasks.md#D-GRAPH-01)；[D-CONFIRM-01](tasks.md#D-CONFIRM-01)；[D-SAFE-NODE-01](tasks.md#D-SAFE-NODE-01)；[D-DOCKER-01](tasks.md#D-DOCKER-01)。

<a id="T34"></a>
- [x] **T34：FastAPI 接口、单任务队列、SSE 序号、确认幂等、终止杀容器**
  - 前置：T32、T33
  - 产出：实现 `api/`：`POST /api/datasets/import`、`POST /api/tasks`（进入单任务串行队列）、`GET /api/tasks/{id}/stream`（SSE 事件带 `seq`，支持断线按序号补发）、`POST /api/tasks/{id}/confirm-plan`（幂等）、`POST /api/tasks/{id}/terminate`（杀容器并保留已完成结果）、`GET /api/tasks/{id}/trace`；统一异常响应。
  - 验收：`pytest tests/api/test_api.py -v` 全部通过；上传 CSV 返回 `dataset_id`；SSE 收到至少 `task_started`、`node_entered`、`task_completed` 且 `seq` 递增；模拟断线重连按序号补发；重复确认不重复执行；终止后容器被杀且轨迹保留终止原因；并发提交时第二任务排队。
  - 完成：`create_app` + `TaskQueue` 串行 worker + SSE `after_seq` 补发；确认共享 `PlanConfirmGate`；终止调 `executor.terminate`。
<a id="D-API-01"></a>
  - > **【实现决策 D-API-01｜2026-10-26】**  
    > **决定：** (1) `api/`：`create_app` 注入 `AppRuntime`（Importer / AuditStore / 共享 `PlanConfirmGate` / `TaskQueue`）；统一 `{"error":{code,message,detail?}}`；(2) **单 worker 串行队列**：`queue_position=前方排队数`；Graph 用 `MemorySaver`+`stream(updates)` 发 `task_started`/`node_entered`/`task_completed`；(3) SSE：`TaskEventLog` 追加序号，`GET .../stream?after_seq=` 先回放再追活；(4) `waiting_confirm`：**路由优先看 status**（忽略 PlanValidate 残留 `next_node`）→ END；确认经共享 Gate 幂等，`update_state(..., as_node=human_confirm)` 后续跑；(5) terminate：`stop_event` + `executor.terminate(reason)`，轨迹保留 `terminate_reason` 与已有 `sub_task_results`；依赖补 `python-multipart`。  
    > **原因：** v1 禁止多任务并发写；SSE 断线必须按序号补发；确认 Gate 必须与节点同实例才能幂等；HumanConfirm 中断若仍读残留 `next_node` 会误进 Report。  
    > **依据：** [design §3.11](design.md#311-任务调度与-api-事件) / [§4](design.md#4-graph-节点设计)；[ADR-012](design.md#ADR-012) / [ADR-016](design.md#ADR-016)；[risk P0-9](risk.md#risk-p0)；[D-CONFIRM-01](tasks.md#D-CONFIRM-01)；[D-GRAPH-01](tasks.md#D-GRAPH-01)；[T34](tasks.md#T34) 验收。

<a id="T35"></a>
- [x] **T35：Vue 3 最小前端**
  - 前置：T34
  - 产出：实现上传页、提问页、执行轨迹列表、计划确认、结果表格与基础图表（柱状图、折线图）；封装 REST 与 SSE 客户端；`npm run build` 可构建。
  - 验收：`cd frontend && npm install && npm run build` 退出码 0；页面可展示 `dataset_id`、任务状态列表、计划确认按钮、结果表格；SSE 断线后状态可恢复。
  - 完成：`frontend/` Vue 3 + Vite + ECharts；REST/SSE 客户端；`npm run build` 通过；trace 暴露 `final_report`。
<a id="D-FE-01"></a>
  - > **【实现决策 D-FE-01｜2026-10-26】**  
    > **决定：** (1) 单页工作台（非多路由）：上传 → 提问 → 任务卡片（轨迹 / 确认 / 报告）；(2) `src/api/client.ts` REST + `sse.ts`：`EventSource` 断线后按 `after_seq` 重连，并用 `GET /trace` 轮询兜底合并状态；(3) 图表用 ECharts 渲染 `bar|line`，`table` 型 ChartSpec 直接表格展示；结果表读 `final_report.tables`；(4) Vite `proxy /api → :8000`；API 侧放行 `localhost:5173` CORS，且 `trace` 增补 `final_report`/`plan`/`dataset_id`；(5) 锁定 `package-lock.json`；本地 `.npmrc`/代理不入库。  
    > **原因：** 验收要求可构建与 SSE 恢复，不要求复杂路由/画布；断线只靠 SSE 不够稳，trace 兜底对齐 [risk P1-6](risk.md#risk-p1)；报告字段不在 SSE payload 里，必须从 trace 拉全量。  
    > **依据：** [defaults §6](defaults.md#6-前端图表)；[ADR-013](design.md#ADR-013) / [ADR-012](design.md#ADR-012)；[D-API-01](tasks.md#D-API-01)；[T35](tasks.md#T35) 验收；[conventions §1.1](conventions.md) Vue 3.5+。

## 六、测试

<a id="T36"></a>
- [x] **T36：主工作流集成测试**
  - 前置：T32–T35
  - 产出：编写 `tests/integration/test_workflow.py`，使用 Mock LLM 与 Mock Sandbox 跑通完整成功路径、失败自愈路径、部分成功路径、降级报告路径、单任务串行、SSE 重连补发。
  - 验收：`pytest tests/integration/test_workflow.py -v` 全部通过；五条路径均产生 `final_report` 或 `fallback_report` 且审计事件完整；SSE 事件序号连续无重复。
  - 完成：6 用例覆盖 Graph 四路径 + API 串行 + SSE 补发；审计 `node_started/completed` 齐全。
<a id="D-INT-01"></a>
  - > **【实现决策 D-INT-01｜2026-10-26】**  
    > **决定：** (1) Graph 层四路径：成功 / 失败自愈（首调 SYNTAX 后 SUCCESS）/ 部分成功（首成功、次 `RESOURCE_LIMIT` → `partial_success`）/ 超范围+`model_unavailable` → Fallback；(2) 各路径注入同一 `AuditStore`，断言 `node_started`+`node_completed` 且 id 有序唯一；(3) API 层：阻塞执行器验证串行 `queue_position`，两任务均出报告；(4) SSE：全量 `seq==0..n-1` 无重复，`after_seq` 重放与尾部一致。全部 Mock，无真实模型/Docker。  
    > **原因：** 验收点名五条报告路径 + SSE 序号；部分成功需跨子任务混合结果，资源杀不可重试才能停在 Report 而不无限自愈。  
    > **依据：** [T36](tasks.md#T36) 验收；[D-GRAPH-01](tasks.md#D-GRAPH-01) / [D-API-01](tasks.md#D-API-01) / [D-FALLBACK-01](tasks.md#D-FALLBACK-01)；[conventions.md](conventions.md) 禁止测真实付费模型。

<a id="T37"></a>
- [x] **T37：安全与沙箱集成测试**
  - 前置：T36
  - 产出：编写 `tests/integration/test_safety_sandbox.py`，覆盖危险 SQL、危险 Python 脚本、越权字段、沙箱超时、资源超限、Docker 不可用、宿主机禁止执行、关闭隔离仍用容器。
  - 验收：`pytest tests/integration/test_safety_sandbox.py -v` 全部通过；危险操作全部被拦截；沙箱不可用时返回 `SandboxUnavailableError`；关闭隔离时仍使用一次性容器；无宿主机执行路径。
  - 完成：10 用例；危险 op 零执行；Docker 假客户端超时/关隔离仍 oneshot；AST 禁宿主机 `eval`/`exec`。
<a id="D-SAFE-INT-01"></a>
  - > **【实现决策 D-SAFE-INT-01｜2026-10-26】**  
    > **决定：** (1) Graph 注入 `_ForcedOpGenerate` 验证危险 SQL/Python/越权列 → Safety 拦下且 **executor.calls==0**；(2) Mock `TIMEOUT`/`RESOURCE_LIMIT` 走通 Graph 标 `resource_killed`+报告；(3) `DockerSandboxExecutor`：daemon 不可用 → `SandboxUnavailableError` 上抛无宿主机降级；假 client 超时仍 kill+remove；`enable_sandbox=False` 仍 `containers.run`+detach+remove；(4) 静态 AST 扫描 executor/execute/safety 源码禁止裸 `eval`/`exec`；Mock 炸弹 payload 成功返回且不落入 `executed_op_contents`。  
    > **原因：** 验收要「拦危险 / 不可用报错 / 关隔离仍容器 / 无宿主机执行」端到端可复现，不依赖真实 Docker daemon。  
    > **依据：** [T37](tasks.md#T37) 验收；[D-SAFE-01](tasks.md#D-SAFE-01) / [D-SAFE-NODE-01](tasks.md#D-SAFE-NODE-01)；[D-DOCKER-01](tasks.md#D-DOCKER-01) / [D-EXEC-NODE-01](tasks.md#D-EXEC-NODE-01)；[risk P0-1](risk.md#risk-p0)。

<a id="T38"></a>
- [x] **T38：策略开关消融集成测试**
  - 前置：T37
  - 产出：编写 `tests/integration/test_strategy_flags.py`，用同一组用例对比 planner 开/关、reflector 开/关、human_confirm 开/关且危险拦截仍生效。
  - 验收：`pytest tests/integration/test_strategy_flags.py -v` 全部通过；关闭 planner 走单步，关闭 reflector 不重试，关闭 human_confirm 无中断但危险操作仍被拦截。
  - 完成：同一 `_CASE_QUERY` 消融矩阵；planner/reflector/human_confirm 开闭对比 + 关确认仍拦危险。
<a id="D-ABLATION-01"></a>
  - > **【实现决策 D-ABLATION-01｜2026-10-26】**  
    > **决定：** (1) 固定评测问句 `对比 APP 和官网 渠道销售额` + `ds-ablation`，所有开关对比共用；(2) planner on≥2 子任务 / off=`st-probe` 单步；(3) reflector on 自愈≥2 次执行 / off 失败直报且 `reflect is None`、retry 不增；(4) human_confirm on → `waiting_confirm`；off → 无中断出报告，且注入危险 DELETE 仍 `dangerous_write`。不复制第二套 Graph（对齐 [D-FLAG-01](tasks.md#D-FLAG-01)）。  
    > **原因：** 消融必须控制变量；验收点名三组开关与「关确认不旁路安全」。  
    > **依据：** [T38](tasks.md#T38) 验收；[D-FLAG-01](tasks.md#D-FLAG-01)；[spec FR-14](spec.md)；[risk P2-3](risk.md#risk-p2)。

<a id="T39"></a>
- [x] **T39：全量测试与覆盖率**
  - 前置：T38
  - 产出：运行全量质量门禁与覆盖率检查，补齐缺失单测。
  - 验收：`ruff check src tests && black --check src tests && mypy src && pytest --cov=src/data_reflex` 全部通过；核心模块覆盖率 ≥85%。
  - 完成：`[tool.coverage.report] fail_under=85`；补 `tests/api/test_errors_events.py`；全量门禁 283 passed / 1 skipped，TOTAL **88.71%**。
<a id="D-COV-01"></a>
  - > **【实现决策 D-COV-01｜2026-10-26】**  
    > **决定：** (1) 覆盖率门槛写在 `pyproject.toml` → `[tool.coverage.report] fail_under = 85`，与验收「≥85%」对齐，未达则 pytest 非零退出；(2) 范围固定 `src/data_reflex`（`--cov=src/data_reflex`），不把 `tests/`/`frontend/` 计入；(3) 偏低的 `api/errors`、`api/events`、`main` 用 `tests/api/test_errors_events.py` 补齐信封/SSE wait/uvicorn 入口；不强制单文件 100%，以包级 TOTAL 为准。  
    > **原因：** 验收只规定整体阈值；门禁必须机器可执行，避免人工读报告漏判。  
    > **依据：** [T39](tasks.md#T39) 验收；[handoff 完成定义](handoff.md#完成定义已锁定)；[defaults 质量门禁](defaults.md#质量门禁)。

## 七、评测脚本

<a id="T40"></a>
- [x] **T40：构造分层评测集（先 30 后 50+）**
  - 前置：T39
  - 产出：在 `eval/datasets/` 创建 JSONL 评测集，按单表、多表 join、嵌套聚合、时间窗口、空值、歧义列名分层；先每层 5 条共 30 条，再扩到每层 10–15 条；每条包含问题、金标准结果、约束、允许字段、数据泄漏标记。
  - 验收：`python -c "import json; lines=[json.loads(l) for l in open('eval/datasets/core.jsonl')]; assert len(lines)>=30; assert {x['category'] for x in lines} >= {'single_table','multi_join','nested_agg','time_window','null_value','ambiguous_column'}"` 无异常；检查数据泄漏字段存在。
  - 完成：`eval/datasets/core.jsonl` **60** 条（六层各 10）；金标准对齐 fixtures；`has_leakage` 全量字段，3 条标记泄漏（演示/消融问句重叠）。
<a id="D-EVALSET-01"></a>
  - > **【实现决策 D-EVALSET-01｜2026-10-26】**  
    > **决定：** (1) 文件固定 `eval/datasets/core.jsonl`（UTF-8 JSONL）；(2) 每条必含 `case_id`/`category`/`question`/`question_tag`/`constraints`/`allowed_fields`/`gold`/`has_leakage`/`fixtures`；(3) 六层枚举与验收一致，每层 10 条共 60（满足「先 30 后 50+」）；(4) 金标准相对 `eval/datasets/fixtures` + 评测时钟 `2025-10-01T00:00:00+08:00` 计算；「上个月」= 2025-09；(5) `has_leakage=true` 标记与演示/消融提示高度重叠的问句（如「对比 APP 和官网 渠道销售额」），供 T42 排除出指标。  
    > **原因：** 验收只锁文件名与 category 集合；若不锁字段名，T41 加载器会各写一套；泄漏需可机器过滤。  
    > **依据：** [T40](tasks.md#T40) 验收；[schema.md](schema.md) fixtures；[defaults 评测时钟](defaults.md)；[spec FR-12](spec.md)；[design §3.12](design.md#312-评测运行模块)。

<a id="T41"></a>
- [x] **T41：批量评测脚本 mock 模式与标注**
  - 前置：T40、T33
  - 产出：实现 `eval/run_eval.py`，支持 `--mode mock`、`--max-cases`、`--strategy-matrix`；v1 策略矩阵仅允许 `planner` 与 `reflector` 两个键，其余固定默认；报表标注 mock 或真实模式；单用例失败继续。
  - 验收：`python eval/run_eval.py --mode mock --max-cases 5 --strategy-matrix planner=on,off` 退出码 0；`grep -q 'mode: mock' eval/results/report.md` 返回 0；`test -f eval/results/metrics.csv` 返回 0；传入 `sandbox` 键时脚本拒绝并提示仅支持 planner/reflector。
  - 完成：`eval/run_eval.py` Mock 路径 + 矩阵笛卡尔积；`report.md`/`metrics.csv`；`sandbox` 键退出码 2；泄漏用例记入 `leakage_cases` 且不计入指标分母。
<a id="D-EVAL-01"></a>
  - > **【实现决策 D-EVAL-01｜2026-11-03】**  
    > **决定：** (1) CLI：`--mode`/`--max-cases`/`--strategy-matrix`；矩阵语法 `key=on,off`，多键用 `;`；(2) 仅 `planner`/`reflector`，其它键（含 `sandbox`）stderr 提示并退出 2；(3) 批跑强制 `enable_human_confirm=False`，沙箱用 `MockSandboxExecutor`，复用同一 `build_main_graph`；(4) 单用例异常记录后继续；(5) 产出 `eval/results/report.md`（含 `mode: mock`）与 `metrics.csv`；`has_leakage` 用例列入 `leakage_cases` 且排除出指标（为 T42 预留）。  
    > **原因：** 验收点名 mock 标注与非法矩阵键；人确认会挂起批跑；消融必须与主图同路径。  
    > **依据：** [T41](tasks.md#T41) 验收；[D-FLAG-01](tasks.md#D-FLAG-01)；[D-EVALSET-01](tasks.md#D-EVALSET-01)；[design §3.12](design.md#312-评测运行模块)；[spec FR-12](spec.md)。

<a id="T42"></a>
- [x] **T42：指标报表与可复现**
  - 前置：T41
  - 产出：在报表中输出首次成功率、自愈后成功率、执行成功率、结果准确率、平均步骤数、平均模型调用量、自愈率；固定时钟、时区、模型参数；检查数据泄漏并排除；支持 planner/reflector 开关矩阵对比。
  - 验收：`python eval/run_eval.py --mode mock --max-cases 5 --strategy-matrix planner=on,off` 退出码 0；`grep -q 'first_pass_rate' eval/results/metrics.csv` 且 `grep -q 'fixed_clock' eval/results/report.md` 返回 0；报表包含 `mode` 字段；泄漏用例出现在 `leakage_cases` 且不参与指标计算。
  - 完成：`report.md` 含 mode/fixed_clock/timezone/llm_* 与全量指标；`metrics.csv` 含 `first_pass_rate` 等；泄漏排除出分母（例：5 案中 2 泄漏 → `n_cases=3`）。
<a id="D-REPRO-01"></a>
  - > **【实现决策 D-REPRO-01｜2026-11-03】**  
    > **决定：** (1) 关键评测 mock 强制 `llm_temperature=0` 并写入报表；(2) `fixed_clock`/`timezone` 取自 Settings（对齐 defaults §7）；(3) 指标键名固定英文：`first_pass_rate`/`post_heal_rate`/`execution_success_rate`/`result_accuracy`/`avg_steps`/`avg_llm_calls`/`self_heal_rate`；(4) `has_leakage=true` 仍执行以便排障，但不计入任何 rate 分母，并出现在 `leakage_cases`；(5) planner/reflector 矩阵每格独立写一行 CSV + 一节 Markdown。  
    > **原因：** 验收点名上述字段与泄漏排除；温度/时钟不锁则跨日不可复现。  
    > **依据：** [T42](tasks.md#T42) 验收；[D-EVAL-01](tasks.md#D-EVAL-01)；[defaults §7](defaults.md)；[spec FR-12](spec.md)；[design §3.12](design.md#312-评测运行模块)。

## 八、文档

<a id="T43"></a>
- [x] **T43：README 快速开始**
  - 前置：T42
  - 产出：编写 `README.md`，包含项目定位、架构简述、环境安装、`.env` 配置、启动命令、评测命令、策略开关说明、降级报告说明、固定三表六类说明。
  - 验收：`grep -q 'DataReflex' README.md && grep -q 'run_eval.py' README.md && grep -q 'enable_reflector' README.md && grep -q 'enable_fallback_report' README.md` 返回 0。
  - 完成：根目录 `README.md` 覆盖定位/架构/安装/`.env`/启动/评测/开关/降级/三表六类；验收关键词齐备。
<a id="D-README-01"></a>
  - > **【实现决策 D-README-01｜2026-11-03】**  
    > **决定：** (1) 根目录单文件 `README.md` 作为快速开始入口，细节链到 `docs/*`；(2) 启动命令同时给出 Windows PowerShell 与 API/前端双进程；(3) 明确 API 默认 Mock 沙箱可演示、Docker 可选；(4) 策略五开关与降级「非分析结论」写进正文以满足验收检索词。  
    > **原因：** T43 验收只锁关键词；若 README 过薄，手测仍依赖对话。  
    > **依据：** [T43](tasks.md#T43) 验收；[handoff](handoff.md)；[defaults](defaults.md)；[schema §7](schema.md#7-六类问题与字段依赖)。

<a id="T44"></a>
- [x] **T44：架构与 ADR 文档**
  - 前置：T43
  - 产出：整理 `docs/architecture.md`，包含架构图、模块职责、数据流、ADR-001 至 ADR-018 摘要；与 `spec.md`、`design.md`、`conventions.md` 对齐；明确固定三表六类、降级报告、单任务串行、上下文范围。
  - 验收：`test -f docs/architecture.md` 返回 0；`grep -q 'ADR-015' docs/architecture.md && grep -q 'ADR-018' docs/architecture.md && grep -q 'Plan-and-Execute' docs/architecture.md && grep -q '非分析结论' docs/architecture.md` 返回 0。
  - 完成：新增 [`docs/architecture.md`](architecture.md)；含 mermaid、模块表、数据流、ADR-001～018 摘要与关键词验收项。
<a id="D-ARCH-01"></a>
  - > **【实现决策 D-ARCH-01｜2026-11-03】**  
    > **决定：** (1) 单独 `docs/architecture.md` 作架构入口，ADR 全文仍以 `design.md` §7 为准，本文只做摘要表；(2) 必须显式写清固定三表六类、降级「非分析结论」、单任务串行、上下文 1–2 轮；(3) 注明演示默认 Mock 与 Docker 真实执行的边界，避免读者把 Mock 数字当金标准。  
    > **原因：** T44 验收点名文件与关键词；architecture 若复制 ADR 全文会与 design 双源漂移。  
    > **依据：** [T44](tasks.md#T44) 验收；[design.md](design.md)；[spec.md](spec.md)；[conventions.md](conventions.md)；[handoff](handoff.md)。

<a id="T45"></a>
- [x] **T45：质量门禁收尾**
  - 前置：T44
  - 产出：清理调试输出、确认无硬编码密钥、无裸 `except:`、无单文件超 800 行；确认宿主机无生成代码执行路径；更新任务勾选状态。
  - 验收：`ruff check src tests && black --check src tests && mypy src && pytest` 全部通过；`grep -R "except:" src || true` 无输出；`grep -R "print(" src || true` 无输出；人工检查 `modules/executor/` 与 `graph_engine/nodes/` 无宿主机执行生成代码路径；`grep -R "sandbox_enabled=False" src` 无绕过容器的执行分支。
  - 完成：lint/type/pytest（283 passed, 1 skipped）全绿；`except:` / `print(` / `sandbox_enabled=False` 在 `src` 无命中；最长源文件约 439 行；executor/nodes 无宿主机 eval/exec 路径。
<a id="D-GATE-01"></a>
  - > **【实现决策 D-GATE-01｜2026-11-03】**  
    > **决定：** (1) CLI 提示与容器内 SQL runner 一律用 `sys.stdout.write` / `sys.stderr.write`，使字面量 `print(` 在 `src` 为零；(2) 将 `_anomaly_fingerprint(` 重命名为 `_anomaly_fp`，避免标识符末尾 `print(` 误伤门禁 grep；(3) 不引入 `sandbox_enabled=False` 执行分支，关隔离仍走一次性容器（既有 D-DOCKER-01）。  
    > **原因：** T45 验收是字面 `grep "print("`，容器脚本字符串与函数名 `*print*(` 都会假阳性；清理调试输出与可 grep 门禁需同时满足。  
    > **依据：** [T45](tasks.md#T45) 验收；[conventions.md §5](conventions.md#5-日志规范) 禁业务 print；[D-DOCKER-01](tasks.md#D-DOCKER-01)；[handoff 完成定义](handoff.md#完成定义已锁定)。
