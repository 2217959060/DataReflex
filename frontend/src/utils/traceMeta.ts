import type { TaskEvent } from "../types";

const NODE_LABELS: Record<string, string> = {
  intent_parse: "理解问题意图",
  plan: "生成分析计划",
  plan_validate: "校验分析计划",
  human_confirm: "等待人工确认",
  generate_op: "生成分析操作",
  safety_check: "安全校验",
  execute: "沙箱执行计算",
  result_validate: "校验执行结果",
  error_classify: "错误分类",
  reflect: "反思并重试",
  report: "汇总分析报告",
  fallback_report: "生成降级说明",
};

const EVENT_LABELS: Record<string, string> = {
  task_started: "任务开始",
  waiting_confirm: "等待人工确认",
  task_completed: "任务完成",
  task_failed: "任务失败",
  task_terminated: "任务已终止",
};

export function nodeLabel(node: string): string {
  return NODE_LABELS[node] || node;
}

export function formatPayload(ev: TaskEvent): string {
  const p = ev.payload || {};
  if (typeof p.node === "string") return String(p.node);
  if (typeof p.status === "string") return String(p.status);
  if (typeof p.reason === "string") return String(p.reason);
  if (typeof p.error === "string") return String(p.error);
  return "";
}

/** Ordered, de-duplicated human-readable steps for the demo view. */
export function humanSteps(events: TaskEvent[]): string[] {
  const steps: string[] = [];
  const push = (label: string) => {
    if (!label) return;
    if (steps[steps.length - 1] === label) return;
    steps.push(label);
  };

  for (const ev of events) {
    if (ev.event_type === "node_entered") {
      const node = typeof ev.payload?.node === "string" ? ev.payload.node : "";
      push(nodeLabel(node));
      continue;
    }
    const fixed = EVENT_LABELS[ev.event_type];
    if (fixed) push(fixed);
  }
  return steps;
}
