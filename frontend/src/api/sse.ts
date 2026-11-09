import type { TaskEvent } from "../types";

export type SseHandlers = {
  onEvent: (ev: TaskEvent) => void;
  onError?: (err: unknown) => void;
  onOpen?: () => void;
};

/**
 * Connect to task SSE. Reconnects after disconnect using after_seq = last seen seq.
 * Returns a dispose function.
 */
export function connectTaskStream(
  taskId: string,
  handlers: SseHandlers,
  initialAfterSeq = -1,
): () => void {
  let closed = false;
  let afterSeq = initialAfterSeq;
  let es: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    if (closed) return;
    const url = `/api/tasks/${encodeURIComponent(taskId)}/stream?after_seq=${afterSeq}`;
    es = new EventSource(url);
    es.onopen = () => handlers.onOpen?.();

    const handleMessage = (raw: MessageEvent) => {
      try {
        const data = JSON.parse(String(raw.data)) as TaskEvent;
        if (typeof data.seq === "number") {
          afterSeq = Math.max(afterSeq, data.seq);
        }
        handlers.onEvent(data);
      } catch (err) {
        handlers.onError?.(err);
      }
    };

    // Named SSE events from server (event: task_started / node_entered / ...)
    for (const name of [
      "task_started",
      "node_entered",
      "waiting_confirm",
      "task_completed",
      "task_terminated",
      "task_failed",
      "message",
    ]) {
      es.addEventListener(name, handleMessage as EventListener);
    }

    es.onerror = () => {
      handlers.onError?.(new Error("sse disconnected"));
      es?.close();
      es = null;
      // Caller may dispose() inside onError (e.g. task already terminal).
      if (closed) return;
      retryTimer = setTimeout(open, 800);
    };
  };

  open();

  return () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    es?.close();
    es = null;
  };
}
