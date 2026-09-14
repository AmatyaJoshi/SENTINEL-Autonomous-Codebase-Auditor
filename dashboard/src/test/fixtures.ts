/** Shared typed fixtures for unit tests. */
import type { Finding, Run, RunNode } from "@/lib/types";
import { NODE_ORDER } from "@/lib/types";

export function makeNodes(overrides: Partial<Record<RunNode["name"], Partial<RunNode>>> = {}): RunNode[] {
  return NODE_ORDER.map((name) => ({
    name,
    status: "pending",
    started_at: null,
    finished_at: null,
    duration_s: null,
    ...overrides[name],
  }));
}

export function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: "r-test",
    repo_url: "https://github.com/acme/payments-api",
    commit_sha: "0123456789abcdef0123456789abcdef01234567",
    language: "python",
    status: "running",
    current_node: "hunt",
    progress: 0.4,
    started_at: "2026-09-14T09:00:00.000Z",
    finished_at: null,
    cost_usd: 1.25,
    budget: { max_usd: 5, max_minutes: 60, max_findings: 25 },
    counts: { candidate: 1, verified: 0, refuted: 0, fixed: 0, regressed: 0, pr_opened: 0 },
    nodes: makeNodes({
      ingest: { status: "done", duration_s: 3.2 },
      index: { status: "done", duration_s: 5.1 },
      analyze: { status: "done", duration_s: 7 },
      plan: { status: "done", duration_s: 4 },
      hunt: { status: "running", started_at: "2026-09-14T09:01:00.000Z" },
    }),
    arm: "full",
    ...overrides,
  };
}

export function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    id: "f-1",
    run_id: "r-test",
    category: "off_by_one",
    severity: "high",
    file: "app/utils/pagination.py",
    line_start: 42,
    line_end: 45,
    symbol: "first_n",
    description: "Slice upper bound excludes the final element when n equals the collection length.",
    hypothesis: "If first_n() is called with n == len, the last element is dropped.",
    evidence: ["ruff B905 app/utils/pagination.py:42"],
    confidence: 0.87,
    status: "fixed",
    test_path: "tests/test_sentinel_first_n.py",
    test_code: "def test_first_n_boundary_is_inclusive():\n    assert first_n([1, 2, 3], 3) == [1, 2, 3]\n",
    patch_diff: "--- a/app/utils/pagination.py\n+++ b/app/utils/pagination.py\n@@ -40,3 +40,3 @@ def first_n(items, n):\n     if n <= 0:\n-    return items[: n - 1]\n+    return items[:n]\n",
    verify_log: "$ pytest -q tests/test_sentinel_first_n.py\n1 failed, 1 passed\nexit_code=1",
    regress_log: null,
    pr_url: null,
    explanation: "first_n() mis-handles the upper bound.",
    blast_radius: 3,
    cost_usd: 0.21,
    created_at: "2026-09-14T09:02:00.000Z",
    ...overrides,
  };
}
