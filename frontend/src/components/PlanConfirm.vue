<script setup lang="ts">
import { ref } from "vue";
import { confirmPlan } from "../api/client";

const props = defineProps<{
  taskId: string;
  visible: boolean;
}>();

const emit = defineEmits<{
  done: [];
}>();

const busy = ref(false);
const message = ref("");
const error = ref("");

async function decide(decision: "accept" | "reject") {
  busy.value = true;
  error.value = "";
  message.value = "";
  try {
    const key = `${props.taskId}-${decision}-v1`;
    const resp = await confirmPlan(props.taskId, decision, key);
    message.value = resp.mutated
      ? `已${decision === "accept" ? "批准" : "拒绝"}计划`
      : "重复确认（幂等，未再次变更）";
    emit("done");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div v-if="visible" class="confirm-box">
    <h3>计划确认</h3>
    <p>任务需要人工确认后才能继续执行。</p>
    <div class="row">
      <button type="button" class="primary" :disabled="busy" @click="decide('accept')">
        批准计划
      </button>
      <button type="button" :disabled="busy" @click="decide('reject')">拒绝</button>
    </div>
    <p v-if="message" class="ok">{{ message }}</p>
    <p v-if="error" class="err">{{ error }}</p>
  </div>
</template>
