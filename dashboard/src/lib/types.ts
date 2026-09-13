/**
 * Typed models mirroring docs/API.md (Sentinel HTTP API v1).
 */

export type Role = "viewer" | "operator" | "admin";

export type RunStatus = "created" | "running" | "completed" | "failed" | "cancelled" | "awaiting_review";

export type NodeStatus = "done" | "running" | "pending" | "skipped" | "error";

export type NodeName =
  | "ingest"
  | "index"
  | "analyze"
  | "plan"
  | "hunt"
  | "triage"
  | "verify"
  | "fix"
  | "regress"
  | "rank"
  | "report";

export const NODE_ORDER: readonly NodeName[] = [
  "ingest",
  "index",
  "analyze",
  "plan",
  "hunt",
  "triage",
  "verify",
  "fix",
  "regress",
  "rank",
  "report",
] as const;

export type Arm = "full" | "no_triage" | "single_shot" | "analyzers";
export const ARMS: readonly Arm[] = ["analyzers", "single_shot", "no_triage", "full"] as const;

export type FindingStatus =
  | "candidate"
  | "verified"
  | "refuted"
  | "fixed"
  | "regressed"
  | "pr_opened"
  | "approved"
  | "rejected";

export type Severity = "critical" | "high" | "medium" | "low" | "info";

export interface RunBudget {
  max_usd: number;
  max_minutes: number;
  max_findings: number;
}

export interface RunCounts {
  candidate: number;
  verified: number;
  refuted: number;
  fixed: number;
  regressed: number;
  pr_opened: number;
}

export interface RunNode {
  name: NodeName;
  status: NodeStatus;
  started_at?: string | null;
  finished_at?: string | null;
  duration_s?: number | null;
}

export interface Run {
  id: string;
  repo_url: string;
  commit_sha: string | null;
  language: string | null;
  status: RunStatus;
  current_node: NodeName | null;
  progress: number;
  started_at: string | null;
  finished_at: string | null;
  cost_usd: number;
  budget: RunBudget;
  counts: RunCounts;
  nodes: RunNode[];
  /** Not in the contract's Run example but returned by some servers; optional. */
  arm?: Arm;
}

export interface RunListResponse {
  items: Run[];
  total: number;
}

export interface CreateRunRequest {
  repo: string;
  sha: string | null;
  open_pr: boolean;
  review: boolean;
  max_usd: number;
  max_minutes: number;
  max_findings: number;
  arm: Arm;
}

export interface Finding {
  id: string;
  run_id: string;
  category: string;
  severity: Severity;
  file: string;
  line_start: number;
  line_end: number;
  symbol: string | null;
  description: string;
  hypothesis: string;
  evidence: string[];
  confidence: number;
  status: FindingStatus;
  test_path: string | null;
  test_code: string | null;
  patch_diff: string | null;
  verify_log: string | null;
  regress_log: string | null;
  pr_url: string | null;
  explanation: string | null;
  blast_radius: number;
  cost_usd: number;
  created_at: string;
}

export interface FindingListResponse {
  items: Finding[];
}

export interface FindingFilters {
  status?: string;
  category?: string;
  min_confidence?: number;
}

export interface DecisionRequest {
  decision: "approve" | "reject";
  note: string;
}

export interface Stats {
  runs_total: number;
  runs_active: number;
  findings_total: number;
  verified_total: number;
  fixed_total: number;
  pr_opened_total: number;
  cost_usd_total: number;
  precision_latest: number | null;
  by_category: Record<string, number>;
  by_status: Record<string, number>;
}

export interface Health {
  status: string;
  version: string;
  /** Per-contract always present, but tolerated as missing so a partial payload never crashes the shell. */
  checks?: {
    db?: string;
    docker?: string;
    llm?: string;
  };
}

export interface Me {
  name: string;
  role: Role;
}

export interface CategoryCounts {
  tp: number;
  fp: number;
  fn: number;
}

export interface BenchArmMetrics {
  precision?: number;
  precision_strict?: number;
  recall?: number;
  f1?: number;
  verified_rate?: number;
  patch_pass_rate?: number;
  cost_usd?: number;
  wall_clock_s?: number;
  per_category?: Record<string, CategoryCounts>;
}

export interface BenchResult {
  id: string;
  created_at: string;
  commit: string;
  suite: "small" | "full";
  arm: Arm;
  precision: number;
  precision_strict: number;
  recall: number;
  f1: number;
  verified_rate: number;
  patch_pass_rate: number;
  cost_usd: number;
  wall_clock_s: number;
  per_category: Record<string, CategoryCounts>;
  arms?: Partial<Record<Arm, BenchArmMetrics>>;
}

export interface BenchListResponse {
  items: BenchResult[];
}

export type SettingsSnapshot = Record<string, unknown>;

/** SSE event payloads. */
export interface RunStatusEvent {
  status: RunStatus;
  current_node: NodeName | null;
  progress: number;
}
export interface NodeStartEvent {
  node: NodeName;
  at: string;
}
export interface NodeEndEvent {
  node: NodeName;
  at: string;
  duration_s: number;
  ok: boolean;
}
export interface LlmCallEvent {
  node: NodeName;
  model: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  duration_s: number;
}
export interface SandboxExecEvent {
  node: NodeName;
  command: string;
  exit_code: number;
  duration_s: number;
  timed_out: boolean;
}
export interface LogEvent {
  level: "debug" | "info" | "warning" | "error";
  message: string;
}

export type RunEvent =
  | { type: "run.status"; data: RunStatusEvent }
  | { type: "node.start"; data: NodeStartEvent }
  | { type: "node.end"; data: NodeEndEvent }
  | { type: "finding.new"; data: Finding }
  | { type: "finding.update"; data: Finding }
  | { type: "llm.call"; data: LlmCallEvent }
  | { type: "sandbox.exec"; data: SandboxExecEvent }
  | { type: "log"; data: LogEvent }
  | { type: "run.end"; data: Run };

export type RunEventType = RunEvent["type"];

export const RUN_EVENT_TYPES: readonly RunEventType[] = [
  "run.status",
  "node.start",
  "node.end",
  "finding.new",
  "finding.update",
  "llm.call",
  "sandbox.exec",
  "log",
  "run.end",
] as const;

/** A received event with envelope metadata. */
export interface RunEventEnvelope {
  id: string;
  seq: number;
  receivedAt: number;
  event: RunEvent;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}
