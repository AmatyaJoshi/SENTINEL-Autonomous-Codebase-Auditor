import { motion } from "framer-motion";
import clsx from "clsx";
import { Trophy } from "lucide-react";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/lib/api";
import type { Arm, BenchResult, CategoryCounts } from "@/lib/types";
import { ARMS } from "@/lib/types";
import { duration, percent, relativeTime, shortDate, titleCase, usd } from "@/lib/format";
import { useAsync } from "@/hooks/useAsync";
import { Counter } from "@/components/Counter";
import { ARM_COLOR, ChartTooltip, axisProps, gridProps } from "@/components/charts";
import { fadeUp, staggerContainer } from "@/components/motion";
import { Card, EmptyState, ErrorNote, Skeleton, TableSkeleton } from "@/components/ui";

type Suite = "all" | "small" | "full";

function latestPerArm(items: BenchResult[]): Partial<Record<Arm, BenchResult>> {
  const out: Partial<Record<Arm, BenchResult>> = {};
  for (const r of [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))) {
    if (!out[r.arm]) out[r.arm] = r;
    // some servers embed all arms in a single result
    if (r.arms) {
      for (const [arm, m] of Object.entries(r.arms) as Array<[Arm, NonNullable<BenchResult["arms"]>[Arm]]>) {
        if (!out[arm] && m) out[arm] = { ...r, arm, ...m, per_category: m.per_category ?? r.per_category };
      }
    }
  }
  return out;
}

export default function Bench() {
  const [suite, setSuite] = useState<Suite>("all");
  const bench = useAsync(() => api.benchResults({ suite: suite === "all" ? undefined : suite, limit: 20 }), [suite]);
  const items = useMemo(() => bench.data?.items ?? [], [bench.data]);
  const latest = useMemo(() => latestPerArm(items), [items]);

  const grouped = useMemo(
    () =>
      (["precision", "recall", "f1"] as const).map((metric) => ({
        metric: metric.toUpperCase(),
        ...Object.fromEntries(ARMS.map((a) => [a, latest[a]?.[metric] ?? 0])),
      })),
    [latest],
  );

  const trend = useMemo(() => {
    const byDay = new Map<string, Record<string, number | string>>();
    for (const r of [...items].sort((a, b) => a.created_at.localeCompare(b.created_at))) {
      const day = r.created_at.slice(0, 10);
      const row = byDay.get(day) ?? { day, label: shortDate(r.created_at) };
      row[r.arm] = r.precision;
      byDay.set(day, row);
    }
    return [...byDay.values()];
  }, [items]);

  const [heatArm, setHeatArm] = useState<Arm>("full");
  const heat = latest[heatArm]?.per_category ?? {};
  const heatRows = Object.entries(heat).sort((a, b) => b[1].tp + b[1].fp + b[1].fn - (a[1].tp + a[1].fp + a[1].fn));

  const full = latest.full;
  const single = latest.single_shot;
  const delta = full && single ? full.precision - single.precision : null;

  return (
    <div className="space-y-5">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Benchmark</h1>
          <p className="text-sm muted mt-1">Ablation arms on the seeded-bug suite. Headline comparison: <strong className="text-slate-900 dark:text-slate-100">full</strong> vs <strong className="text-slate-900 dark:text-slate-100">single_shot</strong>.</p>
        </div>
        <div className="flex gap-1.5" role="group" aria-label="Suite">
          {(["all", "small", "full"] as Suite[]).map((s) => (
            <button key={s} className={clsx("chip", suite === s ? "chip-active" : "chip-idle")} onClick={() => setSuite(s)} aria-pressed={suite === s}>
              {s === "all" ? "All suites" : s}
            </button>
          ))}
        </div>
      </div>

      {bench.error && <ErrorNote message={bench.error} onRetry={bench.reload} />}

      {/* Headline */}
      <motion.div variants={staggerContainer} initial="hidden" animate="show" className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <motion.div variants={fadeUp} className="card p-5 md:col-span-1 relative overflow-hidden bg-gradient-to-br from-indigo-500/10 via-transparent to-violet-500/10">
          <div className="flex items-center gap-2 text-xs muted">
            <Trophy className="h-4 w-4 text-amber-500" /> Headline
          </div>
          {full && single ? (
            <>
              <p className="mt-3 text-4xl font-semibold tabular-nums tracking-tight">
                <Counter value={delta ?? 0} format={(v) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}`} />
                <span className="text-lg muted"> pts</span>
              </p>
              <p className="text-sm mt-1">
                precision, <span className="font-medium" style={{ color: ARM_COLOR.full }}>full</span> {percent(full.precision, 1)} vs <span className="font-medium" style={{ color: ARM_COLOR.single_shot }}>single_shot</span> {percent(single.precision, 1)}
              </p>
              <p className="text-xs muted mt-2">
                recall {percent(full.recall, 1)} vs {percent(single.recall, 1)} · F1 {full.f1.toFixed(2)} vs {single.f1.toFixed(2)} · cost {usd(full.cost_usd)} vs {usd(single.cost_usd)}
              </p>
            </>
          ) : bench.loading ? (
            <Skeleton className="h-24 mt-3" />
          ) : (
            <p className="text-sm muted mt-3">Need results for both arms.</p>
          )}
        </motion.div>
        {(["full", "single_shot"] as Arm[]).map((arm) => {
          const r = latest[arm];
          return (
            <motion.div key={arm} variants={fadeUp} className={clsx("card p-5", arm === "full" && "ring-1 ring-indigo-400/40")}>
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold inline-flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-sm" style={{ background: ARM_COLOR[arm] }} /> {arm}
                </span>
                {r && <span className="text-[11px] muted">{relativeTime(r.created_at)}</span>}
              </div>
              {r ? (
                <div className="grid grid-cols-3 gap-2 mt-3">
                  {(
                    [
                      ["Precision", r.precision],
                      ["Recall", r.recall],
                      ["F1", r.f1],
                    ] as const
                  ).map(([l, v]) => (
                    <div key={l} className="rounded-xl bg-slate-100/70 dark:bg-white/[0.04] px-2.5 py-2">
                      <p className="text-[10px] uppercase tracking-wider muted">{l}</p>
                      <Counter value={v} format={(x) => percent(x, 1)} className="text-lg font-semibold tabular-nums" />
                    </div>
                  ))}
                  <p className="col-span-3 text-[11px] muted">
                    strict {percent(r.precision_strict, 1)} · verified {percent(r.verified_rate)} · patch pass {percent(r.patch_pass_rate)} · {duration(r.wall_clock_s)}
                  </p>
                </div>
              ) : (
                <Skeleton className="h-20 mt-3" />
              )}
            </motion.div>
          );
        })}
      </motion.div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card title="Latest metrics per arm" subtitle="Most recent result for each arm">
          {bench.loading && !bench.data ? (
            <Skeleton className="h-[280px]" />
          ) : Object.keys(latest).length === 0 ? (
            <EmptyState title="No benchmark results" />
          ) : (
            <div className="h-[280px] text-slate-600 dark:text-slate-300">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={grouped} margin={{ top: 8, right: 8, left: -16, bottom: 0 }} barGap={3} barCategoryGap="22%">
                  <CartesianGrid {...gridProps} />
                  <XAxis dataKey="metric" {...axisProps} />
                  <YAxis {...axisProps} domain={[0, 1]} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} />
                  <Tooltip cursor={{ fill: "currentColor", fillOpacity: 0.04 }} content={<ChartTooltip format={(v) => percent(Number(v), 1)} />} />
                  <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 11 }} />
                  {ARMS.map((a) => (
                    <Bar key={a} dataKey={a} name={a} fill={ARM_COLOR[a]} radius={[5, 5, 0, 0]} fillOpacity={a === "full" || a === "single_shot" ? 1 : 0.55} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card title="Precision over time" subtitle="Per arm, daily">
          {bench.loading && !bench.data ? (
            <Skeleton className="h-[280px]" />
          ) : trend.length === 0 ? (
            <EmptyState title="No trend yet" />
          ) : (
            <div className="h-[280px] text-slate-600 dark:text-slate-300">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trend} margin={{ top: 8, right: 12, left: -16, bottom: 0 }}>
                  <CartesianGrid {...gridProps} />
                  <XAxis dataKey="label" {...axisProps} />
                  <YAxis {...axisProps} domain={[0, 1]} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} />
                  <Tooltip content={<ChartTooltip format={(v) => percent(Number(v), 1)} />} />
                  <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 11 }} />
                  {ARMS.map((a) => (
                    <Line key={a} type="monotone" dataKey={a} name={a} stroke={ARM_COLOR[a]} strokeWidth={a === "full" ? 3 : 2} dot={false} activeDot={{ r: 4 }} strokeOpacity={a === "full" || a === "single_shot" ? 1 : 0.6} connectNulls />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>

      <Card
        title="Per-category outcomes"
        subtitle="True positives, false positives, and misses for the latest result"
        actions={
          <select className="input !w-auto !py-1 text-xs" value={heatArm} onChange={(e) => setHeatArm(e.target.value as Arm)} aria-label="Arm">
            {ARMS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        }
      >
        {heatRows.length === 0 ? (
          <EmptyState title="No per-category data" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px]">
              <thead>
                <tr className="border-b border-slate-200/80 dark:border-white/[0.06]">
                  <th className="th">Category</th>
                  <th className="th text-center">TP</th>
                  <th className="th text-center">FP</th>
                  <th className="th text-center">FN</th>
                  <th className="th text-center">Precision</th>
                  <th className="th text-center">Recall</th>
                </tr>
              </thead>
              <tbody>
                {heatRows.map(([cat, c]) => {
                  const p = c.tp + c.fp ? c.tp / (c.tp + c.fp) : 0;
                  const r = c.tp + c.fn ? c.tp / (c.tp + c.fn) : 0;
                  return (
                    <tr key={cat} className="border-b border-slate-200/50 dark:border-white/[0.04]">
                      <td className="td font-medium">{titleCase(cat)}</td>
                      <HeatCell v={c.tp} max={maxOf(heatRows, "tp")} hue="16 185 129" />
                      <HeatCell v={c.fp} max={maxOf(heatRows, "fp")} hue="244 63 94" />
                      <HeatCell v={c.fn} max={maxOf(heatRows, "fn")} hue="245 158 11" />
                      <td className="td text-center tabular-nums">{percent(p)}</td>
                      <td className="td text-center tabular-nums">{percent(r)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Benchmark runs" padded={false} className="overflow-hidden" subtitle={`${items.length} results`}>
        <div className="px-5 pt-5" />
        {bench.loading && !bench.data ? (
          <TableSkeleton rows={6} cols={7} />
        ) : items.length === 0 ? (
          <EmptyState title="No benchmark runs" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px]">
              <thead>
                <tr className="border-b border-slate-200/80 dark:border-white/[0.06]">
                  <th className="th">When</th>
                  <th className="th">Arm</th>
                  <th className="th">Suite</th>
                  <th className="th">Commit</th>
                  <th className="th text-right">Precision</th>
                  <th className="th text-right">Strict</th>
                  <th className="th text-right">Recall</th>
                  <th className="th text-right">F1</th>
                  <th className="th text-right">Patch pass</th>
                  <th className="th text-right">Cost</th>
                  <th className="th text-right">Wall</th>
                </tr>
              </thead>
              <motion.tbody variants={staggerContainer} initial="hidden" animate="show">
                {items.map((r) => (
                  <motion.tr key={r.id} variants={fadeUp} className="border-b border-slate-200/50 dark:border-white/[0.04] hover:bg-slate-900/[0.02] dark:hover:bg-white/[0.02]">
                    <td className="td muted whitespace-nowrap" title={r.created_at}>
                      {relativeTime(r.created_at)}
                    </td>
                    <td className="td">
                      <span className="inline-flex items-center gap-1.5 text-xs font-medium">
                        <span className="h-2 w-2 rounded-sm" style={{ background: ARM_COLOR[r.arm] }} /> {r.arm}
                      </span>
                    </td>
                    <td className="td text-xs">{r.suite}</td>
                    <td className="td font-mono text-xs">{r.commit.slice(0, 7)}</td>
                    <td className="td text-right tabular-nums">{percent(r.precision, 1)}</td>
                    <td className="td text-right tabular-nums muted">{percent(r.precision_strict, 1)}</td>
                    <td className="td text-right tabular-nums">{percent(r.recall, 1)}</td>
                    <td className="td text-right tabular-nums">{r.f1.toFixed(2)}</td>
                    <td className="td text-right tabular-nums">{percent(r.patch_pass_rate)}</td>
                    <td className="td text-right tabular-nums">{usd(r.cost_usd)}</td>
                    <td className="td text-right tabular-nums muted">{duration(r.wall_clock_s)}</td>
                  </motion.tr>
                ))}
              </motion.tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function maxOf(rows: Array<[string, CategoryCounts]>, k: keyof CategoryCounts): number {
  return Math.max(1, ...rows.map(([, c]) => c[k]));
}

function HeatCell({ v, max, hue }: { v: number; max: number; hue: string }) {
  const alpha = v === 0 ? 0 : 0.15 + 0.55 * (v / max);
  return (
    <td className="td text-center tabular-nums">
      <span className="inline-flex min-w-[36px] justify-center rounded-md px-2 py-0.5 font-medium" style={{ background: `rgb(${hue} / ${alpha})` }}>
        {v}
      </span>
    </td>
  );
}
