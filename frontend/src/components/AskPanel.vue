<script setup lang="ts">
import { ref, watch } from "vue";
import { submitTask } from "../api/client";
import type { ConversationTurn, StrategyFlags, TaskCard } from "../types";

const props = defineProps<{
  datasetId: string;
  /** When parent sets this, refill the question box and focus it. */
  draftQuery?: string;
  /** Last 1–2 turns for multi-turn follow-ups (V2-5). */
  conversationContext?: ConversationTurn[];
}>();

const emit = defineEmits<{
  submitted: [task: TaskCard];
  "update:draftQuery": [value: string];
}>();

const query = ref("对比 APP 和官网 渠道销售额");
const enableHumanConfirm = ref(false);
const busy = ref(false);
const error = ref("");
const textareaRef = ref<HTMLTextAreaElement | null>(null);

watch(
  () => props.draftQuery,
  (v) => {
    if (v === undefined || v === "") return;
    query.value = v;
    emit("update:draftQuery", "");
    void Promise.resolve().then(() => {
      textareaRef.value?.focus();
      textareaRef.value?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  },
);

async function onAsk() {
  error.value = "";
  if (!props.datasetId) {
    error.value = "请先上传并获得 dataset_id";
    return;
  }
  if (!query.value.trim()) {
    error.value = "请输入分析问题";
    return;
  }
  busy.value = true;
  try {
    const flags: StrategyFlags = {
      enable_human_confirm: enableHumanConfirm.value,
      enable_planner: true,
      enable_reflector: true,
      enable_sandbox: true,
      enable_fallback_report: true,
    };
    const ctx = (props.conversationContext || []).slice(-2);
    const resp = await submitTask({
      dataset_id: props.datasetId,
      user_query: query.value.trim(),
      strategy_flags: flags,
      conversation_context: ctx,
    });
    emit("submitted", {
      task_id: resp.task_id,
      dataset_id: props.datasetId,
      user_query: query.value.trim(),
      status: "queued",
      events: [],
      lastSeq: -1,
      final_report: null,
      fallback_report: null,
      plan: null,
      intent: null,
      sub_task_results: [],
      expanded: true,
      refreshing: false,
    });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

defineExpose({
  setQuery(text: string) {
    query.value = text;
    textareaRef.value?.focus();
  },
});
</script>

<template>
  <section class="panel">
    <h2>2. 提问</h2>
    <label class="field">
      <span>自然语言问题</span>
      <textarea
        ref="textareaRef"
        v-model="query"
        rows="3"
        placeholder="例如：各渠道销售额对比"
      />
    </label>
    <p class="muted followup-hint">
      同一数据集可追问，例如「同上，排除 APP」。不解「刚才那个渠道」等实体指代时会澄清。
      <span v-if="(conversationContext?.length ?? 0) > 0">
        （已携带上 {{ Math.min(conversationContext!.length, 2) }} 轮上下文）
      </span>
    </p>
    <label class="check">
      <input v-model="enableHumanConfirm" type="checkbox" />
      开启计划人工确认
    </label>
    <div class="row">
      <button type="button" :disabled="busy || !datasetId" @click="onAsk">
        {{ busy ? "提交中…" : "提交任务" }}
      </button>
      <span class="muted">当前 dataset: {{ datasetId || "（未导入）" }}</span>
    </div>
    <p v-if="error" class="err">{{ error }}</p>
  </section>
</template>
