import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import clsx from "clsx";
import { useToast, type ToastKind } from "@/hooks/useToast";
import { spring } from "./motion";

const ICON: Record<ToastKind, typeof Info> = { info: Info, success: CheckCircle2, warning: AlertTriangle, error: XCircle };
const STYLE: Record<ToastKind, string> = {
  info: "border-sky-400/40 text-sky-600 dark:text-sky-300",
  success: "border-emerald-400/40 text-emerald-600 dark:text-emerald-300",
  warning: "border-amber-400/40 text-amber-600 dark:text-amber-300",
  error: "border-rose-400/40 text-rose-600 dark:text-rose-300",
};

export function Toasts() {
  const { toasts, dismiss } = useToast();
  return (
    <div className="fixed bottom-4 right-4 left-4 sm:left-auto z-[60] flex flex-col gap-2 sm:w-[360px] pointer-events-none" aria-live="polite" aria-relevant="additions">
      <AnimatePresence initial={false}>
        {toasts.map((t) => {
          const Icon = ICON[t.kind];
          return (
            <motion.div
              key={t.id}
              layout
              initial={{ opacity: 0, y: 16, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, scale: 0.96 }}
              transition={spring}
              role="status"
              className={clsx("glass-strong rounded-xl border-l-4 px-3 py-2.5 flex items-start gap-3 pointer-events-auto", STYLE[t.kind])}
            >
              <Icon className="h-4.5 w-4.5 mt-0.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{t.title}</p>
                {t.description && <p className="text-xs muted mt-0.5 break-words">{t.description}</p>}
              </div>
              <button className="btn-icon btn-ghost !p-1 text-slate-500" onClick={() => dismiss(t.id)} aria-label="Dismiss notification">
                <X className="h-3.5 w-3.5" />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
