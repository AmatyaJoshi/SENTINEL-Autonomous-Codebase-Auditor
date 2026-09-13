/**
 * In-memory mock server implementing the ApiClient contract.
 *
 * Active when VITE_MOCK=1 or when the real backend is unreachable at startup.
 * Seeds historical runs/findings/bench results and simulates a live run that
 * progresses through all pipeline nodes while emitting SSE-shaped events.
 */
import type { ApiClient, BenchParams, EventStreamHandlers, ReportFormat, RunListParams, Unsubscribe } from "./api";
import { ApiError } from "./api";
import type {
  Arm,
  BenchResult,
  CategoryCounts,
  CreateRunRequest,
  DecisionRequest,
  Finding,
  FindingFilters,
  FindingStatus,
  Health,
  Me,
  NodeName,
  Run,
  RunEvent,
  RunEventEnvelope,
  RunNode,
  Severity,
  SettingsSnapshot,
  Stats,
} from "./types";
import { ARMS, NODE_ORDER } from "./types";

/* ------------------------------------------------------------------ */
/* Deterministic PRNG                                                  */
/* ------------------------------------------------------------------ */

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rand = mulberry32(20260914);
const pick = <T,>(arr: readonly T[]): T => arr[Math.floor(rand() * arr.length)] as T;
const between = (a: number, b: number) => a + rand() * (b - a);
const round = (v: number, d = 2) => Math.round(v * 10 ** d) / 10 ** d;
let idCounter = 1000;
const nextId = (prefix: string) => `${prefix}${(idCounter++).toString(36)}`;

/* ------------------------------------------------------------------ */
/* Fixtures                                                            */
/* ------------------------------------------------------------------ */

const REPOS: Array<{ url: string; language: string }> = [
  { url: "https://github.com/acme/payments-api", language: "python" },
  { url: "https://github.com/acme/ledger-core", language: "python" },
  { url: "https://github.com/northwind/inventory-svc", language: "typescript" },
  { url: "https://github.com/northwind/auth-gateway", language: "go" },
  { url: "https://github.com/contoso/scheduler", language: "python" },
  { url: "https://github.com/contoso/webhooks", language: "typescript" },
  { url: "/srv/repos/internal-billing", language: "python" },
];

const CATEGORIES = [
  "off_by_one",
  "null_deref",
  "resource_leak",
  "race_condition",
  "unchecked_error",
  "injection",
  "type_confusion",
  "logic_error",
] as const;

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];

const FILES: Record<string, string[]> = {
  python: ["app/auth.py", "app/billing/ledger.py", "app/utils/pagination.py", "app/services/retry.py", "app/db/session.py"],
  typescript: ["src/auth/token.ts", "src/queue/worker.ts", "src/http/router.ts", "src/lib/cache.ts"],
  go: ["internal/auth/jwt.go", "internal/store/pool.go", "cmd/gateway/main.go", "pkg/ratelimit/bucket.go"],
};

const MODELS = ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"];

const TEST_CODE = (symbol: string, file: string) => `import pytest
from ${file.replace(/\.py$/, "").replace(/\//g, ".")} import ${symbol}


def test_${symbol}_boundary_is_inclusive():
    """Sentinel: ${symbol} drops the last element when n == len(items)."""
    items = [1, 2, 3]
    assert ${symbol}(items, 3) == [1, 2, 3]


def test_${symbol}_empty():
    assert ${symbol}([], 0) == []
`;

const PATCH = (file: string, symbol: string) => `--- a/${file}
+++ b/${file}
@@ -38,9 +38,9 @@ def paginate(items, page, size):


 def ${symbol}(items, n):
-    # return the first n items
-    if n <= 0:
+    # return the first n items (inclusive upper bound was off by one)
+    if n <= 0 or not items:
         return []
-    return items[: n - 1]
+    return items[:n]


 def last_n(items, n):
`;

const VERIFY_LOG = (test: string) => `$ docker run --rm -v /work:/work sentinel-sandbox:py3.12 pytest -q ${test}
============================= test session starts ==============================
collected 2 items

${test} F.                                                   [100%]

=================================== FAILURES ===================================
____________________ test_first_n_boundary_is_inclusive ________________________
    def test_first_n_boundary_is_inclusive():
        items = [1, 2, 3]
>       assert first_n(items, 3) == [1, 2, 3]
E       AssertionError: assert [1, 2] == [1, 2, 3]
E         Right contains one more item: 3

========================= 1 failed, 1 passed in 0.14s ==========================
exit_code=1  (bug reproduced: test fails on baseline)`;

const REGRESS_LOG = (test: string) => `$ docker run --rm -v /work:/work sentinel-sandbox:py3.12 pytest -q
============================= test session starts ==============================
collected 214 items

......................................................................... [ 34%]
......................................................................... [ 68%]
....................................................................       [100%]

============================= 214 passed in 6.82s ==============================
$ pytest -q ${test}
..                                                                       [100%]
============================== 2 passed in 0.11s ===============================
exit_code=0  (patch applied: sentinel test passes, no regressions)`;

const DESCRIPTIONS: Record<(typeof CATEGORIES)[number], string> = {
  off_by_one: "Slice upper bound excludes the final element when n equals the collection length.",
  null_deref: "Optional return value is dereferenced without a None/undefined guard on the error path.",
  resource_leak: "File handle opened in a loop is never closed when an exception is raised mid-iteration.",
  race_condition: "Check-then-act on a shared counter without holding the lock across both operations.",
  unchecked_error: "Return value of the write call is ignored, silently dropping partial writes.",
  injection: "User-controlled value is interpolated into a shell command without quoting.",
  type_confusion: "Numeric string compared against integer, so the range check always passes.",
  logic_error: "Retry backoff multiplies by zero on the first attempt, producing an immediate hot loop.",
};

/* ------------------------------------------------------------------ */
/* Server state                                                        */
/* ------------------------------------------------------------------ */

interface EventLog {
  seq: number;
  entries: RunEventEnvelope[];
  subs: Set<(env: RunEventEnvelope) => void>;
}

class MockServer implements ApiClient {
  private runs = new Map<string, Run>();
  private findings = new Map<string, Finding[]>();
  private logs = new Map<string, EventLog>();
  private bench: BenchResult[] = [];
  private timers = new Map<string, ReturnType<typeof setTimeout>>();
  private runArms = new Map<string, Arm>();

  constructor() {
    this.seedHistory();
    this.seedBench();
    // one live run so the UI has something moving on first load
    const live = this.newRun({
      repo: "https://github.com/acme/payments-api",
      sha: null,
      open_pr: true,
      review: false,
      max_usd: 5,
      max_minutes: 60,
      max_findings: 25,
      arm: "full",
    });
    this.simulate(live.id, { open_pr: true, review: false, speed: 1 });
  }

  /* ------------------------------ seeding ------------------------------ */

  private seedHistory() {
    const now = Date.now();
    const statuses: Run["status"][] = [
      "completed",
      "completed",
      "completed",
      "awaiting_review",
      "completed",
      "failed",
      "completed",
      "cancelled",
      "completed",
      "completed",
      "completed",
    ];
    statuses.forEach((status, i) => {
      const repo = REPOS[i % REPOS.length] as (typeof REPOS)[number];
      const startedAt = now - (i + 1) * 3.7 * 3600_000 - between(0, 1800_000);
      const wall = between(400, 1900);
      const finishedAt = startedAt + wall * 1000;
      const arm = i % 4 === 3 ? "single_shot" : i % 5 === 4 ? "no_triage" : "full";
      const id = nextId("r");
      const run: Run = {
        id,
        repo_url: repo.url,
        commit_sha: Array.from({ length: 40 }, () => "0123456789abcdef"[Math.floor(rand() * 16)]).join(""),
        language: repo.language,
        status,
        current_node: status === "awaiting_review" ? "report" : null,
        progress: status === "completed" ? 1 : status === "awaiting_review" ? 0.92 : status === "failed" ? 0.55 : 0.4,
        started_at: new Date(startedAt).toISOString(),
        finished_at: status === "awaiting_review" ? null : new Date(finishedAt).toISOString(),
        cost_usd: 0,
        budget: { max_usd: 5, max_minutes: 60, max_findings: 25 },
        counts: { candidate: 0, verified: 0, refuted: 0, fixed: 0, regressed: 0, pr_opened: 0 },
        nodes: this.buildHistoricNodes(status, startedAt, wall),
        arm,
      };
      this.runArms.set(id, arm);
      const findings = this.buildFindings(run, status === "failed" ? 3 : status === "cancelled" ? 2 : Math.floor(between(6, 14)), status);
      this.findings.set(id, findings);
      this.recount(run, findings);
      run.cost_usd = round(findings.reduce((s, f) => s + f.cost_usd, 0) + between(0.4, 1.2));
      this.runs.set(id, run);
      this.logs.set(id, { seq: 0, entries: [], subs: new Set() });
    });
  }

  private buildHistoricNodes(status: Run["status"], startedAt: number, wall: number): RunNode[] {
    const weights = [0.02, 0.05, 0.08, 0.04, 0.25, 0.08, 0.2, 0.15, 0.08, 0.02, 0.03];
    let t = startedAt;
    const cutoff = status === "failed" ? 6 : status === "cancelled" ? 4 : status === "awaiting_review" ? 10 : 11;
    return NODE_ORDER.map((name, i) => {
      if (i < cutoff) {
        const d = wall * (weights[i] ?? 0.05) * between(0.8, 1.2);
        const node: RunNode = {
          name,
          status: "done",
          started_at: new Date(t).toISOString(),
          finished_at: new Date(t + d * 1000).toISOString(),
          duration_s: round(d, 1),
        };
        t += d * 1000;
        return node;
      }
      if (i === cutoff && status === "failed") {
        return { name, status: "error", started_at: new Date(t).toISOString(), finished_at: new Date(t + 4000).toISOString(), duration_s: 4 };
      }
      if (i === cutoff && status === "awaiting_review") {
        return { name, status: "pending", started_at: null, finished_at: null, duration_s: null };
      }
      return { name, status: status === "completed" ? "skipped" : "pending", started_at: null, finished_at: null, duration_s: null };
    });
  }

  private buildFindings(run: Run, n: number, runStatus: Run["status"]): Finding[] {
    const files = FILES[run.language ?? "python"] ?? FILES.python ?? [];
    const out: Finding[] = [];
    for (let i = 0; i < n; i++) {
      const category = pick(CATEGORIES);
      const file = pick(files);
      const symbol = pick(["first_n", "parse_token", "acquire", "flush_batch", "next_page", "with_retry", "load_config"]);
      const confidence = round(between(0.35, 0.97));
      let status: FindingStatus;
      if (runStatus === "failed" || runStatus === "cancelled") status = "candidate";
      else if (confidence < 0.5) status = "refuted";
      else if (confidence < 0.62) status = "candidate";
      else if (confidence < 0.72) status = "verified";
      else if (confidence < 0.8) status = rand() > 0.6 ? "regressed" : "fixed";
      else status = rand() > 0.5 ? "pr_opened" : "fixed";
      if (runStatus === "awaiting_review" && (status === "fixed" || status === "pr_opened")) status = "fixed";
      const verifiedLike = status !== "candidate" && status !== "refuted";
      const line = Math.floor(between(12, 240));
      const f: Finding = {
        id: nextId("f"),
        run_id: run.id,
        category,
        severity: pick(SEVERITIES),
        file,
        line_start: line,
        line_end: line + Math.floor(between(1, 6)),
        symbol,
        description: DESCRIPTIONS[category],
        hypothesis: `If ${symbol}() is called with a boundary input (n == len), the ${category.replace(/_/g, " ")} manifests and a failing test can demonstrate it deterministically.`,
        evidence: [
          `ruff B905 ${file}:${line}: zip() without explicit strict=`,
          `semgrep python.lang.correctness ${file}:${line}`,
          `call graph: ${symbol} reachable from 3 public handlers (blast radius)`,
        ].slice(0, Math.floor(between(1, 4))),
        confidence,
        status,
        test_path: verifiedLike || status === "refuted" ? `tests/test_sentinel_${symbol}.py` : null,
        test_code: verifiedLike || status === "refuted" ? TEST_CODE(symbol, file) : null,
        patch_diff: status === "fixed" || status === "pr_opened" || status === "regressed" ? PATCH(file, symbol) : null,
        verify_log: verifiedLike || status === "refuted" ? VERIFY_LOG(`tests/test_sentinel_${symbol}.py`) : null,
        regress_log: status === "fixed" || status === "pr_opened" ? REGRESS_LOG(`tests/test_sentinel_${symbol}.py`) : status === "regressed" ? REGRESS_LOG("").replace("214 passed", "212 passed, 2 failed").replace("exit_code=0", "exit_code=1") : null,
        pr_url: status === "pr_opened" ? `${run.repo_url}/pull/${Math.floor(between(100, 999))}` : null,
        explanation: verifiedLike
          ? `${symbol}() in ${file} mis-handles the upper bound. The generated test fails on the baseline commit and passes after the one-line patch; the full suite still passes, so the fix is safe to merge.`
          : null,
        blast_radius: Math.floor(between(1, 9)),
        cost_usd: round(between(0.05, 0.6), 3),
        created_at: new Date(new Date(run.started_at ?? Date.now()).getTime() + between(60_000, 600_000)).toISOString(),
      };
      out.push(f);
    }
    return out;
  }

  private seedBench() {
    const base: Record<Arm, { p: number; r: number }> = {
      analyzers: { p: 0.31, r: 0.22 },
      single_shot: { p: 0.48, r: 0.41 },
      no_triage: { p: 0.66, r: 0.58 },
      full: { p: 0.82, r: 0.63 },
    };
    const now = Date.now();
    for (let day = 9; day >= 0; day--) {
      for (const arm of ARMS) {
        const b = base[arm];
        const drift = (9 - day) * 0.006;
        const precision = round(Math.min(0.96, b.p + drift + between(-0.03, 0.03)), 3);
        const recall = round(Math.min(0.9, b.r + drift * 0.6 + between(-0.03, 0.03)), 3);
        const f1 = round((2 * precision * recall) / (precision + recall || 1), 3);
        const per_category: Record<string, CategoryCounts> = {};
        for (const c of CATEGORIES) {
          const total = Math.floor(between(3, 9));
          const tp = Math.round(total * recall * between(0.85, 1.1));
          per_category[c] = { tp: Math.min(total, tp), fp: Math.max(0, Math.round(tp * (1 - precision) * 1.4)), fn: Math.max(0, total - tp) };
        }
        this.bench.push({
          id: nextId("b"),
          created_at: new Date(now - day * 86_400_000 - between(0, 3_600_000)).toISOString(),
          commit: Array.from({ length: 7 }, () => "0123456789abcdef"[Math.floor(rand() * 16)]).join(""),
          suite: day % 3 === 0 ? "full" : "small",
          arm,
          precision,
          precision_strict: round(precision - between(0.04, 0.1), 3),
          recall,
          f1,
          verified_rate: round(between(0.4, 0.8), 3),
          patch_pass_rate: round(between(0.55, 0.9), 3),
          cost_usd: round(arm === "analyzers" ? between(0.1, 0.4) : arm === "single_shot" ? between(1.5, 3) : between(3, 6.5)),
          wall_clock_s: Math.floor(arm === "analyzers" ? between(60, 180) : between(600, 1800)),
          per_category,
        });
      }
    }
    this.bench.sort((a, b) => b.created_at.localeCompare(a.created_at));
  }

  /* ------------------------------ helpers ------------------------------ */

  private recount(run: Run, findings: Finding[]) {
    const c = { candidate: 0, verified: 0, refuted: 0, fixed: 0, regressed: 0, pr_opened: 0 };
    for (const f of findings) {
      if (f.status in c) c[f.status as keyof typeof c] += 1;
      else if (f.status === "approved") c.fixed += 1;
      else if (f.status === "rejected") c.refuted += 1;
    }
    run.counts = c;
  }

  private emit(runId: string, event: RunEvent) {
    const log = this.logs.get(runId);
    if (!log) return;
    log.seq += 1;
    const env: RunEventEnvelope = { id: String(log.seq), seq: log.seq, receivedAt: Date.now(), event };
    log.entries.push(env);
    if (log.entries.length > 2000) log.entries.splice(0, log.entries.length - 2000);
    log.subs.forEach((s) => s(env));
  }

  private newRun(body: CreateRunRequest): Run {
    const repoInfo = REPOS.find((r) => r.url === body.repo);
    const language = repoInfo?.language ?? (body.repo.endsWith(".ts") ? "typescript" : pick(["python", "typescript", "go"]));
    const id = nextId("r");
    const run: Run = {
      id,
      repo_url: body.repo,
      commit_sha: body.sha ?? Array.from({ length: 40 }, () => "0123456789abcdef"[Math.floor(rand() * 16)]).join(""),
      language,
      status: "created",
      current_node: null,
      progress: 0,
      started_at: new Date().toISOString(),
      finished_at: null,
      cost_usd: 0,
      budget: { max_usd: body.max_usd, max_minutes: body.max_minutes, max_findings: body.max_findings },
      counts: { candidate: 0, verified: 0, refuted: 0, fixed: 0, regressed: 0, pr_opened: 0 },
      nodes: NODE_ORDER.map((name) => ({ name, status: "pending", started_at: null, finished_at: null, duration_s: null })),
      arm: body.arm,
    };
    this.runArms.set(id, body.arm);
    this.runs.set(id, run);
    this.findings.set(id, []);
    this.logs.set(id, { seq: 0, entries: [], subs: new Set() });
    return run;
  }

  /** Drives a run through the pipeline with timers, emitting realistic events. */
  private simulate(runId: string, opts: { open_pr: boolean; review: boolean; speed: number }) {
    const run = this.runs.get(runId);
    if (!run) return;
    const nodeDurations: Record<NodeName, number> = {
      ingest: 3,
      index: 5,
      analyze: 7,
      plan: 4,
      hunt: 16,
      triage: 6,
      verify: 14,
      fix: 12,
      regress: 8,
      rank: 3,
      report: 3,
    };
    const skip = new Set<NodeName>();
    const arm = this.runArms.get(runId) ?? "full";
    if (arm === "no_triage") skip.add("triage");
    if (arm === "single_shot") {
      skip.add("plan");
      skip.add("triage");
      skip.add("rank");
    }
    if (arm === "analyzers") ["plan", "hunt", "triage", "verify", "fix", "regress", "rank"].forEach((n) => skip.add(n as NodeName));

    let idx = 0;
    const findings = this.findings.get(runId) ?? [];
    const schedule = (fn: () => void, ms: number) => {
      const t = setTimeout(fn, ms / opts.speed);
      this.timers.set(runId, t);
    };

    const setStatus = (status: Run["status"], node: NodeName | null) => {
      run.status = status;
      run.current_node = node;
      const doneCount = run.nodes.filter((n) => n.status === "done" || n.status === "skipped").length;
      run.progress = round(Math.min(1, doneCount / NODE_ORDER.length + (node ? 0.5 / NODE_ORDER.length : 0)), 3);
      this.emit(runId, { type: "run.status", data: { status: run.status, current_node: run.current_node, progress: run.progress } });
    };

    const finish = (status: Run["status"]) => {
      run.status = status;
      run.current_node = null;
      run.finished_at = new Date().toISOString();
      run.progress = status === "completed" ? 1 : run.progress;
      this.emit(runId, { type: "log", data: { level: status === "completed" ? "info" : "warning", message: `Run ${status}. Total cost ${run.cost_usd.toFixed(2)} USD.` } });
      this.emit(runId, { type: "run.end", data: { ...run } });
    };

    const llm = (node: NodeName, cost: number) => {
      const tokens_in = Math.floor(between(800, 6000));
      const tokens_out = Math.floor(between(150, 1200));
      run.cost_usd = round(run.cost_usd + cost, 3);
      this.emit(runId, {
        type: "llm.call",
        data: { node, model: pick(MODELS), tokens_in, tokens_out, cost_usd: round(cost, 3), duration_s: round(between(1.2, 6.5), 1) },
      });
    };

    const sandbox = (node: NodeName, command: string, exit_code: number) => {
      this.emit(runId, { type: "sandbox.exec", data: { node, command, exit_code, duration_s: round(between(0.8, 9), 1), timed_out: false } });
    };

    const log = (message: string, level: "info" | "warning" | "error" | "debug" = "info") => {
      this.emit(runId, { type: "log", data: { level, message } });
    };

    const step = () => {
      if (run.status === "cancelled") return;
      if (idx >= NODE_ORDER.length) {
        finish("completed");
        return;
      }
      const name = NODE_ORDER[idx] as NodeName;
      const node = run.nodes[idx] as RunNode;
      if (skip.has(name)) {
        node.status = "skipped";
        idx += 1;
        log(`Skipping ${name} (arm=${arm})`, "debug");
        step();
        return;
      }
      node.status = "running";
      node.started_at = new Date().toISOString();
      setStatus("running", name);
      this.emit(runId, { type: "node.start", data: { node: name, at: node.started_at } });
      log(`${name}: started`);

      const dur = nodeDurations[name] * 1000 * between(0.8, 1.25);
      const ticks = Math.max(1, Math.floor(dur / 2200));
      let tick = 0;
      const doTick = () => {
        if (run.status === "cancelled") return;
        tick += 1;
        this.nodeActivity(name, tick, ticks, { run, findings, llm, sandbox, log, open_pr: opts.open_pr });
        if (tick < ticks) {
          schedule(doTick, dur / ticks);
          return;
        }
        node.status = "done";
        node.finished_at = new Date().toISOString();
        node.duration_s = round((new Date(node.finished_at).getTime() - new Date(node.started_at ?? node.finished_at).getTime()) / 1000, 1);
        this.recount(run, findings);
        this.emit(runId, { type: "node.end", data: { node: name, at: node.finished_at, duration_s: node.duration_s, ok: true } });
        log(`${name}: done in ${node.duration_s}s`);
        idx += 1;
        if (name === "regress" && opts.review) {
          setStatus("awaiting_review", null);
          log("Awaiting operator review before opening PRs.", "warning");
          return; // resumes via decisions
        }
        schedule(step, 600);
      };
      schedule(doTick, dur / ticks);
    };

    schedule(step, 800);
  }

  private nodeActivity(
    name: NodeName,
    tick: number,
    ticks: number,
    ctx: {
      run: Run;
      findings: Finding[];
      llm: (node: NodeName, cost: number) => void;
      sandbox: (node: NodeName, cmd: string, exit: number) => void;
      log: (m: string, level?: "info" | "warning" | "error" | "debug") => void;
      open_pr: boolean;
    },
  ) {
    const { run, findings, llm, sandbox, log } = ctx;
    const files = FILES[run.language ?? "python"] ?? FILES.python ?? [];
    switch (name) {
      case "ingest":
        sandbox(name, `git clone --depth 1 ${run.repo_url} /work`, 0);
        log(`Cloned ${run.repo_url} @ ${run.commit_sha?.slice(0, 7)}`);
        break;
      case "index":
        log(`Indexed ${Math.floor(between(120, 640))} files, ${Math.floor(between(900, 4200))} symbols`);
        break;
      case "analyze":
        sandbox(name, run.language === "python" ? "ruff check --output-format json ." : "eslint -f json .", 1);
        if (tick === ticks) sandbox(name, "semgrep --config auto --json .", 0);
        break;
      case "plan":
        llm(name, between(0.03, 0.08));
        log(`Planned ${Math.floor(between(4, 9))} hunt regions ranked by churn x complexity`);
        break;
      case "hunt": {
        llm(name, between(0.04, 0.12));
        if (findings.length < run.budget.max_findings && rand() > 0.25) {
          const f = this.buildFindings(run, 1, "running")[0];
          if (f) {
            f.status = "candidate";
            f.test_code = null;
            f.test_path = null;
            f.patch_diff = null;
            f.verify_log = null;
            f.regress_log = null;
            f.pr_url = null;
            f.explanation = null;
            f.file = pick(files);
            f.created_at = new Date().toISOString();
            findings.push(f);
            this.recount(run, findings);
            this.emit(run.id, { type: "finding.new", data: { ...f } });
            log(`Candidate: ${f.category} in ${f.file}:${f.line_start} (conf ${f.confidence})`);
          }
        }
        break;
      }
      case "triage": {
        llm(name, between(0.01, 0.03));
        const cands = findings.filter((f) => f.status === "candidate");
        const target = cands[Math.floor(rand() * cands.length)];
        if (target && target.confidence < 0.5) {
          target.status = "refuted";
          this.emit(run.id, { type: "finding.update", data: { ...target } });
          log(`Triage refuted ${target.id} (${target.category}), confidence too low`, "warning");
        }
        break;
      }
      case "verify": {
        const cands = findings.filter((f) => f.status === "candidate");
        const target = cands[0];
        if (target) {
          llm(name, between(0.05, 0.15));
          target.test_path = `tests/test_sentinel_${target.symbol ?? "case"}.py`;
          target.test_code = TEST_CODE(target.symbol ?? "case", target.file);
          const reproduced = rand() > 0.3;
          sandbox(name, `pytest -q ${target.test_path}`, reproduced ? 1 : 0);
          target.verify_log = reproduced ? VERIFY_LOG(target.test_path) : `pytest -q ${target.test_path}\n2 passed\nexit_code=0  (test passes on baseline: hypothesis refuted)`;
          target.status = reproduced ? "verified" : "refuted";
          target.cost_usd = round(target.cost_usd + between(0.05, 0.2), 3);
          this.emit(run.id, { type: "finding.update", data: { ...target } });
          log(reproduced ? `Verified ${target.id}: failing test reproduces the bug` : `Refuted ${target.id}: test passes on baseline`, reproduced ? "info" : "warning");
        }
        break;
      }
      case "fix": {
        const target = findings.find((f) => f.status === "verified");
        if (target) {
          llm(name, between(0.08, 0.2));
          target.patch_diff = PATCH(target.file, target.symbol ?? "fn");
          sandbox(name, `git apply --check sentinel-${target.id}.patch`, 0);
          target.status = "fixed";
          this.emit(run.id, { type: "finding.update", data: { ...target } });
          log(`Patched ${target.file} for ${target.id}`);
        }
        break;
      }
      case "regress": {
        const target = findings.find((f) => f.status === "fixed" && !f.regress_log);
        if (target) {
          const ok = rand() > 0.2;
          sandbox(name, "pytest -q", ok ? 0 : 1);
          target.regress_log = ok ? REGRESS_LOG(target.test_path ?? "") : REGRESS_LOG(target.test_path ?? "").replace("214 passed", "212 passed, 2 failed").replace("exit_code=0", "exit_code=1");
          if (!ok) target.status = "regressed";
          target.explanation = ok ? `${target.symbol}() mis-handles the boundary. The generated test fails on baseline and passes after the patch; full suite green.` : target.explanation;
          this.emit(run.id, { type: "finding.update", data: { ...target } });
          log(ok ? `Regression suite green for ${target.id}` : `Regression failures after patch for ${target.id}`, ok ? "info" : "error");
        }
        break;
      }
      case "rank":
        llm(name, between(0.01, 0.03));
        log("Ranked findings by severity x confidence x blast radius");
        break;
      case "report": {
        if (ctx.open_pr) {
          for (const f of findings.filter((x) => x.status === "fixed" || x.status === "approved").slice(0, 3)) {
            f.status = "pr_opened";
            f.pr_url = `${run.repo_url}/pull/${Math.floor(between(100, 999))}`;
            this.emit(run.id, { type: "finding.update", data: { ...f } });
            log(`Opened PR ${f.pr_url}`);
          }
          this.recount(run, findings);
        }
        log("Report rendered: report.html, report.json, report.md");
        break;
      }
    }
  }

  private delay<T>(v: T, ms = 120): Promise<T> {
    return new Promise((r) => setTimeout(() => r(structuredClone(v)), ms + Math.random() * 80));
  }

  /* --------------------------- ApiClient impl -------------------------- */

  health(): Promise<Health> {
    return this.delay({ status: "ok", version: "0.1.0-mock", checks: { db: "ok", docker: "ok", llm: "configured" } });
  }

  stats(): Promise<Stats> {
    const runs = [...this.runs.values()];
    const all = [...this.findings.values()].flat();
    const by_category: Record<string, number> = {};
    const by_status: Record<string, number> = {};
    for (const f of all) {
      by_category[f.category] = (by_category[f.category] ?? 0) + 1;
      by_status[f.status] = (by_status[f.status] ?? 0) + 1;
    }
    const latestFull = this.bench.find((b) => b.arm === "full");
    return this.delay({
      runs_total: runs.length,
      runs_active: runs.filter((r) => r.status === "running" || r.status === "created").length,
      findings_total: all.length,
      verified_total: all.filter((f) => ["verified", "fixed", "pr_opened", "regressed", "approved"].includes(f.status)).length,
      fixed_total: all.filter((f) => ["fixed", "pr_opened", "approved"].includes(f.status)).length,
      pr_opened_total: all.filter((f) => f.status === "pr_opened").length,
      cost_usd_total: round(runs.reduce((s, r) => s + r.cost_usd, 0)),
      precision_latest: latestFull?.precision ?? null,
      by_category,
      by_status,
    });
  }

  listRuns(params: RunListParams = {}) {
    let items = [...this.runs.values()].sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""));
    if (params.status) {
      const set = new Set(params.status.split(","));
      items = items.filter((r) => set.has(r.status));
    }
    const total = items.length;
    const offset = params.offset ?? 0;
    const limit = params.limit ?? 50;
    return this.delay({ items: items.slice(offset, offset + limit), total });
  }

  getRun(id: string) {
    const run = this.runs.get(id);
    if (!run) return Promise.reject(new ApiError(404, "not_found", `Run ${id} not found`));
    return this.delay(run, 60);
  }

  createRun(body: CreateRunRequest) {
    if (!body.repo) return Promise.reject(new ApiError(422, "validation", "repo is required"));
    const run = this.newRun(body);
    this.simulate(run.id, { open_pr: body.open_pr, review: body.review, speed: 1 });
    return this.delay(run, 250);
  }

  cancelRun(id: string) {
    const run = this.runs.get(id);
    if (!run) return Promise.reject(new ApiError(404, "not_found", `Run ${id} not found`));
    if (run.status !== "running" && run.status !== "created" && run.status !== "awaiting_review") {
      return Promise.reject(new ApiError(409, "conflict", `Run is already ${run.status}`));
    }
    const t = this.timers.get(id);
    if (t) clearTimeout(t);
    for (const n of run.nodes) if (n.status === "running") n.status = "error";
    run.status = "cancelled";
    run.current_node = null;
    run.finished_at = new Date().toISOString();
    this.emit(id, { type: "log", data: { level: "warning", message: "Run cancelled by operator." } });
    this.emit(id, { type: "run.end", data: { ...run } });
    return this.delay(run, 150);
  }

  deleteRun(id: string) {
    if (!this.runs.has(id)) return Promise.reject(new ApiError(404, "not_found", `Run ${id} not found`));
    this.runs.delete(id);
    this.findings.delete(id);
    this.logs.delete(id);
    return this.delay(undefined, 100);
  }

  listFindings(runId: string, filters: FindingFilters = {}) {
    if (!this.runs.has(runId)) return Promise.reject(new ApiError(404, "not_found", `Run ${runId} not found`));
    let items = this.findings.get(runId) ?? [];
    if (filters.status) items = items.filter((f) => f.status === filters.status);
    if (filters.category) items = items.filter((f) => f.category === filters.category);
    if (filters.min_confidence != null) items = items.filter((f) => f.confidence >= (filters.min_confidence ?? 0));
    return this.delay({ items }, 90);
  }

  getFinding(runId: string, fid: string) {
    const f = (this.findings.get(runId) ?? []).find((x) => x.id === fid);
    if (!f) return Promise.reject(new ApiError(404, "not_found", `Finding ${fid} not found`));
    return this.delay(f, 60);
  }

  decide(runId: string, fid: string, body: DecisionRequest) {
    const run = this.runs.get(runId);
    const list = this.findings.get(runId) ?? [];
    const f = list.find((x) => x.id === fid);
    if (!run || !f) return Promise.reject(new ApiError(404, "not_found", `Finding ${fid} not found`));
    if (run.status !== "awaiting_review") return Promise.reject(new ApiError(409, "conflict", "Run is not awaiting review"));
    f.status = body.decision === "approve" ? "approved" : "rejected";
    this.emit(runId, { type: "finding.update", data: { ...f } });
    this.emit(runId, { type: "log", data: { level: "info", message: `${body.decision}d ${fid}${body.note ? `: ${body.note}` : ""}` } });
    const pending = list.filter((x) => x.status === "fixed" || x.status === "verified");
    if (pending.length === 0) {
      // resume to report
      const reportIdx = NODE_ORDER.indexOf("report");
      const rankNode = run.nodes[reportIdx - 1];
      if (rankNode && rankNode.status === "pending") {
        rankNode.status = "done";
        rankNode.duration_s = 1.2;
      }
      run.status = "running";
      run.current_node = "report";
      this.emit(runId, { type: "run.status", data: { status: "running", current_node: "report", progress: 0.95 } });
      const node = run.nodes[reportIdx];
      if (node) {
        node.status = "running";
        node.started_at = new Date().toISOString();
        this.emit(runId, { type: "node.start", data: { node: "report", at: node.started_at } });
      }
      const t = setTimeout(() => {
        for (const x of list.filter((y) => y.status === "approved")) {
          x.status = "pr_opened";
          x.pr_url = `${run.repo_url}/pull/${Math.floor(between(100, 999))}`;
          this.emit(runId, { type: "finding.update", data: { ...x } });
        }
        if (node) {
          node.status = "done";
          node.finished_at = new Date().toISOString();
          node.duration_s = 2.4;
          this.emit(runId, { type: "node.end", data: { node: "report", at: node.finished_at, duration_s: 2.4, ok: true } });
        }
        this.recount(run, list);
        run.status = "completed";
        run.progress = 1;
        run.current_node = null;
        run.finished_at = new Date().toISOString();
        this.emit(runId, { type: "run.end", data: { ...run } });
      }, 2500);
      this.timers.set(runId, t);
    }
    this.recount(run, list);
    return this.delay(f, 180);
  }

  benchResults(params: BenchParams = {}) {
    let items = this.bench;
    if (params.suite) items = items.filter((b) => b.suite === params.suite);
    return this.delay({ items: items.slice(0, params.limit ?? 20) });
  }

  settings(): Promise<SettingsSnapshot> {
    return this.delay({
      SENTINEL_ENV: "development",
      SENTINEL_DB_URL: "sqlite:///./sentinel.db",
      SENTINEL_API_KEYS: "[REDACTED x3]",
      SENTINEL_LLM_PROVIDER: "anthropic",
      SENTINEL_LLM_MODEL: "claude-sonnet-4-5",
      ANTHROPIC_API_KEY: "sk-ant-***REDACTED***",
      SENTINEL_DOCKER_IMAGE: "sentinel-sandbox:py3.12",
      SENTINEL_MAX_PARALLEL_RUNS: 2,
      SENTINEL_DEFAULT_BUDGET_USD: 5,
      SENTINEL_RATE_LIMIT_PER_MIN: 120,
      GITHUB_TOKEN: "ghp_***REDACTED***",
    });
  }

  me(): Promise<Me> {
    return this.delay({ name: "demo-admin", role: "admin" });
  }

  reportUrl(runId: string, fmt: ReportFormat) {
    const run = this.runs.get(runId);
    const body = fmt === "json" ? JSON.stringify({ run, findings: this.findings.get(runId) ?? [] }, null, 2) : fmt === "md" ? `# Sentinel report for ${run?.repo_url}\n\n(Demo data)\n` : `<!doctype html><title>Sentinel report</title><h1>Sentinel report (demo)</h1><p>${run?.repo_url ?? ""}</p>`;
    const mime = fmt === "json" ? "application/json" : fmt === "md" ? "text/markdown" : "text/html";
    return `data:${mime};charset=utf-8,${encodeURIComponent(body)}`;
  }

  streamEvents(runId: string, lastEventId: string | null, handlers: EventStreamHandlers): Unsubscribe {
    const log = this.logs.get(runId);
    if (!log) {
      setTimeout(() => handlers.onError?.(new ApiError(404, "not_found", "Run not found"), true), 0);
      return () => undefined;
    }
    let active = true;
    const sub = (env: RunEventEnvelope) => {
      if (active) handlers.onEvent(env);
    };
    setTimeout(() => {
      if (!active) return;
      handlers.onOpen?.();
      const from = lastEventId ? Number(lastEventId) : 0;
      for (const e of log.entries) if (e.seq > from) handlers.onEvent(e);
      log.subs.add(sub);
    }, 80);
    return () => {
      active = false;
      log.subs.delete(sub);
    };
  }
}

let singleton: MockServer | null = null;
export function createMockClient(): ApiClient {
  if (!singleton) singleton = new MockServer();
  return singleton;
}
