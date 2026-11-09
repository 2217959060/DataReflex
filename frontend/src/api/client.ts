import type {
  ConversationTurn,
  ImportResp,
  StrategyFlags,
  TaskSubmitResp,
  TaskTrace,
} from "../types";

const BASE = "";

async function parseJson<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = (body as { error?: { message?: string } }).error;
    throw new Error(err?.message || `HTTP ${res.status}`);
  }
  return body as T;
}

export type HealthResp = {
  status: string;
  sandbox_backend?: "mock" | "docker" | string;
};

export async function fetchHealth(): Promise<HealthResp> {
  const res = await fetch(`${BASE}/health`);
  return parseJson<HealthResp>(res);
}

export async function importDatasets(files: File[]): Promise<ImportResp> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f, f.name);
  }
  const res = await fetch(`${BASE}/api/datasets/import`, {
    method: "POST",
    body: form,
  });
  return parseJson<ImportResp>(res);
}

export async function submitTask(input: {
  dataset_id: string;
  user_query: string;
  strategy_flags?: StrategyFlags;
  conversation_context?: ConversationTurn[];
}): Promise<TaskSubmitResp> {
  const res = await fetch(`${BASE}/api/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseJson<TaskSubmitResp>(res);
}

export async function fetchTrace(taskId: string): Promise<TaskTrace> {
  const res = await fetch(`${BASE}/api/tasks/${encodeURIComponent(taskId)}/trace`);
  return parseJson<TaskTrace>(res);
}

export async function confirmPlan(
  taskId: string,
  decision: "accept" | "reject",
  idempotencyKey: string,
): Promise<{ mutated: boolean; decision: string }> {
  const res = await fetch(
    `${BASE}/api/tasks/${encodeURIComponent(taskId)}/confirm-plan`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task_id: taskId,
        confirm_type: "plan",
        decision,
        idempotency_key: idempotencyKey,
      }),
    },
  );
  return parseJson(res);
}

export async function terminateTask(
  taskId: string,
  reason: string,
): Promise<Record<string, unknown>> {
  const res = await fetch(
    `${BASE}/api/tasks/${encodeURIComponent(taskId)}/terminate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId, reason }),
    },
  );
  return parseJson(res);
}
