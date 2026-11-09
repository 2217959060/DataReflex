# -*- coding: utf-8 -*-
"""领域请求/响应模型（与 design.md IO 对齐）。

各 Graph 节点与 modules 之间传递的结构化数据；字段以类型与 Field 为准，少写行内注释。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from data_reflex.models.base import StrictModel

# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


class ColumnSchema(StrictModel):
    name: str
    dtype: str
    null_ratio: float = Field(ge=0.0, le=1.0)
    sample_values: list[str] = Field(default_factory=list)


class JoinHint(StrictModel):
    left_file: str
    left_column: str
    right_file: str
    right_column: str
    confidence: float = Field(ge=0.0, le=1.0)


class TableSchema(StrictModel):
    file_name: str
    row_count: int = Field(ge=0)
    columns: list[ColumnSchema] = Field(default_factory=list)
    join_hints: list[JoinHint] = Field(default_factory=list)


class SubTask(StrictModel):
    task_id: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    expected_input: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    parallel_group: int = Field(default=0, ge=0)


class SubTaskResult(StrictModel):
    """Per-sub-task execution outcome used by report / fallback modules."""

    sub_task_id: str
    status: Literal[
        "pending",
        "running",
        "success",
        "failed",
        "healing",
        "terminated",
    ] = "pending"
    result_rows: list[dict[str, Any]] = Field(default_factory=list)
    result_columns: list[str] = Field(default_factory=list)
    error_summary: str | None = None
    duration_ms: int = Field(default=0, ge=0)


class ChartSpec(StrictModel):
    """v1 charts: bar, line, or table; unsupported types use alternative_note."""

    chart_type: Literal["bar", "line", "table"]
    title: str = ""
    x_field: str | None = None
    y_field: str | None = None
    series: list[dict[str, Any]] = Field(default_factory=list)
    alternative_note: str | None = None


class EvalMetric(StrictModel):
    config: dict[str, Any] = Field(default_factory=dict)
    first_pass_rate: float = Field(ge=0.0, le=1.0)
    post_heal_rate: float = Field(ge=0.0, le=1.0)
    execution_success_rate: float = Field(ge=0.0, le=1.0)
    result_accuracy: float = Field(ge=0.0, le=1.0)
    avg_steps: float = Field(ge=0.0)
    avg_llm_calls: float = Field(ge=0.0)
    avg_duration_ms: int = Field(ge=0)
    self_heal_rate: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Data import
# ---------------------------------------------------------------------------


class DataImportReq(StrictModel):
    file_paths: list[str] = Field(min_length=1)
    file_encoding: str | None = None


class DataImportResp(StrictModel):
    dataset_id: str
    success: bool
    tables: list[TableSchema] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_msg: str | None = None


# ---------------------------------------------------------------------------
# Intent / plan / generate
# ---------------------------------------------------------------------------


class IntentParseReq(StrictModel):
    dataset_id: str
    user_query: str
    conversation_context: list[dict[str, Any]] = Field(default_factory=list)


class IntentParseResp(StrictModel):
    in_scope: bool
    reject_reason: str | None = None
    need_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    normalized_query: str | None = None
    constraints: list[str] = Field(default_factory=list)
    question_tag: (
        Literal[
            "return_rate_topn",
            "channel_compare",
            "time_trend",
            "segment_compare",
            "promo_compare",
            "repurchase_rate",
        ]
        | None
    ) = None


class PlanReq(StrictModel):
    dataset_id: str
    normalized_query: str
    constraints: list[str] = Field(default_factory=list)
    conversation_context: list[dict[str, Any]] = Field(default_factory=list)


class PlanResp(StrictModel):
    success: bool
    sub_tasks: list[SubTask] = Field(default_factory=list)
    has_cycle: bool = False
    cycle_detail: str | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    error_msg: str | None = None


class GenerateOpReq(StrictModel):
    sub_task: SubTask
    table_schemas: list[TableSchema] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    previous_error: str | None = None


class GenerateOpResp(StrictModel):
    op_type: Literal["sql", "python"]
    op_content: str
    expected_input: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    format_valid: bool
    error_msg: str | None = None


# ---------------------------------------------------------------------------
# Safety / execute
# ---------------------------------------------------------------------------


class SafetyCheckReq(StrictModel):
    op_type: Literal["sql", "python"]
    op_content: str
    allowed_columns: list[str] = Field(default_factory=list)


class SafetyCheckResp(StrictModel):
    passed: bool
    violation_type: (
        Literal["dangerous_write", "unauthorized_column", "unparseable"] | None
    ) = None
    violation_detail: str | None = None


class ExecuteReq(StrictModel):
    op_type: Literal["sql", "python"]
    op_content: str
    dataset_id: str
    timeout_seconds: int = Field(gt=0)
    memory_limit_mb: int = Field(gt=0)
    cpu_limit: float = Field(gt=0.0)


class ExecuteResp(StrictModel):
    success: bool
    result_rows: list[dict[str, Any]] = Field(default_factory=list)
    result_columns: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = Field(default=0, ge=0)
    resource_killed: bool = False


# ---------------------------------------------------------------------------
# Error classify / reflect / result validate
# ---------------------------------------------------------------------------


class ErrorClassifyReq(StrictModel):
    execute_resp: ExecuteResp
    op_type: Literal["sql", "python"]


class ErrorClassifyResp(StrictModel):
    error_type: Literal[
        "syntax",
        "schema_semantic",
        "runtime",
        "result_anomaly",
        "none",
    ]
    error_detail: str = ""
    error_fingerprint: str = ""
    retryable: bool = False
    suggested_fix: str | None = None


class ReflectReq(StrictModel):
    sub_task: SubTask
    error_classify: ErrorClassifyResp
    retry_count: int = Field(ge=0)
    max_retry: int = Field(ge=0)
    llm_calls_used: int = Field(ge=0)
    max_llm_calls: int = Field(ge=1)
    previous_fingerprints: list[str] = Field(default_factory=list)


class ReflectResp(StrictModel):
    should_retry: bool
    circuit_breaker: bool
    melt_reason: (
        Literal["duplicate_fingerprint", "retry_limit", "llm_call_limit"] | None
    ) = None
    fix_hint: str | None = None
    final_status: Literal["retrying", "failed", "melted"]


class ResultValidateReq(StrictModel):
    execute_resp: ExecuteResp
    structured_constraints: list[dict[str, Any]] = Field(default_factory=list)


class ResultValidateResp(StrictModel):
    valid: bool
    anomaly_type: (
        Literal[
            "empty_anomaly",
            "ratio_out_of_range",
            "type_anomaly",
            "constraint_mismatch",
        ]
        | None
    ) = None
    anomaly_detail: str | None = None


# ---------------------------------------------------------------------------
# Report / fallback
# ---------------------------------------------------------------------------


class ReportReq(StrictModel):
    task_id: str
    sub_task_results: list[SubTaskResult] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)


class ReportResp(StrictModel):
    summary: str
    tables: list[dict[str, Any]] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)
    source_map: dict[str, Any] = Field(default_factory=dict)
    partial_success: bool = False


class FallbackReportReq(StrictModel):
    task_id: str
    dataset_id: str
    failed_node: str
    failed_sub_tasks: list[str] = Field(default_factory=list)
    completed_results: list[SubTaskResult] = Field(default_factory=list)
    parsed_keywords: list[str] = Field(default_factory=list)
    table_schemas: list[TableSchema] = Field(default_factory=list)


class FallbackReportResp(StrictModel):
    summary: str
    data_overview: dict[str, Any] = Field(default_factory=dict)
    parse_status: dict[str, Any] = Field(default_factory=dict)
    failure_scope: dict[str, Any] = Field(default_factory=dict)
    manual_suggestions: list[str] = Field(default_factory=list)
    completed_results: list[dict[str, Any]] = Field(default_factory=list)
    is_analysis_conclusion: bool = False

    @field_validator("is_analysis_conclusion")
    @classmethod
    def _must_be_false(cls, value: bool) -> bool:
        if value is not False:
            raise ValueError("fallback report must set is_analysis_conclusion=False")
        return value


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------


class EvalRunReq(StrictModel):
    eval_set_id: str
    mode: Literal["mock", "real"]
    max_cases: int = Field(ge=1)
    strategy_matrix: dict[str, list[bool]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _only_planner_reflector(self) -> EvalRunReq:
        allowed = {"planner", "reflector"}
        extra = set(self.strategy_matrix) - allowed
        if extra:
            raise ValueError(
                "v1 strategy_matrix only allows keys planner/reflector; "
                f"got {sorted(extra)}"
            )
        return self


class EvalRunResp(StrictModel):
    report_path: str
    metrics: list[EvalMetric] = Field(default_factory=list)
    failed_cases: list[dict[str, Any]] = Field(default_factory=list)
    leakage_cases: list[str] = Field(default_factory=list)
    mode: Literal["mock", "real"]
