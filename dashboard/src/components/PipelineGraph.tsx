import { motion, useReducedMotion } from "framer-motion";
import clsx from "clsx";
import { AlertTriangle, Check, Minus } from "lucide-react";
import type { RunNode, NodeStatus } from "@/lib/types";
import { duration, titleCase } from "@/lib/format";

const ICON_BG: Record<NodeStatus, string> = {
  pending: "bg-slate-200/70 dark:bg-white/[0.06] text-slate-500 dark:text-slate-400 border-slate-300 dark:border-white/10",
  running: "bg-indigo-500/15 text-indigo-600 dark:text-indigo-300 border-indigo-400/60 shadow-glow",
  done: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border-emerald-400/50",
  skipped: "bg-slate-200/50 dark:bg-white/[0.04] text-slate-400 dark:text-slate-500 border-dashed border-slate-300 dark:border-white/10",
  error: "bg-rose-500/15 text-rose-600 dark:text-rose-300 border-rose-400/60",
};

function NodeGlyph({ status }: { status: NodeStatus }) {
  switch (status) {
    case "done":
      return <Check className="h-4 w-4" strokeWidth={3} />;
    case "error":
      return <AlertTriangle className="h-4 w-4" />;
    case "skipped":
      return <Minus className="h-4 w-4" />;
    case "running":
      return (
        <span className="relative flex h-3 w-3">
          <span className="absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75 animate-ping" />
          <span className="relative inline-flex h-3 w-3 rounded-full bg-indigo-500" />
        </span>
      );
    default:
      return <span className="h-2 w-2 rounded-full bg-current opacity-60" />;
  }
}

/** Connector between two nodes; animates flow when the downstream node is running. */
function Connector({ active, done }: { active: boolean; done: boolean }) {
  const reduce = useReducedMotion();
  return (
    <div className="relative flex-1 min-w-[22px] sm:min-w-[32px] h-10 flex items-center" aria-hidden="true">
      <svg className="w-full h-2 overflow-visible" viewBox="0 0 100 8" preserveAspectRatio="none">
        <line x1="0" y1="4" x2="100" y2="4" stroke="currentColor" strokeWidth="2" className={clsx(done ? "text-emerald-400/70" : "text-slate-300 dark:text-white/10")} vectorEffect="non-scaling-stroke" />
        {active && (
          <line
            x1="0"
            y1="4"
            x2="100"
            y2="4"
            stroke="url(#pipe-flow)"
            strokeWidth="2.5"
            strokeDasharray="8 8"
            className={reduce ? undefined : "animate-flow"}
            vectorEffect="non-scaling-stroke"
            strokeLinecap="round"
          />
        )}
      </svg>
    </div>
  );
}

export function PipelineGraph({ nodes, currentNode, className }: { nodes: RunNode[]; currentNode?: string | null; className?: string }) {
  return (
    <div className={clsx("overflow-x-auto pb-1", className)} role="list" aria-label="Pipeline stages">
      <svg width="0" height="0" className="absolute" aria-hidden="true">
        <defs>
          <linearGradient id="pipe-flow" x1="0" x2="1">
            <stop offset="0" stopColor="#818cf8" />
            <stop offset="1" stopColor="#c084fc" />
          </linearGradient>
        </defs>
      </svg>
      <div className="flex items-start min-w-max px-1">
        {nodes.map((n, i) => {
          const status = n.status;
          const isCurrent = currentNode === n.name || status === "running";
          const prevDone = i > 0 && (nodes[i - 1]?.status === "done" || nodes[i - 1]?.status === "skipped");
          return (
            <div key={n.name} className="flex items-start">
              {i > 0 && <Connector active={isCurrent && status !== "done"} done={prevDone && (status === "done" || status === "skipped" || isCurrent)} />}
              <motion.div
                role="listitem"
                aria-label={`${titleCase(n.name)}: ${status}${n.duration_s ? `, ${duration(n.duration_s)}` : ""}`}
                className="flex flex-col items-center w-[64px] sm:w-[76px]"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.04, duration: 0.3 }}
              >
                <motion.div
                  layout
                  className={clsx("relative h-10 w-10 rounded-xl border flex items-center justify-center transition-colors duration-300", ICON_BG[status])}
                  animate={status === "running" ? { scale: [1, 1.06, 1] } : { scale: 1 }}
                  transition={status === "running" ? { repeat: Infinity, duration: 1.6, ease: "easeInOut" } : undefined}
                >
                  <NodeGlyph status={status} />
                  {status === "running" && <span className="absolute inset-0 rounded-xl border border-indigo-400/60 animate-pulseRing" aria-hidden="true" />}
                </motion.div>
                <span className={clsx("mt-2 text-[11px] sm:text-xs font-medium capitalize leading-none", status === "pending" || status === "skipped" ? "muted" : "")}>{n.name}</span>
                <span className="mt-1 text-[10px] tabular-nums muted h-3 leading-none">
                  {status === "done" || status === "error" ? duration(n.duration_s) : status === "skipped" ? "skipped" : status === "running" ? "running" : ""}
                </span>
              </motion.div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
