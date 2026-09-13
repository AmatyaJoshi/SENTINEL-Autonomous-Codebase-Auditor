import { motion, useReducedMotion } from "framer-motion";
import clsx from "clsx";
import { usd } from "@/lib/format";

/** Semi-circular cost vs budget gauge. */
export function CostGauge({ cost, budget, className }: { cost: number; budget: number; className?: string }) {
  const reduce = useReducedMotion();
  const ratio = budget > 0 ? Math.min(1, cost / budget) : 0;
  const r = 44;
  const c = Math.PI * r; // half circumference
  const color = ratio >= 0.9 ? "#f43f5e" : ratio >= 0.7 ? "#f59e0b" : "#10b981";
  return (
    <div className={clsx("flex flex-col items-center", className)} role="img" aria-label={`Cost ${usd(cost)} of ${usd(budget)} budget`}>
      <svg viewBox="0 0 112 64" className="w-full max-w-[200px]">
        <path d={`M 12 58 A ${r} ${r} 0 0 1 100 58`} fill="none" stroke="currentColor" strokeWidth="9" strokeLinecap="round" className="text-slate-200 dark:text-white/10" />
        <motion.path
          d={`M 12 58 A ${r} ${r} 0 0 1 100 58`}
          fill="none"
          stroke={color}
          strokeWidth="9"
          strokeLinecap="round"
          strokeDasharray={c}
          initial={reduce ? false : { strokeDashoffset: c }}
          animate={{ strokeDashoffset: c * (1 - ratio) }}
          transition={{ type: "spring", stiffness: 60, damping: 18 }}
        />
        <text x="56" y="50" textAnchor="middle" className="fill-current text-[15px] font-semibold tabular-nums">
          {usd(cost)}
        </text>
        <text x="56" y="62" textAnchor="middle" className="fill-slate-500 text-[8px]">
          of {usd(budget, { digits: 0 })} budget · {Math.round(ratio * 100)}%
        </text>
      </svg>
    </div>
  );
}
