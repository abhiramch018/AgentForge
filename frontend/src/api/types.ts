export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "interrupted"
  | "awaiting_approval";

export type UsageCoverage = "complete" | "partial" | "unavailable";

export interface JobTiming {
  submitted_at_ms: number;
  started_at_ms: number | null;
  finished_at_ms: number | null;
  queue_wait_ms: number | null;
  execute_ms: number | null;
  total_ms: number | null;
}

export interface JobStatusCounts {
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  interrupted: number;
  awaiting_approval: number;
}

export interface JobListItem {
  job_id: string;
  status: JobStatus;
  attempt: number;
  max_attempts: number;
  created_at_ms: number;
  updated_at_ms: number;
  timing: JobTiming;
  error_code: string | null;
  error_message: string | null;
  goal_preview: string | null;
  result_available: boolean;
  review_enabled: boolean | null;
  memory_enabled: boolean | null;
}

export interface JobListResponse {
  api_version: string;
  items: JobListItem[];
  count: number;
  status_counts: JobStatusCounts;
}

export interface JobSubmitResponse {
  api_version: string;
  job_id: string;
  status: JobStatus;
}

export interface JobStatusResponse {
  api_version: string;
  job_id: string;
  status: JobStatus;
  attempt: number;
  max_attempts: number;
  timing: JobTiming;
  error_code: string | null;
  error_message: string | null;
  uncertainty: string | null;
  result_available: boolean;
}

export interface JobResultResponse {
  api_version: string;
  job_id: string;
  status: JobStatus;
  result: RunResult | null;
  error_code: string | null;
  error_message: string | null;
  uncertainty: string | null;
  timing: JobTiming;
}

export interface PlanTask {
  id: string;
  description: string;
  role: string;
  depends_on: string[];
  expected_output: string;
}

export interface ExecutionPlan {
  goal: string;
  tasks: PlanTask[];
  summary?: string | null;
}

export interface TaskResult {
  task_id: string;
  role: string;
  status: string;
  output: string | null;
  error: string | null;
  duration_ms: number | null;
  quality_approved: boolean;
  review_policy: string;
  review_bypass_reason: string | null;
  revision_count: number;
  attempts?: AttemptRecord[];
}

export interface AttemptRecord {
  index: number;
  kind: string;
  output: string | null;
  error: string | null;
  review?: {
    decision: string;
    feedback: string;
    issues: string[];
  } | null;
}

export interface ObservabilitySummary {
  usage: {
    coverage: UsageCoverage;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    notes: string[];
  };
  cost: {
    kind: "estimate";
    coverage: UsageCoverage;
    total_usd: number | null;
    as_of: string;
    provenance: string;
    notes: string[];
  };
  latency?: {
    queue_wait_ms?: number | null;
    planning_ms?: number | null;
    execution_ms?: number | null;
    approval_wait_ms?: number | null;
    total_ms?: number | null;
  } | null;
  trace_id?: string | null;
}

export interface RunResult {
  run_id: string;
  status: string;
  plan: ExecutionPlan;
  execution: {
    status: string;
    task_results: TaskResult[];
    duration_ms: number;
  };
  timing?: {
    total_ms: number;
    plan_ms: number | null;
    execute_ms: number;
  };
  observability?: ObservabilitySummary | null;
  approval_id?: string | null;
  artifact?: Record<string, unknown> | null;
  memory_enabled?: boolean;
}

export interface MetaResponse {
  api_version: string;
  auth_required: boolean;
  workspace_id: string;
  memory_enabled_default: boolean;
  review_default_enabled: boolean;
  notes: string[];
}

export interface MemoryRecord {
  memory_id: string;
  workspace_id: string;
  kind: string;
  trust: string;
  summary: string;
  created_at_ms: number;
  provenance?: Record<string, unknown>;
}

export interface MemoryListResponse {
  workspace_id: string;
  items: MemoryRecord[];
  count: number;
}

export interface ProtectedAction {
  action: "write_artifact";
  relative_path: string;
  content: string;
  reason: string;
}

export interface ApprovalRecord {
  approval_id: string;
  job_id: string;
  workspace_id: string;
  status: string;
  action: ProtectedAction;
  fingerprint: string;
  reason: string;
  risks: string[];
  expires_at_ms?: number;
}

export interface ApprovalListResponse {
  workspace_id: string;
  items: ApprovalRecord[];
  count: number;
}

export interface ReplayTimeline {
  mode: "timeline_playback";
  job_id: string;
  event_count: number;
  events: Array<{
    event_id: number;
    event_type: string;
    created_at_ms: number;
    delta_ms: number | null;
    payload: Record<string, unknown>;
  }>;
  latency: Record<string, unknown>;
  notes: string[];
}

export interface JobLifecycleEvent {
  event_id?: number;
  id?: string;
  event_type: string;
  created_at_ms?: number;
  payload?: Record<string, unknown>;
  stream_kind?: string;
}

export interface ApiErrorBody {
  api_version?: string;
  error: { code: string; message: string };
}

export interface RunRequestBody {
  api_version: "v1";
  goal?: string;
  plan?: ExecutionPlan;
  review_enabled: boolean;
  memory_enabled: boolean;
  max_revisions: number;
  max_concurrency?: number;
  protected_action?: ProtectedAction;
}
