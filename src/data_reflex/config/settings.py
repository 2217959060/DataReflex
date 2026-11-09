# -*- coding: utf-8 -*-
"""集中配置：从环境变量 / ``.env`` 加载；默认值锁定见 ``docs/defaults.md``。

上游：进程启动、各模块 ``get_settings()``。
本文件：LLM、策略开关、限额、沙箱、Langfuse、评测时钟、敏感字段等。
下游：Graph / Queue / 执行器 / Tracer 行为均由此驱动。

改 ``.env`` 后需重启 API 进程才生效。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用设置（环境变量 / .env）；字段说明见各 Field description。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM ---
    llm_api_key: str = Field(default="", description="LLM API key (never hardcode)")
    llm_base_url: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4/",
        description="OpenAI-compatible API base URL",
    )
    llm_model: str = Field(default="glm-4-flash-250414", description="Primary chat model id")
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    enable_llm_ops: bool = Field(
        default=True,
        description=(
            "v2-1/v2-4: when True and LLM_API_KEY set, Graph may call GLM for "
            "GenerateOp / clarify / Reflect hints / conclusion polish; "
            "no key or any failure always falls back to templates"
        ),
    )

    # --- Strategy flags (defaults.md §1) ---
    enable_planner: bool = True
    enable_reflector: bool = True
    enable_sandbox: bool = True
    enable_human_confirm: bool = True
    enable_fallback_report: bool = True

    # --- Limits & timeouts (defaults.md §2) ---
    max_retry_rounds: int = Field(default=3, ge=0)
    max_llm_calls_per_task: int = Field(default=20, ge=1)
    plan_confirm_timeout_seconds: int = Field(default=300, ge=1)
    sandbox_timeout_seconds: int = Field(default=60, ge=1)
    sandbox_memory_limit_mb: int = Field(default=512, ge=64)
    sandbox_cpu_limit: float = Field(default=1.0, gt=0.0)
    max_upload_files: int = Field(default=5, ge=1)
    max_file_size_mb: int = Field(default=20, ge=1)

    # --- Sandbox image (defaults.md §5) ---
    sandbox_backend: Literal["mock", "docker"] = Field(
        default="mock",
        description="mock=canned rows for demo; docker=one-shot container (needs daemon+image)",
    )
    sandbox_image: str = Field(
        default="data-reflex-sandbox:3.11",
        description="Prebuilt one-shot analysis image",
    )

    # --- Persistence / observability ---
    sqlite_path: str = Field(default="data/data_reflex.db")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    # --- Eval clock (defaults.md §7) ---
    eval_fixed_clock: str = Field(default="2025-10-01T00:00:00+08:00")
    eval_timezone: str = Field(default="Asia/Shanghai")

    # --- New-topic rule id (defaults.md §3; logic lives in context manager) ---
    new_topic_rule: str = Field(
        default="dataset_or_label_change_without_anaphora",
        description="Fixed rule id; do not hardcode variants inside nodes",
    )

    # --- Sensitive field names (defaults.md §4) ---
    sensitive_fields: list[str] = Field(
        default_factory=lambda: [
            "phone",
            "mobile",
            "email",
            "id_card",
            "address",
            "手机",
            "手机号",
            "电话",
            "邮箱",
            "身份证",
            "身份证号",
            "地址",
        ]
    )

    @field_validator("sensitive_fields", mode="before")
    @classmethod
    def _parse_sensitive_fields(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            return parts
        return value


@lru_cache
def get_settings() -> Settings:
    """进程级缓存的 Settings 单例（改 .env 后需重启进程）。"""
    return Settings()
