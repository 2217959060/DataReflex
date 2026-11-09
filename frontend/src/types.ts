export interface StrategyFlags {
  enable_planner?: boolean;
  enable_reflector?: boolean;
  enable_sandbox?: boolean;
  enable_human_confirm?: boolean;
  enable_fallback_report?: boolean;
}

export interface TaskSubmitResp {
  task_id: string;
  queue_position: number;
  accepted: boolean;
  reject_reason: string | null;
}

export interface TaskEvent {
  task_id: string;
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  timestamp: string;
}

export interface ChartSpec {
  chart_type: "bar" | "line" | "table";
  title: string;
  x_field: string | null;
  y_field: string | null;
  series: Array<Record<string, unknown>>;
  alternative_note: string | null;
}

export interface ReportTable {
  sub_task_id?: string;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  row_count?: number;
}

export interface ReportResp {
  summary: string;
  tables: ReportTable[];
  charts: ChartSpec[];
  source_map: Record<string, unknown>;
  partial_success: boolean;
}

export interface SubTaskResultView {
  sub_task_id: string;
  status: string;
  result_rows?: Array<Record<string, unknown>>;
  result_columns?: string[];
  error_summary?: string | null;
  duration_ms?: number;
}

/** One prior turn for V2-5 multi-turn follow-ups (D-CTX-01 keys). */
export interface ConversationTurn {
  dataset_id: string;
  question_tag?: string | null;
  user_query: string;
  constraints?: string[];
  plan?: Record<string, unknown> | null;
  /** Summary only — do not ship full result tables. */
  results?: { row_count?: number } | null;
}

export interface IntentView {
  in_scope?: boolean;
  need_clarification?: boolean;
  question_tag?: string | null;
  constraints?: string[];
  normalized_query?: string | null;
  clarification_questions?: string[];
}

export interface TaskTrace {
  task_id: string;
  status: string;
  terminate_reason: string | null;
  events: TaskEvent[];
  audit_events: Array<Record<string, unknown>>;
  sub_task_results: SubTaskResultView[];
  final_report: ReportResp | null;
  fallback_report: Record<string, unknown> | null;
  plan: Record<string, unknown> | null;
  intent?: IntentView | null;
  queue_position: number;
  user_query?: string;
  dataset_id?: string;
}

export interface ImportResp {
  dataset_id: string;
  success: boolean;
  tables: Array<Record<string, unknown>>;
  warnings: string[];
  error_msg: string | null;
}

export interface TaskCard {
  task_id: string;
  dataset_id: string;
  user_query: string;
  status: string;
  events: TaskEvent[];
  lastSeq: number;
  final_report: ReportResp | null;
  fallback_report: Record<string, unknown> | null;
  plan: Record<string, unknown> | null;
  intent?: IntentView | null;
  sub_task_results?: SubTaskResultView[];
  error?: string;
  /** UI: whether detail body is expanded. */
  expanded?: boolean;
  /** Submission order (1-based, oldest first). */
  seq?: number;
  /** UI: this card's refresh button is in-flight. */
  refreshing?: boolean;
}
