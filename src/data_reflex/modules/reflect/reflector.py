# -*- coding: utf-8 -*-
"""自愈决策：该不该再试一次，还是熔断结束。

在 Graph 里的位置（同一任务内部，通常不弹前端澄清框）：
  执行/校验失败 → ErrorClassify（分类+模板修复建议）
                → Reflector（本文件：熔断 or 重试 + fix_hint）
                → 若重试则回到 GenerateOp（带着 fix_hint 再生成）

See ``tasks.md`` T28 / D-REFLECT-01 and roadmap V2-1 (optional LLM fix hints).
"""

from __future__ import annotations

from typing import Literal

from data_reflex.config.settings import Settings, get_settings
from data_reflex.models.schemas import ReflectReq, ReflectResp
from data_reflex.modules.llm.client import LlmClient, LlmError, build_llm_client
from data_reflex.modules.llm.prompts import clip_hint, reflect_fix_hint_messages
from data_reflex.utils.logger import get_logger

logger = get_logger(__name__)

# 熔断原因（熔断 = 不再自愈，走向 Report）
MeltReason = Literal["duplicate_fingerprint", "retry_limit", "llm_call_limit"]


class Reflector:
    """熔断规则优先级：LLM 调用上限 → 连续相同错误指纹 → 重试次数上限。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        llm: LlmClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._llm = llm
        # 本次 reflect 若调了 LLM 润色 hint，记 1，供节点累加 llm_calls_used
        self.last_llm_calls: int = 0

    def reflect(self, req: ReflectReq) -> ReflectResp:
        """主入口：先看要不要熔断；否则给出 fix_hint 并标记应重试。"""
        self.last_llm_calls = 0
        fp = req.error_classify.error_fingerprint

        # --- 分支 A：熔断（不重试）---
        melt = self._melt_reason(req, fp)
        if melt is not None:
            logger.info(
                "reflect melt",
                extra={
                    "task_id": "-",
                    "dataset_id": "-",
                    "melt_reason": melt,
                    "retry_count": req.retry_count,
                },
            )
            return ReflectResp(
                should_retry=False,
                circuit_breaker=True,
                melt_reason=melt,
                fix_hint=None,
                final_status="melted",
            )

        # --- 分支 B：错误不可重试，或其实没有错误 ---
        if not req.error_classify.retryable or req.error_classify.error_type == "none":
            return ReflectResp(
                should_retry=False,
                circuit_breaker=False,
                melt_reason=None,
                fix_hint=None,
                final_status="failed",
            )

        # --- 分支 C：可以自愈 → 准备修复提示，让 Graph 再去 GenerateOp ---
        # 默认用 ErrorClassify 的模板 suggested_fix；有 Key 时可 LLM 润成中文短 hint
        hint = req.error_classify.suggested_fix
        hint = self._maybe_enrich_hint(req, hint)
        logger.info(
            "reflect retry",
            extra={
                "task_id": "-",
                "dataset_id": "-",
                "retry_count": req.retry_count,
                "fingerprint": fp[:80] if fp else "",
            },
        )
        return ReflectResp(
            should_retry=True,
            circuit_breaker=False,
            melt_reason=None,
            fix_hint=hint,  # 写入状态后，下一轮生成会读到
            final_status="retrying",
        )

    def _melt_reason(self, req: ReflectReq, fp: str) -> MeltReason | None:
        """返回熔断原因；None 表示尚未触达上限，仍可考虑重试。"""
        # 1) 本任务 LLM 调用次数用尽（含 GenerateOp / Reflect 等）
        if req.llm_calls_used >= req.max_llm_calls:
            return "llm_call_limit"

        # 2) 连续两次相同错误指纹 → 再生成多半还是同样挂，停止空转
        if _consecutive_duplicate(req.previous_fingerprints, fp):
            return "duplicate_fingerprint"

        # 3) 该子任务重试次数达到 MAX_RETRY_ROUNDS
        if req.retry_count >= req.max_retry:
            return "retry_limit"

        return None

    def _maybe_enrich_hint(self, req: ReflectReq, hint: str | None) -> str | None:
        """可选：用 LLM 把模板建议润成一句短中文；失败则退回原 hint。"""
        if not (self._settings.enable_llm_ops and self._settings.llm_api_key.strip()):
            return hint
        # 与熔断共用同一 LLM 预算，避免润色本身把额度刷爆
        if req.llm_calls_used >= req.max_llm_calls:
            return hint
        client = self._llm or build_llm_client(self._settings)
        try:
            text = client.chat(
                messages=reflect_fix_hint_messages(
                    sub_task=req.sub_task,
                    error_detail=req.error_classify.error_detail,
                    suggested_fix=hint,
                ),
                max_tokens=120,
            )
            self.last_llm_calls = 1
            polished = clip_hint(text)
            return polished or hint
        except LlmError as exc:
            # 润色失败不影响自愈：仍用分类器给的模板建议
            logger.info(
                "reflect llm hint skipped",
                extra={
                    "task_id": "-",
                    "dataset_id": "-",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return hint


def _consecutive_duplicate(fingerprints: list[str], current: str) -> bool:
    """判断是否「连续两次」撞上同一个错误指纹。

    ErrorClassify 会在 Reflect 之前把当前指纹 append 进历史，
    因此第二次相同失败时历史末尾是 ``[..., fp, fp]``。
    """
    if not current or len(fingerprints) < 2:
        return False
    return fingerprints[-1] == fingerprints[-2] == current
