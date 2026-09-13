import { motion } from "framer-motion";
import { Activity, ArrowRight, Bug, CircleDollarSign, GitPullRequest, Plus, ShieldCheck, Target, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/lib/api";
import type { Stats } from "@/lib/types";
import { percent, relativeTime, repoName, titleCase, usd } from "@/lib/format";
import { useAsync } from "@/hooks/useAsync";
import { useSession } from "@/hooks/useSession";
import { Counter } from "@/components/Counter";
import { NewRunModal } from "@/components/NewRunModal";
import { CATEGORY_COLORS, ChartTooltip, axisProps } from "@/components/charts";
import { fadeUp, staggerContainer } from "@/components/motion";
import { Card, EmptyState, ErrorNote, FINDING_STATUS_COLOR, LanguageBadge, ProgressBar, RunStatusPill, Skeleton, TableSkeleton } from "@/components/ui";

interface Tile {
  key: keyof Stats | "precision";
  label: string;
  icon: typeof Activity;
  color: string;
  value: (s: Stats) => number;
  format: (v: number) => string;
  hint?: (s: Stats) => string;
}

const TILES: Tile[] = [
  { key: "runs_total", label: "Runs", icon: Activity, color: "text-indigo-500 bg-indigo-500/10", value: (s) => s.runs_total, format: (v) => Math.round(v).toLocaleString(), hint: (s) => `${s.runs_active} active` },
  { key: "verified_total", label: "Verified bugs", icon: ShieldCheck, color: "text-emerald-500 bg-emerald-500/10", value: (s) => s.verified_total, format: (v) => Math.round(v).toLocaleString(), hint: (s) => `${s.findings_total} findings` },
  { key: "fixed_total", label: "Fixed", icon: Wrench, color: "text-emerald-500 bg-emerald-500/10", value: (s) => s.fixed_total, format: (v) => Math.round(v).toLocaleString() },
  { key: "pr_opened_total", label: "PRs opened", icon: GitPullRequest, color: "text-sky-500 bg-sky-500/10", value: (s) => s.pr_opened_total, format: (v) => Math.round(v).toLocaleString() },
  { key: "cost_usd_total", label: "Total cost", icon: CircleDollarSign, color: "text-amber-500 bg-amber-500/10", value: (s) => s.cost_usd_total, format: (v) => usd(v, { digits: 2 }) },
  { key: "precision", label: "Latest precision", icon: Target, color: "text-violet-500 bg-violet-500/10", value: (s) => s.precision_latest ?? 0, format: (v) => percent(v, 1), hint: (s) => (s.precision_latest == null ? "no bench yet" : "full arm") },
];

export default function Overview() {
  const { can } = useSession();
  const stats = useAsync(() => api.stats(), [], { pollMs: 15_000 });
  const runs = useAsync(() => api.listRuns({ limit: 6, offset: 0 }), [], { pollMs: 10_000 });
  const [modal, setModal] = useState(false);

  const byCategory = useMemo(() => Object.entries(stats.data?.by_category ?? {}).map(([name, value]) => ({ name: titleCase(name), value })).sort((a, b) => b.value - a.value), [stats.data]);
  const byStatus = useMemo(() => Object.entries(stats.data?.by_status ?? {}).map(([name, value]) => ({ name, value })).filter((d) => d.value > 0), [stats.data]);
  const statusTotal = byStatus.reduce((s, d) => s + d.value, 0);

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
          <p className="text-sm muted mt-1">Autonomous audits, verified bugs, and fixes across your repositories.</p>
        </div>
        <button className="btn-primary" onClick={() => setModal(true)} disabled={!can("operator")} title={can("operator") ? "Start a new audit" : "Operator role required"}>
          <Plus className="h-4 w-4" /> New audit
        </button>
      </div>

      {stats.error && <ErrorNote message={stats.error} onRetry={stats.reload} />}

      <motion.div variants={staggerContainer} initial="hidden" animate="show" className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        {TILES.map((t) => (
          <motion.div key={t.key} variants={fadeUp} className="card p-4 relative overflow-hidden group">
            <div className="absolute -right-6 -top-6 h-20 w-20 rounded-full bg-gradient-to-br from-indigo-500/10 to-violet-500/10 blur-xl group-hover:scale-125 transition-transform" aria-hidden="true" />
            <div className={`h-8 w-8 rounded-lg flex items-center justify-center ${t.color}`}>
              <t.icon className="h-4 w-4" />
            </div>
            <p className="text-xs muted mt-3">{t.label}</p>
            {stats.data ? <Counter value={t.value(stats.data)} format={t.format} className="text-2xl font-semibold tabular-nums tracking-tight block" /> : <Skeleton className="h-7 w-16 mt-1" />}
            {t.hint && stats.data && <p className="text-[11px] muted mt-0.5">{t.hint(stats.data)}</p>}
          </motion.div>
        ))}
      </motion.div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Card title="Findings by category" subtitle="All runs" className="lg:col-span-3">
          {stats.loading && !stats.data ? (
            <Skeleton className="h-[240px]" />
          ) : byCategory.length === 0 ? (
            <EmptyState title="No findings yet" icon={<Bug className="h-6 w-6" />} />
          ) : (
            <div className="h-[240px] text-slate-600 dark:text-slate-300">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={byCategory} margin={{ top: 8, right: 8, left: -20, bottom: 0 }} barCategoryGap="28%">
                  <XAxis dataKey="name" {...axisProps} interval={0} angle={byCategory.length > 6 ? -20 : 0} textAnchor={byCategory.length > 6 ? "end" : "middle"} height={byCategory.length > 6 ? 48 : 30} />
                  <YAxis {...axisProps} allowDecimals={false} />
                  <Tooltip cursor={{ fill: "currentColor", fillOpacity: 0.04 }} content={<ChartTooltip />} />
                  <Bar dataKey="value" name="Findings" radius={[6, 6, 0, 0]} isAnimationActive>
                    {byCategory.map((_, i) => (
                      <Cell key={i} fill={CATEGORY_COLORS[i % CATEGORY_COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card title="Finding status" subtitle={statusTotal ? `${statusTotal} total` : undefined} className="lg:col-span-2">
          {stats.loading && !stats.data ? (
            <Skeleton className="h-[240px]" />
          ) : byStatus.length === 0 ? (
            <EmptyState title="Nothing to show" />
          ) : (
            <div className="flex flex-col sm:flex-row lg:flex-col items-center gap-4">
              <div className="h-[200px] w-[200px] shrink-0 relative">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={byStatus} dataKey="value" nameKey="name" innerRadius={62} outerRadius={90} paddingAngle={3} cornerRadius={6} stroke="none">
                      {byStatus.map((d) => (
                        <Cell key={d.name} fill={FINDING_STATUS_COLOR[d.name] ?? "#94a3b8"} />
                      ))}
                    </Pie>
                    <Tooltip content={<ChartTooltip />} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                  <Counter value={statusTotal} className="text-2xl font-semibold tabular-nums" />
                  <span className="text-[11px] muted">findings</span>
                </div>
              </div>
              <ul className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs w-full">
                {byStatus.map((d) => (
                  <li key={d.name} className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-sm" style={{ background: FINDING_STATUS_COLOR[d.name] ?? "#94a3b8" }} />
                    <span className="muted capitalize">{d.name.replace("_", " ")}</span>
                    <span className="ml-auto tabular-nums font-medium">{d.value}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      </div>

      <Card
        title="Recent runs"
        padded={false}
        className="overflow-hidden"
        actions={
          <Link to="/runs" className="btn-ghost !py-1 text-xs">
            All runs <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        }
      >
        <div className="px-4 sm:px-5 pt-4 sm:pt-5" />
        {runs.loading && !runs.data ? (
          <TableSkeleton rows={4} cols={5} />
        ) : runs.error ? (
          <div className="p-4">
            <ErrorNote message={runs.error} onRetry={runs.reload} />
          </div>
        ) : !runs.data?.items.length ? (
          <EmptyState title="No runs yet" description="Start a new audit to see it here." action={<button className="btn-primary" onClick={() => setModal(true)}>New audit</button>} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead>
                <tr className="border-b border-slate-200/80 dark:border-white/[0.06]">
                  <th className="th">Repository</th>
                  <th className="th">Status</th>
                  <th className="th w-40">Progress</th>
                  <th className="th">Verified / Fixed</th>
                  <th className="th">Cost</th>
                  <th className="th">Started</th>
                </tr>
              </thead>
              <motion.tbody variants={staggerContainer} initial="hidden" animate="show">
                {runs.data.items.map((r) => (
                  <motion.tr key={r.id} variants={fadeUp} className="border-b border-slate-200/50 dark:border-white/[0.04] hover:bg-slate-900/[0.02] dark:hover:bg-white/[0.02] transition-colors">
                    <td className="td">
                      <Link to={`/runs/${r.id}`} className="font-medium hover:text-indigo-500 inline-flex items-center gap-2">
                        {repoName(r.repo_url)}
                        <LanguageBadge language={r.language} />
                      </Link>
                    </td>
                    <td className="td">
                      <RunStatusPill status={r.status} />
                    </td>
                    <td className="td">
                      <ProgressBar value={r.progress} color={r.status === "failed" ? "rose" : r.status === "completed" ? "emerald" : "indigo"} />
                    </td>
                    <td className="td tabular-nums">
                      <span className="text-emerald-600 dark:text-emerald-300">{r.counts.verified}</span> / <span className="text-emerald-600 dark:text-emerald-300">{r.counts.fixed}</span>
                      <span className="muted"> of {Object.values(r.counts).reduce((a, b) => a + b, 0)}</span>
                    </td>
                    <td className="td tabular-nums">{usd(r.cost_usd)}</td>
                    <td className="td muted whitespace-nowrap" title={r.started_at ?? undefined}>
                      {relativeTime(r.started_at)}
                    </td>
                  </motion.tr>
                ))}
              </motion.tbody>
            </table>
          </div>
        )}
      </Card>

      <NewRunModal open={modal} onClose={() => setModal(false)} />
    </div>
  );
}
