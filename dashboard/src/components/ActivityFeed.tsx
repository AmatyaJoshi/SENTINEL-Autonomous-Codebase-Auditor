import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { ArrowDownToLine, Bot, Bug, CircleDot, Flag, Info, Play, Square, Terminal, Wifi, WifiOff } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { RunEventEnvelope, RunEventType } from "@/lib/types";
import { duration, shortTime, usd } from "@/lib/format";
import type { Connection } from "@/lib/useRunEvents";

type FeedFilter = "all" | "llm" | "sandbox" | "log" | "findings" | "nodes";

function matches(type: RunEventType, filter: FeedFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "llm":
      return type === "llm.call";
    case "sandbox":
      return type === "sandbox.exec";
    case "log":
      return type === "log";
    case "findings":
      return type === "finding.new" || type === "finding.update";
    case "nodes":
      return type === "node.start" || type === "node.end" || type === "run.status" || type === "run.end";
  }
}

function EventLine({ env }: { env: RunEventEnvelope }) {
  const { event } = env;
  const time = <span className="tabular-nums text-slate-400 dark:text-slate-500 shrink-0 w-[62px]">{shortTime(env.receivedAt)}</span>;
  switch (event.type) {
    case "llm.call":
      return (
        <>
          {time}
          <Bot className="h-3.5 w-3.5 text-violet-500 shrink-0" />
          <span className="truncate">
            <span className="text-violet-600 dark:text-violet-300 font-medium">{event.data.node}</span> · {event.data.model} · {event.data.tokens_in.toLocaleString()}→{event.data.tokens_out.toLocaleString()} tok · {usd(event.data.cost_usd)} · {duration(event.data.duration_s)}
          </span>
        </>
      );
    case "sandbox.exec":
      return (
        <>
          {time}
          <Terminal className={clsx("h-3.5 w-3.5 shrink-0", event.data.exit_code === 0 ? "text-emerald-500" : "text-rose-500")} />
          <span className="truncate">
            <span className="text-sky-600 dark:text-sky-300 font-medium">{event.data.node}</span> · <span className="font-mono">{event.data.command}</span> · exit {event.data.exit_code}
            {event.data.timed_out && <span className="text-rose-500"> · timed out</span>} · {duration(event.data.duration_s)}
          </span>
        </>
      );
    case "log": {
      const lvl = event.data.level;
      return (
        <>
          {time}
          <Info className={clsx("h-3.5 w-3.5 shrink-0", lvl === "error" ? "text-rose-500" : lvl === "warning" ? "text-amber-500" : "text-slate-400")} />
          <span className={clsx("truncate", lvl === "error" && "text-rose-600 dark:text-rose-300", lvl === "warning" && "text-amber-700 dark:text-amber-300", lvl === "debug" && "muted")}>{event.data.message}</span>
        </>
      );
    }
    case "node.start":
      return (
        <>
          {time}
          <Play className="h-3.5 w-3.5 text-indigo-500 shrink-0" />
          <span className="truncate">
            Node <span className="font-medium">{event.data.node}</span> started
          </span>
        </>
      );
    case "node.end":
      return (
        <>
          {time}
          <Square className={clsx("h-3.5 w-3.5 shrink-0", event.data.ok ? "text-emerald-500" : "text-rose-500")} />
          <span className="truncate">
            Node <span className="font-medium">{event.data.node}</span> {event.data.ok ? "finished" : "failed"} in {duration(event.data.duration_s)}
          </span>
        </>
      );
    case "finding.new":
      return (
        <>
          {time}
          <Bug className="h-3.5 w-3.5 text-amber-500 shrink-0" />
          <span className="truncate">
            New candidate <span className="font-mono">{event.data.id}</span> · {event.data.category} · {event.data.file}:{event.data.line_start}
          </span>
        </>
      );
    case "finding.update":
      return (
        <>
          {time}
          <CircleDot className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
          <span className="truncate">
            <span className="font-mono">{event.data.id}</span> → <span className="font-medium">{event.data.status.replace("_", " ")}</span>
          </span>
        </>
      );
    case "run.status":
      return (
        <>
          {time}
          <Flag className="h-3.5 w-3.5 text-indigo-400 shrink-0" />
          <span className="truncate muted">
            status {event.data.status}
            {event.data.current_node ? ` · ${event.data.current_node}` : ""} · {Math.round(event.data.progress * 100)}%
          </span>
        </>
      );
    case "run.end":
      return (
        <>
          {time}
          <Flag className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
          <span className="truncate font-medium">Run {event.data.status}</span>
        </>
      );
  }
}

const FILTERS: Array<{ id: FeedFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "nodes", label: "Nodes" },
  { id: "llm", label: "LLM" },
  { id: "sandbox", label: "Sandbox" },
  { id: "findings", label: "Findings" },
  { id: "log", label: "Logs" },
];

export function ConnectionBadge({ connection }: { connection: Connection }) {
  const map: Record<Connection, { label: string; cls: string; icon: typeof Wifi }> = {
    idle: { label: "idle", cls: "text-slate-500", icon: WifiOff },
    connecting: { label: "connecting", cls: "text-amber-500", icon: Wifi },
    live: { label: "live", cls: "text-emerald-500", icon: Wifi },
    polling: { label: "polling 5s", cls: "text-sky-500", icon: Wifi },
    closed: { label: "stream closed", cls: "text-slate-500", icon: WifiOff },
    error: { label: "error", cls: "text-rose-500", icon: WifiOff },
  };
  const m = map[connection];
  const Icon = m.icon;
  return (
    <span className={clsx("inline-flex items-center gap-1 text-xs font-medium", m.cls)} aria-live="polite">
      <Icon className="h-3.5 w-3.5" />
      {connection === "live" && <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />}
      {m.label}
    </span>
  );
}

export function ActivityFeed({ events, connection, className }: { events: RunEventEnvelope[]; connection: Connection; className?: string }) {
  const [filter, setFilter] = useState<FeedFilter>("all");
  const [autoscroll, setAutoscroll] = useState(true);
  const ref = useRef<HTMLDivElement>(null);
  const visible = useMemo(() => events.filter((e) => matches(e.event.type, filter)), [events, filter]);

  useEffect(() => {
    if (autoscroll && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [visible.length, autoscroll]);

  return (
    <div className={clsx("flex flex-col min-h-0", className)}>
      <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
        <div className="flex gap-1 flex-wrap" role="group" aria-label="Filter activity">
          {FILTERS.map((f) => (
            <button key={f.id} className={clsx("chip", filter === f.id ? "chip-active" : "chip-idle")} onClick={() => setFilter(f.id)} aria-pressed={filter === f.id}>
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <ConnectionBadge connection={connection} />
          <button className={clsx("chip", autoscroll ? "chip-active" : "chip-idle")} onClick={() => setAutoscroll((v) => !v)} aria-pressed={autoscroll} title="Toggle autoscroll">
            <ArrowDownToLine className="h-3 w-3" /> Autoscroll
          </button>
        </div>
      </div>
      <div ref={ref} className="flex-1 min-h-[220px] max-h-[420px] overflow-y-auto rounded-xl border border-slate-200/80 dark:border-white/[0.06] bg-white/40 dark:bg-black/20 p-2 font-mono text-[12px] leading-5" role="log" aria-live="polite" aria-relevant="additions">
        {visible.length === 0 ? (
          <p className="muted text-center py-8 font-sans text-sm">No activity yet.</p>
        ) : (
          <AnimatePresence initial={false}>
            {visible.slice(-300).map((env) => (
              <motion.div key={env.id} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.18 }} className="flex items-center gap-2 px-1.5 py-0.5 rounded hover:bg-slate-900/[0.03] dark:hover:bg-white/[0.03]">
                <EventLine env={env} />
              </motion.div>
            ))}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}
