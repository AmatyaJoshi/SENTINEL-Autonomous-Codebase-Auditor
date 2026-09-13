import { motion } from "framer-motion";
import clsx from "clsx";
import { ChevronLeft, ChevronRight, Plus, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api } from "@/lib/api";
import type { Run, RunStatus } from "@/lib/types";
import { relativeTime, repoName, shortSha, usd } from "@/lib/format";
import { useAsync } from "@/hooks/useAsync";
import { useHotkey } from "@/hooks/useHotkey";
import { useSession } from "@/hooks/useSession";
import { NewRunModal } from "@/components/NewRunModal";
import { fadeUp, staggerContainer } from "@/components/motion";
import { Card, EmptyState, ErrorNote, Kbd, LanguageBadge, ProgressBar, RunStatusPill, TableSkeleton } from "@/components/ui";

const PAGE = 20;
const STATUS_FILTERS: Array<{ id: RunStatus | "all"; label: string }> = [
  { id: "all", label: "All" },
  { id: "running", label: "Running" },
  { id: "awaiting_review", label: "Awaiting review" },
  { id: "completed", label: "Completed" },
  { id: "failed", label: "Failed" },
  { id: "cancelled", label: "Cancelled" },
];

function CountChips({ counts }: { counts: Run["counts"] }) {
  const items: Array<[string, number, string]> = [
    ["cand", counts.candidate, "bg-amber-500/15 text-amber-700 dark:text-amber-300"],
    ["ver", counts.verified, "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"],
    ["fix", counts.fixed, "bg-emerald-500/20 text-emerald-700 dark:text-emerald-200"],
    ["ref", counts.refuted, "bg-rose-500/15 text-rose-700 dark:text-rose-300"],
    ["reg", counts.regressed, "bg-rose-500/20 text-rose-700 dark:text-rose-200"],
    ["pr", counts.pr_opened, "bg-sky-500/15 text-sky-700 dark:text-sky-300"],
  ];
  return (
    <div className="flex gap-1 flex-wrap">
      {items
        .filter(([, v]) => v > 0)
        .map(([k, v, cls]) => (
          <span key={k} className={clsx("rounded px-1.5 py-0.5 text-[10px] font-semibold tabular-nums", cls)} title={k}>
            {v} {k}
          </span>
        ))}
      {items.every(([, v]) => v === 0) && <span className="text-[10px] muted">-</span>}
    </div>
  );
}

export default function Runs() {
  const { can } = useSession();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const status = (params.get("status") ?? "all") as RunStatus | "all";
  const page = Math.max(0, Number(params.get("page") ?? 0));
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [modal, setModal] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const runs = useAsync(() => api.listRuns({ limit: PAGE, offset: page * PAGE, status: status === "all" ? undefined : status }), [status, page], { pollMs: 10_000 });

  useHotkey(
    "/",
    useCallback((e: KeyboardEvent) => {
      e.preventDefault();
      searchRef.current?.focus();
    }, []),
  );

  useEffect(() => {
    const st = location.state as { focusSearch?: boolean } | null;
    if (st?.focusSearch) setTimeout(() => searchRef.current?.focus(), 50);
  }, [location.state]);

  const setStatus = (s: RunStatus | "all") => {
    const next = new URLSearchParams(params);
    if (s === "all") next.delete("status");
    else next.set("status", s);
    next.delete("page");
    setParams(next, { replace: true });
  };
  const setPage = (p: number) => {
    const next = new URLSearchParams(params);
    if (p <= 0) next.delete("page");
    else next.set("page", String(p));
    setParams(next, { replace: true });
  };

  const filtered = useMemo(() => {
    const items = runs.data?.items ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((r) => r.repo_url.toLowerCase().includes(q) || r.id.toLowerCase().includes(q) || (r.commit_sha ?? "").startsWith(q) || (r.language ?? "").toLowerCase().includes(q));
  }, [runs.data, query]);

  const total = runs.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <div className="space-y-5">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Runs</h1>
          <p className="text-sm muted mt-1">{total ? `${total.toLocaleString()} audits` : "Audit history"}</p>
        </div>
        <button className="btn-primary" onClick={() => setModal(true)} disabled={!can("operator")} title={can("operator") ? "Start a new audit" : "Operator role required"}>
          <Plus className="h-4 w-4" /> New audit
        </button>
      </div>

      <div className="flex flex-col md:flex-row gap-3 md:items-center">
        <div className="flex gap-1.5 flex-wrap" role="group" aria-label="Filter by status">
          {STATUS_FILTERS.map((f) => (
            <button key={f.id} className={clsx("chip", status === f.id ? "chip-active" : "chip-idle")} onClick={() => setStatus(f.id)} aria-pressed={status === f.id}>
              {f.label}
            </button>
          ))}
        </div>
        <div className="relative md:ml-auto md:w-80">
          <Search className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 muted pointer-events-none" />
          <input ref={searchRef} className="input pl-9 pr-9" placeholder="Search repo, id, sha…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search runs" />
          <span className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none">
            <Kbd>/</Kbd>
          </span>
        </div>
      </div>

      <Card padded={false} className="overflow-hidden">
        {runs.error && (
          <div className="p-4">
            <ErrorNote message={runs.error} onRetry={runs.reload} />
          </div>
        )}
        {runs.loading && !runs.data ? (
          <TableSkeleton rows={8} cols={6} />
        ) : filtered.length === 0 ? (
          <EmptyState title="No runs match" description={query ? "Try a different search term." : "Start a new audit to populate this list."} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px]">
              <thead>
                <tr className="border-b border-slate-200/80 dark:border-white/[0.06]">
                  <th className="th">Repository</th>
                  <th className="th">Status</th>
                  <th className="th w-44">Progress</th>
                  <th className="th">Findings</th>
                  <th className="th">Cost</th>
                  <th className="th">Started</th>
                </tr>
              </thead>
              <motion.tbody key={`${status}-${page}`} variants={staggerContainer} initial="hidden" animate="show">
                {filtered.map((r) => (
                  <motion.tr key={r.id} variants={fadeUp} className="border-b border-slate-200/50 dark:border-white/[0.04] hover:bg-slate-900/[0.02] dark:hover:bg-white/[0.02] transition-colors">
                    <td className="td">
                      <Link to={`/runs/${r.id}`} className="group block">
                        <span className="font-medium group-hover:text-indigo-500 inline-flex items-center gap-2">
                          {repoName(r.repo_url)}
                          <LanguageBadge language={r.language} />
                        </span>
                        <span className="block text-[11px] muted font-mono mt-0.5">
                          {r.id} · {shortSha(r.commit_sha)}
                          {r.current_node && r.status === "running" ? ` · ${r.current_node}` : ""}
                        </span>
                      </Link>
                    </td>
                    <td className="td">
                      <RunStatusPill status={r.status} />
                    </td>
                    <td className="td">
                      <div className="flex items-center gap-2">
                        <ProgressBar value={r.progress} color={r.status === "failed" ? "rose" : r.status === "completed" ? "emerald" : r.status === "awaiting_review" ? "amber" : "indigo"} className="flex-1" />
                        <span className="text-[11px] tabular-nums muted w-8 text-right">{Math.round(r.progress * 100)}%</span>
                      </div>
                    </td>
                    <td className="td">
                      <CountChips counts={r.counts} />
                    </td>
                    <td className="td tabular-nums">
                      {usd(r.cost_usd)} <span className="muted text-xs">/ {usd(r.budget.max_usd, { digits: 0 })}</span>
                    </td>
                    <td className="td muted whitespace-nowrap" title={r.started_at ?? undefined}>
                      {relativeTime(r.started_at)}
                    </td>
                  </motion.tr>
                ))}
              </motion.tbody>
            </table>
          </div>
        )}
        <div className="flex items-center justify-between px-4 py-3 border-t border-slate-200/80 dark:border-white/[0.06] text-xs muted">
          <span>
            Page {page + 1} of {pages}
            {query && ` · ${filtered.length} shown`}
          </span>
          <div className="flex gap-1">
            <button className="btn-outline !py-1 !px-2" onClick={() => setPage(page - 1)} disabled={page === 0} aria-label="Previous page">
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button className="btn-outline !py-1 !px-2" onClick={() => setPage(page + 1)} disabled={page + 1 >= pages} aria-label="Next page">
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </Card>

      <NewRunModal open={modal} onClose={() => setModal(false)} />
    </div>
  );
}
