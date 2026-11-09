<script setup lang="ts">
import type { TaskEvent } from "../types";
import { formatPayload, humanSteps } from "../utils/traceMeta";

defineProps<{
  events: TaskEvent[];
  status: string;
}>();
</script>

<template>
  <div class="trace">
    <details v-if="events.length" class="step-fold" open>
      <summary class="step-fold-summary">
        <span class="step-fold-title">执行步骤（{{ humanSteps(events).length }}）</span>
        <span class="pill">{{ status }}</span>
      </summary>
      <ol class="step-list">
        <li v-for="(step, idx) in humanSteps(events)" :key="`${idx}-${step}`">
          {{ step }}
        </li>
      </ol>
    </details>
    <p v-else class="muted">暂无步骤</p>

    <details v-if="events.length" class="trace-advanced">
      <summary class="muted">高级：完整事件轨迹</summary>
      <ul class="trace-list">
        <li v-for="ev in events" :key="ev.seq">
          <span class="seq">#{{ ev.seq }}</span>
          <span class="type">{{ ev.event_type }}</span>
          <span class="payload">{{ formatPayload(ev) }}</span>
        </li>
      </ul>
    </details>
  </div>
</template>
