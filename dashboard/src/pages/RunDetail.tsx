import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { ArrowLeft, ArrowUpDown, Ban, Check, Download, ExternalLink, FileJson, FileText, GitBranch, RefreshCw, X } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage } from "@/lib/api";
import type { Finding, FindingStatus, Severity } from "@/lib/types";
import { absoluteTime, duration, relativeTime, repoName, shortSha, titleCase } from "@/lib/format";
import { useRunEvents } from "@/lib/useRunEvents";
import { useSession } from "@/hooks/useSession";
import { useToast } from "@/hooks/useToast";
import { ActivityFeed } from "@/components/ActivityFeed";
import { CostGauge } from "@/components/CostGauge";
import { FindingDrawer } from "@/components/FindingDrawer";
import { PipelineGraph } from "@/components/PipelineGraph";
import { fadeUp, staggerContainer } from "@/components/motion";
import { Card, CategoryChip, ConfidenceBar, EmptyState, ErrorNote, FindingStatusPill, LanguageBadge, ProgressBar, RunStatusPill, SeverityPill, Skeleton, Spinner } from "@/components/ui";

type SortKey = "severity" | "confidence" | "status" | "category" | "file";
const SEV_RANK: Record<Severity, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const STATUS_RANK: Record<FindingStatus, number> = { pr_opened: 0, fixed: 1, approved: 2, verified: 3, regressed: 4, candidate: 5, refuted: 6, rejected: 7 };

export default function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const { can } = useSession();
  const toast = useToast();
  const { run, findings, events, connection, loading, error, refresh, setFinding, setRun } = useRunEvents(id);

  const [selected, setSelected] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<FindingStatus | "all">("all");
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "severity", dir: 1 });
  const [cancelling, setCancelling] = useState(false);
  const [deciding, setDeciding] = useState<string | null>(null);

  const categories = useMemo(() => Array.from(new Set(findings.map((f) => f.category))).sort(), [findings]);
  const visible = useMemo(() => {
    let list = findings;
    if (statusFilter !== "all") list = list.filter((f) => f.status === statusFilter);
    if (categoryFilter !== "all") list = list.filter((f) => f.category === categoryFilter);
    const cmp = (a: Finding, b: Finding): number => {
      switch (sort.key) {
        case "severity":
          return SEV_RANK[a.severity] - SEV_RANK[b.severity] || b.confidence - a.confidence;
        case "confidence":
          return b.confidence - a.confidence;
        case "status":
          return STATUS_RANK[a.status] - STATUS_RANK[b.status];
        case "category":
          return a.category.localeCompare(b.category);
        case "file":
          return a.file.localeCompare(b.file) || a.line_start - b.line_start;
      }
    };
    return [...list].sort((a, b) => cmp(a, b) * sort.dir);
  }, [findings, statusFilter, categoryFilter, sort]);

  const selectedFinding = useMemo(() => findings.find((f) => f.id === selected) ?? null, [findings, selected]);

  const toggleSort = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: 1 }));

  const cancel = useCallback(async () => {
    if (!id || !run) return;
    if (!window.confirm(`Cancel run ${run.id}?`)) return;
    setCancelling(true);
    try {
      const r = await api.cancelRun(id);
      setRun(r);
      toast.warning("Run cancelled", r.id);
    } catch (e) {
      toast.error("Could not cancel run", errorMessage(e));
    } finally {
      setCancelling(false);
    }
  }, [id, run, setRun, toast]);

  const decide = useCallback(
    async (f: Finding, decision: "approve" | "reject") => {
      if (!id) return;
      const prev = f;
      setDeciding(f.id);
      setFinding({ ...f, status: decision === "approve" ? "approved" : "rejected" }); // optimistic
      try {
        const updated = await api.decide(id, f.id, { decision, note: "" });
        setFinding(updated);
        toast.success(decision === "approve" ? "Approved" : "Rejected", `${f.id} · ${f.category}`);
      } catch (e) {
        setFinding(prev);
        toast.error("Decision failed", errorMessage(e));
      } finally {
        setDeciding(null);
      }
    },
    [id, setFinding, toast],
  );

  const isActive = run?.status === "running" || run?.status === "created" || run?.status === "awaiting_review";
  const awaiting = run?.status === "awaiting_review";
  const pendingReview = awaiting ? findings.filter((f) => f.status === "fixed" || f.status === "verified").length : 0;
  const elapsed = run?.started_at ? ((run.finished_at ? new Date(run.finished_at).getTime() : Date.now()) - new Date(run.started_at).getTime()) / 1000 : null;

  if (error && !run) {
    return (
      <div className="space-y-4">
        <Link to="/runs" className="btn-ghost !px-2 text-xs">
          <ArrowLeft className="h-4 w-4" /> Runs
        </Link>
        <ErrorNote message={error} onRetry={() => void refresh()} />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-col gap-3">
        <Link to="/runs" className="text-xs muted hover:text-indigo-500 inline-flex items-center gap-1 w-fit">
          <ArrowLeft className="h-3.5 w-3.5" /> All runs
        </Link>
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
          <div className="min-w-0">
            {run ? (
              <>
                <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2 flex-wrap">
                  <span className="truncate">{repoName(run.repo_url)}</span>
                  <LanguageBadge language={run.language} />
                  <RunStatusPill status={run.status} />
                  {run.arm && <span className="pill bg-violet-500/10 text-violet-700 dark:text-violet-300">{run.arm}</span>}
                </h1>
                <p className="text-xs muted mt-1.5 font-mono flex items-center gap-2 flex-wrap">
                  <span>{run.id}</span>
                  <span>·</span>
                  <span className="inline-flex items-center gap-1">
                    <GitBranch className="h-3 w-3" /> {shortSha(run.commit_sha)}
                  </span>
                  <span>·</span>
                  <a href={run.repo_url.startsWith("http") ? run.repo_url : undefined} className={clsx("truncate max-w-[280px]", run.repo_url.startsWith("http") && "hover:text-indigo-500")} target="_blank" rel="noreferrer">
                    {run.repo_url}
                  </a>
                  <span>·</span>
                  <span title={absoluteTime(run.started_at)}>started {relativeTime(run.started_at)}</span>
                  {elapsed != null && <span>· {duration(elapsed)} elapsed</span>}
                </p>
              </>
            ) : (
              <>
                <Skeleton className="h-8 w-72" />
                <Skeleton className="h-4 w-96 mt-2" />
              </>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <button className="btn-ghost !px-2.5" onClick={() => void refresh()} aria-label="Refresh" title="Refresh">
              <RefreshCw className="h-4 w-4" />
            </button>
            <div className="flex rounded-xl border border-slate-200 dark:border-white/10 overflow-hidden">
              {(
                [
                  ["html", "HTML", FileText],
                  ["json", "JSON", FileJson],
                  ["md", "MD", Download],
                ] as const
              ).map(([fmt, label, Icon]) => (
                <a key={fmt} href={id ? api.reportUrl(id, fmt) : "#"} download={`sentinel-${id}.${fmt}`} target="_blank" rel="noreferrer" className="btn-ghost rounded-none !py-1.5 !px-2.5 text-xs border-r last:border-r-0 border-slate-200 dark:border-white/10" title={`Download report.${fmt}`} aria-disabled={!run}>
                  <Icon className="h-3.5 w-3.5" /> {label}
                </a>
              ))}
            </div>
            {run && isActive && can("operator") && (
              <button className="btn-danger" onClick={cancel} disabled={cancelling}>
                {cancelling ? <Spinner /> : <Ban className="h-4 w-4" />} Cancel run
              </button>
            )}
          </div>
        </div>
      </div>

      {error && <ErrorNote message={error} onRetry={() => void refresh()} />}

      {/* Review banner */}
      <AnimatePresence>
        {awaiting && (
          <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} className="rounded-2xl border border-amber-400/40 bg-amber-500/10 px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-2" role="status">
            <span className="relative flex h-2.5 w-2.5 shrink-0">
              <span className="absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75 animate-ping" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
            </span>
            <p className="text-sm text-amber-800 dark:text-amber-200 flex-1">
              <strong>Awaiting review.</strong> {pendingReview} fix{pendingReview === 1 ? "" : "es"} pending. Approve or reject each finding below; the run resumes to <em>report</em> once all decisions are in.
            </p>
            {!can("operator") && <span className="text-xs muted">Operator role required to decide.</span>}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Pipeline */}
      <Card title="Pipeline" subtitle={run?.current_node ? `Currently in ${run.current_node}` : run ? titleCase(run.status) : undefined} actions={run && <span className="text-xs tabular-nums muted">{Math.round((run.progress ?? 0) * 100)}%</span>}>
        {run ? (
          <>
            <PipelineGraph nodes={run.nodes} currentNode={run.current_node} />
            <ProgressBar value={run.progress} className="mt-3" color={run.status === "failed" ? "rose" : run.status === "completed" ? "emerald" : awaiting ? "amber" : "indigo"} />
          </>
        ) : (
          <Skeleton className="h-24" />
        )}
      </Card>

      {/* Metrics + feed */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card title="Cost vs budget" subtitle={run ? `${run.budget.max_minutes} min · ${run.budget.max_findings} findings max` : undefined}>
          {run ? (
            <div className="flex flex-col items-center">
              <CostGauge cost={run.cost_usd} budget={run.budget.max_usd} />
              <motion.ul variants={staggerContainer} initial="hidden" animate="show" className="grid grid-cols-3 gap-2 w-full mt-2">
                {(
                  [
                    ["candidate", run.counts.candidate, "text-amber-600 dark:text-amber-300"],
                    ["verified", run.counts.verified, "text-emerald-600 dark:text-emerald-300"],
                    ["fixed", run.counts.fixed, "text-emerald-600 dark:text-emerald-300"],
                    ["refuted", run.counts.refuted, "text-rose-600 dark:text-rose-300"],
                    ["regressed", run.counts.regressed, "text-rose-600 dark:text-rose-300"],
                    ["pr_opened", run.counts.pr_opened, "text-sky-600 dark:text-sky-300"],
                  ] as const
                ).map(([k, v, cls]) => (
                  <motion.li key={k} variants={fadeUp} className="rounded-xl bg-slate-100/70 dark:bg-white/[0.04] px-2.5 py-2 text-center">
                    <span className={clsx("block text-lg font-semibold tabular-nums", cls)}>{v}</span>
                    <span className="block text-[10px] uppercase tracking-wider muted">{k.replace("_", " ")}</span>
                  </motion.li>
                ))}
              </motion.ul>
            </div>
          ) : (
            <Skeleton className="h-48" />
          )}
        </Card>
        <Card title="Live activity" subtitle={`${events.length} events`} className="xl:col-span-2">
          <ActivityFeed events={events} connection={connection} />
        </Card>
      </div>

      {/* Findings */}
      <Card
        title="Findings"
        subtitle={`${visible.length} of ${findings.length}`}
        padded={false}
        className="overflow-hidden"
        actions={
          <div className="flex gap-2 flex-wrap justify-end">
            <select className="input !w-auto !py-1 text-xs" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as FindingStatus | "all")} aria-label="Filter by status">
              <option value="all">All statuses</option>
              {(["candidate", "verified", "fixed", "pr_opened", "approved", "regressed", "refuted", "rejected"] as FindingStatus[]).map((s) => (
                <option key={s} value={s}>
                  {s.replace("_", " ")}
                </option>
              ))}
            </select>
            <select className="input !w-auto !py-1 text-xs" value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)} aria-label="Filter by category">
              <option value="all">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {titleCase(c)}
                </option>
              ))}
            </select>
          </div>
        }
      >
        <div className="px-4 sm:px-5 pt-4 sm:pt-5" />
        {loading && findings.length === 0 ? (
          <div className="p-4 space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-9" />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <EmptyState title={findings.length ? "No findings match the filters" : isActive ? "Hunting for bugs…" : "No findings"} description={findings.length ? undefined : isActive ? "Candidates appear here as the hunt stage produces them." : undefined} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px]">
              <thead>
                <tr className="border-b border-slate-200/80 dark:border-white/[0.06]">
                  <SortTh label="Severity" k="severity" sort={sort} onSort={toggleSort} />
                  <SortTh label="Category" k="category" sort={sort} onSort={toggleSort} />
                  <SortTh label="Location" k="file" sort={sort} onSort={toggleSort} />
                  <SortTh label="Confidence" k="confidence" sort={sort} onSort={toggleSort} />
                  <SortTh label="Status" k="status" sort={sort} onSort={toggleSort} />
                  <th className="th text-right">{awaiting && can("operator") ? "Decision" : ""}</th>
                </tr>
              </thead>
              <tbody>
                <AnimatePresence initial={false}>
                  {visible.map((f) => {
                    const pending = awaiting && (f.status === "fixed" || f.status === "verified");
                    return (
                      <motion.tr
                        key={f.id}
                        layout
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        className={clsx("border-b border-slate-200/50 dark:border-white/[0.04] hover:bg-indigo-500/[0.04] transition-colors cursor-pointer", selected === f.id && "bg-indigo-500/[0.06]")}
                        onClick={() => setSelected(f.id)}
                        tabIndex={0}
                        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setSelected(f.id))}
                        aria-label={`Open finding ${f.id}`}
                      >
                        <td className="td">
                          <SeverityPill severity={f.severity} />
                        </td>
                        <td className="td">
                          <CategoryChip category={f.category} />
                        </td>
                        <td className="td font-mono text-xs">
                          <span className="block truncate max-w-[280px]">{f.file}</span>
                          <span className="muted">
                            :{f.line_start}
                            {f.symbol ? ` · ${f.symbol}()` : ""}
                          </span>
                        </td>
                        <td className="td">
                          <ConfidenceBar value={f.confidence} />
                        </td>
                        <td className="td">
                          <div className="flex items-center gap-2">
                            <FindingStatusPill status={f.status} />
                            {f.pr_url && (
                              <a href={f.pr_url} target="_blank" rel="noreferrer" className="text-sky-500 hover:text-sky-400" onClick={(e) => e.stopPropagation()} aria-label="Open pull request">
                                <ExternalLink className="h-3.5 w-3.5" />
                              </a>
                            )}
                          </div>
                        </td>
                        <td className="td text-right">
                          {pending && can("operator") && (
                            <div className="inline-flex gap-1" onClick={(e) => e.stopPropagation()}>
                              <button className="btn-success !py-1 !px-2 text-xs" onClick={() => decide(f, "approve")} disabled={deciding === f.id} aria-label={`Approve ${f.id}`}>
                                {deciding === f.id ? <Spinner className="h-3 w-3" /> : <Check className="h-3.5 w-3.5" />}
                              </button>
                              <button className="btn-danger !py-1 !px-2 text-xs" onClick={() => decide(f, "reject")} disabled={deciding === f.id} aria-label={`Reject ${f.id}`}>
                                <X className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          )}
                        </td>
                      </motion.tr>
                    );
                  })}
                </AnimatePresence>
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <FindingDrawer finding={selectedFinding} onClose={() => setSelected(null)} canDecide={!!awaiting && can("operator")} onDecide={decide} deciding={deciding} />
    </div>
  );
}

function SortTh({ label, k, sort, onSort }: { label: string; k: SortKey; sort: { key: SortKey; dir: 1 | -1 }; onSort: (k: SortKey) => void }) {
  const active = sort.key === k;
  return (
    <th className="th" aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button className={clsx("inline-flex items-center gap-1 hover:text-slate-900 dark:hover:text-slate-100", active && "text-indigo-600 dark:text-indigo-300")} onClick={() => onSort(k)}>
        {label}
        <ArrowUpDown className={clsx("h-3 w-3", active ? "opacity-100" : "opacity-40")} />
      </button>
    </th>
  );
}
