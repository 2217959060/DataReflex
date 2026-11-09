<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { fetchHealth, fetchTrace, terminateTask } from "./api/client";
import { connectTaskStream } from "./api/sse";
import AskPanel from "./components/AskPanel.vue";
import PlanConfirm from "./components/PlanConfirm.vue";
import ResultCharts from "./components/ResultCharts.vue";
import ResultTable from "./components/ResultTable.vue";
import TaskTrace from "./components/TaskTrace.vue";
import UploadPanel from "./components/UploadPanel.vue";
import type {
  ConversationTurn,
  SubTaskResultView,
  TaskCard,
  TaskEvent,
} from "./types";
import {
  formatTableTitle,
  isKnownIntermediateId,
} from "./utils/resultMeta";

const INTERMEDIATE_PREVIEW_ROWS = 20;
const MAX_CONTEXT_TURNS = 2;

const datasetId = ref("");
const draftQuery = ref("");
const conversationContext = ref<ConversationTurn[]>([]);
const tasks = reactive<TaskCard[]>([]);
const disposers = new Map<string, () => void>();
const pollTimers = new Map<string, ReturnType<typeof setInterval>>();
/** Avoid re-recording the same finished task into conversation context. */
const rememberedTaskIds = new Set<string>();
/** V2-9: live sandbox mode from GET /health (null = not loaded yet). */
const sandboxBackend = ref<"mock" | "docker" | "unknown" | null>(null);

const sandboxBadgeLabel = computed(() => {
  if (sandboxBackend.value === "mock") {
    return "沙箱：Mock · 演示占位数据";
  }
  if (sandboxBackend.value === "docker") {
    return "沙箱：Docker · 容器内对 CSV 真实计算";
  }
  if (sandboxBackend.value === "unknown") {
    return "沙箱：未知（无法读取后端配置）";
  }
  return "沙箱：读取中…";
});

const sandboxBadgeClass = computed(() => {
  if (sandboxBackend.value === "docker") return "sandbox-badge sandbox-badge-docker";
  if (sandboxBackend.value === "mock") return "sandbox-badge sandbox-badge-mock";
  return "sandbox-badge sandbox-badge-unknown";
});

onMounted(() => {
  void fetchHealth()
    .then((h) => {
      const raw = String(h.sandbox_backend || "").toLowerCase();
      if (raw === "mock" || raw === "docker") {
        sandboxBackend.value = raw;
      } else {
        sandboxBackend.value = "unknown";
      }
    })
    .catch(() => {
      sandboxBackend.value = "unknown";
    });
});

const TERMINAL = new Set([
  "success",
  "failed",
  "fallback",
  "terminated",
]);

const EXAMPLE_QUERIES = [
  "对比 APP 和官网 渠道销售额",
  "退货率最高的前 5 个商品是哪些？",
  "最近订单金额按天的趋势？",
  "不同用户分层的下单情况对比",
  "促销单和非促销单的金额对比",
  "用户复购率是多少？",
];

function upsertEvent(task: TaskCard, ev: TaskEvent) {
  if (task.events.some((e) => e.seq === ev.seq)) return;
  task.events.push(ev);
  task.events.sort((a, b) => a.seq - b.seq);
  task.lastSeq = Math.max(task.lastSeq, ev.seq);
  if (ev.event_type === "waiting_confirm") {
    task.status = "waiting_confirm";
  }
  if (ev.event_type === "task_started" || ev.event_type === "node_entered") {
    if (task.status === "queued" || task.status === "pending") {
      task.status = "running";
    }
  }
  if (
    ev.event_type === "task_completed" ||
    ev.event_type === "task_terminated" ||
    ev.event_type === "task_failed"
  ) {
    const st = ev.payload?.status;
    if (typeof st === "string") task.status = st;
    else if (ev.event_type === "task_failed") task.status = "failed";
  }
}

function stopPolling(taskId: string) {
  const t = pollTimers.get(taskId);
  if (t) {
    clearInterval(t);
    pollTimers.delete(taskId);
  }
}

/** Stop /trace polling and SSE reconnects once a task has finished. */
function stopLiveUpdates(taskId: string) {
  stopPolling(taskId);
  disposers.get(taskId)?.();
  disposers.delete(taskId);
}

/** Terminal status alone is not enough — wait until report/error is present. */
function hasSettledOutput(task: TaskCard): boolean {
  if (task.final_report || task.fallback_report) return true;
  if (task.status === "failed" || task.status === "terminated") return true;
  if (task.error) return true;
  return false;
}

function isTaskSettled(task: TaskCard): boolean {
  return TERMINAL.has(task.status) && hasSettledOutput(task);
}

async function refreshTrace(task: TaskCard, opts?: { silent?: boolean }) {
  const showBusy = !opts?.silent;
  if (showBusy) {
    if (task.refreshing) return;
    task.refreshing = true;
  }
  try {
    const trace = await fetchTrace(task.task_id);
    task.status = trace.status;
    task.final_report = trace.final_report;
    task.fallback_report = trace.fallback_report;
    task.plan = trace.plan;
    task.intent = trace.intent ?? null;
    task.sub_task_results = trace.sub_task_results || [];
    task.error = "";
    for (const ev of trace.events) {
      upsertEvent(task, ev);
    }
    // Finished tasks must drop both poll + SSE, or the page keeps jumping.
    if (isTaskSettled(task)) {
      stopLiveUpdates(task.task_id);
      rememberTurn(task);
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    task.error = msg;
    // API 重启后内存任务会消失；继续轮询只会刷 404。
    if (/404|Not Found|unknown task/i.test(msg)) {
      task.status = "failed";
      stopLiveUpdates(task.task_id);
      task.error =
        "任务已失效（常见原因：后端重启后内存任务清空）。请重新上传并提交问题。";
    }
  } finally {
    if (showBusy) task.refreshing = false;
  }
}

function startPolling(task: TaskCard) {
  stopPolling(task.task_id);
  // Mock 路径往往 <200ms 结束，SSE 可能错过首包；轮询 /trace 兜底。
  // 不要仅凭 status=success 停轮询——可能尚未拉到 final_report。
  const timer = setInterval(() => {
    if (isTaskSettled(task)) {
      stopLiveUpdates(task.task_id);
      return;
    }
    void refreshTrace(task, { silent: true });
  }, 400);
  pollTimers.set(task.task_id, timer);
}

function attachStream(task: TaskCard) {
  disposers.get(task.task_id)?.();
  const dispose = connectTaskStream(
    task.task_id,
    {
      onEvent: (ev) => {
        upsertEvent(task, ev);
        if (
          ev.event_type === "task_completed" ||
          ev.event_type === "task_terminated" ||
          ev.event_type === "task_failed" ||
          ev.event_type === "waiting_confirm"
        ) {
          void refreshTrace(task, { silent: true });
        }
      },
      onError: () => {
        // Task finished → server closes SSE; do not reconnect / re-fetch (UI jump).
        if (isTaskSettled(task)) {
          stopLiveUpdates(task.task_id);
          return;
        }
        void refreshTrace(task, { silent: true });
      },
    },
    task.lastSeq,
  );
  disposers.set(task.task_id, dispose);
}

function onImported(id: string) {
  if (datasetId.value && datasetId.value !== id) {
    conversationContext.value = [];
  }
  datasetId.value = id;
}

/** Keep last 1–2 successful/in-scope turns for follow-up submits (V2-5). */
function rememberTurn(task: TaskCard) {
  if (rememberedTaskIds.has(task.task_id)) return;
  const intent = task.intent;
  if (!intent?.question_tag || intent.need_clarification) return;
  if (intent.in_scope === false) return;
  if (task.dataset_id !== datasetId.value) return;
  if (!(task.status === "success" || task.status === "fallback")) return;
  rememberedTaskIds.add(task.task_id);

  const rowCount =
    task.final_report?.tables?.reduce(
      (n, t) => n + (t.row_count ?? t.rows?.length ?? 0),
      0,
    ) ?? 0;

  const turn: ConversationTurn = {
    dataset_id: task.dataset_id,
    question_tag: intent.question_tag,
    user_query: task.user_query,
    constraints: Array.isArray(intent.constraints)
      ? [...intent.constraints]
      : [],
    plan: task.plan ? { sub_task_count: Object.keys(task.plan).length } : null,
    results: { row_count: rowCount },
  };

  const withoutDup = conversationContext.value.filter(
    (t) =>
      !(
        t.user_query === turn.user_query &&
        t.question_tag === turn.question_tag &&
        t.dataset_id === turn.dataset_id
      ),
  );
  conversationContext.value = [...withoutDup, turn].slice(-MAX_CONTEXT_TURNS);
}

function toggleExpanded(task: TaskCard) {
  task.expanded = !isExpanded(task);
}

function isExpanded(task: TaskCard): boolean {
  return task.expanded !== false;
}

const STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  pending: "等待中",
  running: "执行中",
  waiting_confirm: "待确认",
  success: "成功",
  failed: "失败",
  fallback: "已降级",
  terminated: "已终止",
  needs_clarification: "待澄清",
};

function needsClarification(task: TaskCard): boolean {
  const raw = String(task.final_report?.summary || "");
  return raw.includes("【需要澄清】");
}

/** UI 展示状态：澄清场景不显示「失败」。 */
function displayStatus(task: TaskCard): string {
  if (needsClarification(task)) return "needs_clarification";
  return task.status;
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status;
}

function clarificationText(task: TaskCard): string {
  const raw = String(task.final_report?.summary || "");
  const line = raw.split("\n").find((x) => x.includes("【需要澄清】")) || raw;
  return line.replace(/^【需要澄清】\s*/, "").trim() || line.trim();
}

function humanSummary(task: TaskCard): string {
  const report = task.final_report;
  if (!report) return "";
  if (needsClarification(task)) return clarificationText(task);
  const raw = String(report.summary || "").trim();
  // Prefer Chinese conclusion line(s); drop machine audit trail (task=...).
  if (raw) {
    const lines = raw
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean)
      .filter((x) => !/^task=/.test(x));
    if (lines.length) return lines.join(" ");
  }
  const n = report.tables?.length ?? 0;
  const st = statusLabel(displayStatus(task));
  return `问题「${task.user_query}」已处理完毕（${st}），共产出 ${n} 张结果表。`;
}

function showRetryActions(task: TaskCard): boolean {
  return (
    needsClarification(task) ||
    task.status === "failed" ||
    task.status === "terminated" ||
    task.status === "fallback" ||
    Boolean(task.error)
  );
}

/** Intermediate / hidden-from-report rows for答辩与沙箱调试（与最终结果表去重）. */
function intermediateResults(task: TaskCard): SubTaskResultView[] {
  const all = task.sub_task_results || [];
  const reported = new Set(
    (task.final_report?.tables || [])
      .map((t) => t.sub_task_id)
      .filter((id): id is string => Boolean(id)),
  );
  const planSteps = Array.isArray(
    (task.plan as { sub_tasks?: Array<Record<string, unknown>> } | null)?.sub_tasks,
  )
    ? (
        task.plan as {
          sub_tasks: Array<{
            task_id?: string;
            expected_output?: { report?: boolean };
          }>;
        }
      ).sub_tasks
    : [];
  const hideByPlan = new Set(
    planSteps
      .filter((st) => st.expected_output?.report === false)
      .map((st) => String(st.task_id || ""))
      .filter(Boolean),
  );

  return all.filter((r) => {
    if (r.status !== "success") return false;
    if (!(r.result_rows || []).length) return false;
    // Already in the formal report → skip.
    if (reported.has(r.sub_task_id)) return false;
    // Prefer plan / known intermediate ids; otherwise any success omitted from report.
    if (hideByPlan.has(r.sub_task_id) || isKnownIntermediateId(r.sub_task_id)) {
      return true;
    }
    return reported.size > 0;
  });
}

function resultColumns(res: SubTaskResultView): string[] {
  if (res.result_columns?.length) return res.result_columns;
  const row = res.result_rows?.[0];
  return row ? Object.keys(row) : [];
}

function previewRows(res: SubTaskResultView): Array<Record<string, unknown>> {
  return (res.result_rows || []).slice(0, INTERMEDIATE_PREVIEW_ROWS);
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function refillQuery(text: string) {
  draftQuery.value = text;
}

function onSubmitted(task: TaskCard) {
  // 新任务默认展开，其余折叠；旧任务在上（按提问先后）。
  for (const t of tasks) {
    t.expanded = false;
  }
  task.expanded = true;
  task.seq = tasks.length + 1;
  task.refreshing = false;
  tasks.push(task);
  // 必须用数组里的响应式代理：对 push 前的原始对象赋值，界面不会更新，
  // 表现就是一直「排队中」，只有点「刷新状态」（拿到的是代理）才变成成功。
  const card = tasks[tasks.length - 1]!;
  attachStream(card);
  void refreshTrace(card, { silent: true });
  startPolling(card);
}

function taskOrdinal(task: TaskCard): number {
  return task.seq ?? tasks.indexOf(task) + 1;
}

async function onConfirmDone(task: TaskCard) {
  await refreshTrace(task, { silent: true });
  // Resume may produce new SSE events; keep stream alive.
  attachStream(task);
}

async function onTerminate(task: TaskCard) {
  try {
    await terminateTask(task.task_id, "user_cancel");
    await refreshTrace(task, { silent: true });
  } catch (e) {
    task.error = e instanceof Error ? e.message : String(e);
  }
}

onBeforeUnmount(() => {
  for (const d of disposers.values()) d();
  disposers.clear();
  for (const id of [...pollTimers.keys()]) stopPolling(id);
});
</script>

<template>
  <div class="app">
    <header class="hero">
      <p class="brand">DataReflex</p>
      <h1>自然语言分析工作台</h1>
      <p class="tagline">上传固定三表 → 提问 → 轨迹 / 确认 → 表格与图表</p>
      <div class="hero-sandbox-row">
        <span
          :class="sandboxBadgeClass"
          role="status"
          :title="`SANDBOX_BACKEND=${sandboxBackend ?? '…'}`"
        >
          {{ sandboxBadgeLabel }}
        </span>
        <p class="muted hero-sandbox-note">
          改 <code>.env</code> 中 <code>SANDBOX_BACKEND</code> 后需重启 API。
        </p>
      </div>
    </header>

    <UploadPanel @imported="onImported" />
    <AskPanel
      :dataset-id="datasetId"
      :draft-query="draftQuery"
      :conversation-context="conversationContext"
      @submitted="onSubmitted"
      @update:draft-query="draftQuery = $event"
    />

    <section class="panel">
      <h2>3. 任务状态</h2>
      <p v-if="!tasks.length" class="muted">尚未提交任务</p>
      <article
        v-for="task in tasks"
        :key="task.task_id"
        class="task-card"
        :class="{ collapsed: !isExpanded(task) }"
      >
        <div class="task-meta">
          <div class="task-summary">
            <div class="task-query">
              <span class="task-seq">任务 {{ taskOrdinal(task) }}</span>
              {{ task.user_query }}
            </div>
            <details class="debug-fold">
              <summary class="muted">调试信息</summary>
              <div class="muted task-id-line">
                <strong>task_id</strong> {{ task.task_id }}
              </div>
              <div class="muted">dataset: {{ task.dataset_id }}</div>
              <div
                v-if="intermediateResults(task).length"
                class="debug-intermediate"
              >
                <div class="muted debug-intermediate-label">
                  中间结果（未进正式报告，答辩/沙箱核对用）
                </div>
                <div
                  v-for="res in intermediateResults(task)"
                  :key="res.sub_task_id"
                  class="debug-intermediate-block"
                >
                  <div class="table-title">
                    {{ formatTableTitle(res.sub_task_id, 0) }}
                    <span class="muted">
                      ({{ res.result_rows?.length ?? 0 }} 行
                      <template
                        v-if="
                          (res.result_rows?.length ?? 0) > INTERMEDIATE_PREVIEW_ROWS
                        "
                      >
                        · 预览前 {{ INTERMEDIATE_PREVIEW_ROWS }} 行
                      </template>
                      )
                    </span>
                  </div>
                  <div class="table-wrap debug-table-wrap">
                    <table v-if="resultColumns(res).length">
                      <thead>
                        <tr>
                          <th
                            v-for="col in resultColumns(res)"
                            :key="col"
                          >
                            {{ col }}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr
                          v-for="(row, rIdx) in previewRows(res)"
                          :key="rIdx"
                        >
                          <td
                            v-for="col in resultColumns(res)"
                            :key="col"
                          >
                            {{ formatCell(row[col]) }}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
              <p v-else class="muted debug-intermediate-empty">
                暂无中间结果（任务完成后若有 join/聚合中间步会显示在此）
              </p>
            </details>
          </div>
          <div class="row task-actions">
            <button
              type="button"
              class="toggle-btn"
              :aria-expanded="isExpanded(task)"
              :title="isExpanded(task) ? '收起详情' : '展开详情'"
              @click="toggleExpanded(task)"
            >
              {{ isExpanded(task) ? "▼ 收起" : "▶ 展开" }}
            </button>
            <span
              class="pill"
              :class="{
                'pill-clarify': displayStatus(task) === 'needs_clarification',
                'pill-ok': displayStatus(task) === 'success',
                'pill-bad':
                  displayStatus(task) === 'failed' ||
                  displayStatus(task) === 'terminated',
              }"
            >
              {{ statusLabel(displayStatus(task)) }}
            </span>
            <button
              v-if="!TERMINAL.has(task.status)"
              type="button"
              @click="onTerminate(task)"
            >
              终止
            </button>
            <button
              type="button"
              class="ghost"
              :disabled="task.refreshing"
              @click="refreshTrace(task)"
            >
              {{ task.refreshing ? "刷新中…" : "刷新状态" }}
            </button>
          </div>
        </div>

        <div v-show="isExpanded(task)" class="task-body">
          <TaskTrace
            :events="task.events"
            :status="statusLabel(displayStatus(task))"
          />

          <PlanConfirm
            :task-id="task.task_id"
            :visible="task.status === 'waiting_confirm'"
            @done="onConfirmDone(task)"
          />

          <div
            v-if="task.final_report && needsClarification(task)"
            class="clarify-card"
          >
            <h3>需要澄清</h3>
            <p class="clarify-body">{{ clarificationText(task) }}</p>
            <p class="muted clarify-hint">
              请补充更具体的分析目标后重新提问（例如指定指标、维度或对比方式）。
            </p>
            <div class="retry-actions">
              <button
                type="button"
                class="primary"
                @click="refillQuery(task.user_query)"
              >
                改完再问（回填原问题）
              </button>
              <div class="example-chips">
                <span class="muted example-label">试试这些问法：</span>
                <button
                  v-for="ex in EXAMPLE_QUERIES"
                  :key="ex"
                  type="button"
                  class="chip"
                  @click="refillQuery(ex)"
                >
                  {{ ex }}
                </button>
              </div>
            </div>
          </div>

          <div v-else-if="task.final_report" class="report">
            <h3>分析报告</h3>
            <p class="report-summary">{{ humanSummary(task) }}</p>
            <p v-if="task.final_report.partial_success" class="warn">部分成功</p>
            <ResultTable :tables="task.final_report.tables || []" />
            <ResultCharts
              :charts="task.final_report.charts || []"
              :tables="task.final_report.tables || []"
            />
          </div>

          <div v-else-if="task.fallback_report" class="report">
            <h3>降级报告（非分析结论）</h3>
            <p>{{ String(task.fallback_report.summary || "") }}</p>
          </div>

          <p v-if="task.error" class="err">{{ task.error }}</p>

          <div
            v-if="showRetryActions(task) && !needsClarification(task)"
            class="retry-actions"
          >
            <button
              type="button"
              class="primary"
              @click="refillQuery(task.user_query)"
            >
              改完再问（回填原问题）
            </button>
            <div class="example-chips">
              <span class="muted example-label">或换一句示例：</span>
              <button
                v-for="ex in EXAMPLE_QUERIES.slice(0, 3)"
                :key="ex"
                type="button"
                class="chip"
                @click="refillQuery(ex)"
              >
                {{ ex }}
              </button>
            </div>
          </div>
        </div>
      </article>
    </section>
  </div>
</template>
