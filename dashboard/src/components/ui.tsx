import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Check, Copy, Inbox, X } from "lucide-react";
import clsx from "clsx";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { FindingStatus, NodeStatus, RunStatus, Severity } from "@/lib/types";
import { titleCase } from "@/lib/format";
import { spring } from "./motion";

/* ------------------------------ Card ------------------------------ */

export function Card({ className, children, title, subtitle, actions, padded = true }: { className?: string; children: ReactNode; title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; padded?: boolean }) {
  return (
    <section className={clsx("card", padded && "p-4 sm:p-5", className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 mb-4">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold tracking-tight">{title}</h2>}
            {subtitle && <p className="text-xs muted mt-0.5">{subtitle}</p>}
          </div>
          {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

/* ------------------------------ Pills ------------------------------ */

const RUN_STATUS_STYLE: Record<RunStatus, string> = {
  created: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  running: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300",
  completed: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  failed: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  cancelled: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  awaiting_review: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
};

export function RunStatusPill({ status, className }: { status: RunStatus; className?: string }) {
  return (
    <span className={clsx("pill", RUN_STATUS_STYLE[status] ?? RUN_STATUS_STYLE.created, className)}>
      {(status === "running" || status === "awaiting_review") && (
        <span className="relative flex h-1.5 w-1.5">
          <span className={clsx("absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping", status === "running" ? "bg-indigo-400" : "bg-amber-400")} />
          <span className={clsx("relative inline-flex h-1.5 w-1.5 rounded-full", status === "running" ? "bg-indigo-500" : "bg-amber-500")} />
        </span>
      )}
      {status.replace("_", " ")}
    </span>
  );
}

export const FINDING_STATUS_STYLE: Record<FindingStatus, string> = {
  candidate: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  verified: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  fixed: "bg-emerald-500/20 text-emerald-700 dark:text-emerald-200",
  pr_opened: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  refuted: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  regressed: "bg-rose-500/20 text-rose-700 dark:text-rose-200",
  approved: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  rejected: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
};

export const FINDING_STATUS_COLOR: Record<string, string> = {
  candidate: "#f59e0b",
  verified: "#10b981",
  fixed: "#34d399",
  pr_opened: "#38bdf8",
  refuted: "#f43f5e",
  regressed: "#fb7185",
  approved: "#10b981",
  rejected: "#94a3b8",
};

export function FindingStatusPill({ status, className }: { status: FindingStatus; className?: string }) {
  return <span className={clsx("pill", FINDING_STATUS_STYLE[status] ?? FINDING_STATUS_STYLE.candidate, className)}>{status.replace("_", " ")}</span>;
}

const SEVERITY_STYLE: Record<Severity, string> = {
  critical: "bg-rose-600/20 text-rose-700 dark:text-rose-200 ring-1 ring-rose-500/40",
  high: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  medium: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  low: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  info: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
};

export function SeverityPill({ severity, className }: { severity: Severity; className?: string }) {
  return <span className={clsx("pill", SEVERITY_STYLE[severity] ?? SEVERITY_STYLE.info, className)}>{severity}</span>;
}

const LANG_STYLE: Record<string, string> = {
  python: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-300",
  typescript: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  javascript: "bg-yellow-400/15 text-yellow-700 dark:text-yellow-200",
  go: "bg-cyan-500/15 text-cyan-700 dark:text-cyan-300",
  rust: "bg-orange-500/15 text-orange-700 dark:text-orange-300",
  java: "bg-red-500/15 text-red-700 dark:text-red-300",
};

export function LanguageBadge({ language }: { language: string | null }) {
  if (!language) return <span className="pill bg-slate-500/10 text-slate-500">unknown</span>;
  return <span className={clsx("pill", LANG_STYLE[language.toLowerCase()] ?? "bg-slate-500/15 text-slate-600 dark:text-slate-300")}>{language}</span>;
}

export function CategoryChip({ category }: { category: string }) {
  return <span className="inline-flex items-center rounded-md bg-violet-500/10 text-violet-700 dark:text-violet-300 px-2 py-0.5 text-xs font-medium whitespace-nowrap">{titleCase(category)}</span>;
}

export const NODE_STATUS_COLOR: Record<NodeStatus, string> = {
  pending: "#64748b",
  running: "#818cf8",
  done: "#10b981",
  skipped: "#94a3b8",
  error: "#f43f5e",
};

/* ------------------------------ Bars ------------------------------ */

export function ProgressBar({ value, className, color = "indigo", height = "h-1.5", animated = true }: { value: number; className?: string; color?: "indigo" | "emerald" | "amber" | "rose" | "sky"; height?: string; animated?: boolean }) {
  const pct = Math.max(0, Math.min(100, value * 100));
  const colors = {
    indigo: "from-indigo-500 to-violet-500",
    emerald: "from-emerald-500 to-teal-400",
    amber: "from-amber-500 to-orange-400",
    rose: "from-rose-500 to-pink-500",
    sky: "from-sky-500 to-cyan-400",
  };
  return (
    <div className={clsx("w-full rounded-full bg-slate-200/80 dark:bg-white/[0.07] overflow-hidden", height, className)} role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
      <motion.div
        className={clsx("h-full rounded-full bg-gradient-to-r", colors[color])}
        initial={animated ? { width: 0 } : false}
        animate={{ width: `${pct}%` }}
        transition={{ type: "spring", stiffness: 80, damping: 20 }}
      />
    </div>
  );
}

export function ConfidenceBar({ value }: { value: number }) {
  const color = value >= 0.75 ? "emerald" : value >= 0.5 ? "amber" : "rose";
  return (
    <div className="flex items-center gap-2 min-w-[110px]">
      <ProgressBar value={value} color={color} className="flex-1" />
      <span className="text-xs tabular-nums muted w-9 text-right">{Math.round(value * 100)}%</span>
    </div>
  );
}

/* ------------------------------ Skeleton ------------------------------ */

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} aria-hidden="true" />;
}

export function TableSkeleton({ rows = 5, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2 p-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-3">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className={clsx("h-6", c === 0 ? "w-2/5" : "flex-1")} />
          ))}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------ Empty / Error ------------------------------ */

export function EmptyState({ title, description, icon, action }: { title: string; description?: string; icon?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-4">
      <div className="h-12 w-12 rounded-2xl bg-indigo-500/10 text-indigo-500 flex items-center justify-center mb-3">{icon ?? <Inbox className="h-6 w-6" />}</div>
      <p className="text-sm font-medium">{title}</p>
      {description && <p className="text-xs muted mt-1 max-w-sm">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-center justify-between gap-3 rounded-xl border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-700 dark:text-rose-200">
      <span className="truncate">{message}</span>
      {onRetry && (
        <button className="btn-outline !py-1 !px-2 text-xs" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

/* ------------------------------ Toggle ------------------------------ */

export function Toggle({ checked, onChange, label, description, id }: { checked: boolean; onChange: (v: boolean) => void; label: string; description?: string; id?: string }) {
  const autoId = useId();
  const tid = id ?? autoId;
  return (
    <label htmlFor={tid} className="flex items-center justify-between gap-3 cursor-pointer select-none">
      <span>
        <span className="block text-sm font-medium">{label}</span>
        {description && <span className="block text-xs muted">{description}</span>}
      </span>
      <button
        id={tid}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={clsx("relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors", checked ? "bg-indigo-500" : "bg-slate-300 dark:bg-white/15")}
      >
        <motion.span layout transition={spring} className={clsx("inline-block h-5 w-5 rounded-full bg-white shadow", checked ? "ml-[22px]" : "ml-0.5")} />
      </button>
    </label>
  );
}

/* ------------------------------ Copy button ------------------------------ */

export function CopyButton({ text, className, label = "Copy" }: { text: string; className?: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable */
    }
  }, [text]);
  return (
    <button type="button" onClick={copy} className={clsx("btn-outline !py-1 !px-2 text-xs", className)} aria-label={copied ? "Copied" : label}>
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
      {copied ? "Copied" : label}
    </button>
  );
}

/* ------------------------------ Modal ------------------------------ */

export function Modal({ open, onClose, title, children, footer, size = "md" }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; size?: "md" | "lg" }) {
  const reduce = useReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    const prev = document.activeElement as HTMLElement | null;
    setTimeout(() => ref.current?.querySelector<HTMLElement>("input,select,button,textarea")?.focus(), 30);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      prev?.focus?.();
    };
  }, [open, onClose]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-3 sm:p-6" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="absolute inset-0 bg-slate-950/60 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
          <motion.div
            ref={ref}
            role="dialog"
            aria-modal="true"
            aria-label={typeof title === "string" ? title : undefined}
            className={clsx("relative glass-strong rounded-2xl w-full max-h-[92vh] overflow-y-auto", size === "lg" ? "max-w-3xl" : "max-w-lg")}
            initial={reduce ? false : { y: 24, scale: 0.98, opacity: 0 }}
            animate={{ y: 0, scale: 1, opacity: 1 }}
            exit={{ y: 16, scale: 0.98, opacity: 0 }}
            transition={spring}
          >
            <header className="flex items-center justify-between px-5 pt-5 pb-3">
              <h2 className="text-base font-semibold tracking-tight">{title}</h2>
              <button className="btn-icon btn-ghost" onClick={onClose} aria-label="Close dialog">
                <X className="h-4 w-4" />
              </button>
            </header>
            <div className="px-5 pb-5">{children}</div>
            {footer && <footer className="px-5 py-4 border-t border-slate-200/80 dark:border-white/[0.06] flex items-center justify-end gap-2">{footer}</footer>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/* ------------------------------ Drawer ------------------------------ */

export function Drawer({ open, onClose, title, children, width = "max-w-2xl" }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: string }) {
  const reduce = useReducedMotion();
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="absolute inset-0 bg-slate-950/50 backdrop-blur-[2px]" onClick={onClose} aria-hidden="true" />
          <motion.aside
            role="dialog"
            aria-modal="true"
            aria-label={typeof title === "string" ? title : "Details"}
            className={clsx("absolute right-0 top-0 h-full w-full glass-strong rounded-l-2xl sm:border-l flex flex-col", width)}
            initial={reduce ? false : { x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 300, damping: 32 }}
          >
            <header className="flex items-center justify-between gap-3 px-4 sm:px-6 py-4 border-b border-slate-200/80 dark:border-white/[0.06]">
              <div className="min-w-0 flex-1">{title}</div>
              <button className="btn-icon btn-ghost shrink-0" onClick={onClose} aria-label="Close panel">
                <X className="h-4 w-4" />
              </button>
            </header>
            <div className="flex-1 overflow-y-auto">{children}</div>
          </motion.aside>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/* ------------------------------ Tabs ------------------------------ */

export function Tabs<T extends string>({ tabs, value, onChange, ariaLabel }: { tabs: Array<{ id: T; label: string; badge?: ReactNode }>; value: T; onChange: (v: T) => void; ariaLabel?: string }) {
  return (
    <div role="tablist" aria-label={ariaLabel} className="flex gap-1 overflow-x-auto border-b border-slate-200/80 dark:border-white/[0.06] -mb-px">
      {tabs.map((t) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.id)}
            className={clsx("relative px-3 py-2 text-sm whitespace-nowrap transition-colors rounded-t-lg", active ? "text-indigo-600 dark:text-indigo-300" : "muted hover:text-slate-900 dark:hover:text-slate-100")}
          >
            <span className="inline-flex items-center gap-1.5">
              {t.label}
              {t.badge}
            </span>
            {active && <motion.span layoutId="tab-underline" className="absolute left-2 right-2 -bottom-px h-0.5 rounded-full bg-indigo-500" transition={spring} />}
          </button>
        );
      })}
    </div>
  );
}

/* ------------------------------ Misc ------------------------------ */

export function Dot({ color, pulse }: { color: string; pulse?: boolean }) {
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      {pulse && <span className="absolute inline-flex h-full w-full rounded-full animate-ping opacity-60" style={{ background: color }} />}
      <span className="relative inline-flex h-2.5 w-2.5 rounded-full" style={{ background: color }} />
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span className={clsx("inline-block h-4 w-4 rounded-full border-2 border-current border-r-transparent animate-spin", className)} role="status" aria-label="Loading" />
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}
