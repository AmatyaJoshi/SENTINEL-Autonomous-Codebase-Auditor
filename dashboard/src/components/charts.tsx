import type { ReactNode } from "react";
import type { Arm } from "@/lib/types";

export const PALETTE = {
  indigo: "#818cf8",
  violet: "#a78bfa",
  emerald: "#34d399",
  amber: "#fbbf24",
  rose: "#fb7185",
  sky: "#38bdf8",
  slate: "#94a3b8",
  fuchsia: "#e879f9",
};

export const ARM_COLOR: Record<Arm, string> = {
  analyzers: PALETTE.slate,
  single_shot: PALETTE.amber,
  no_triage: PALETTE.sky,
  full: PALETTE.indigo,
};

export const CATEGORY_COLORS = [PALETTE.indigo, PALETTE.violet, PALETTE.sky, PALETTE.emerald, PALETTE.amber, PALETTE.rose, PALETTE.fuchsia, PALETTE.slate];

export const axisProps = {
  tick: { fontSize: 11, fill: "currentColor", opacity: 0.6 },
  axisLine: false as const,
  tickLine: false as const,
};

export const gridProps = { strokeDasharray: "3 3", stroke: "currentColor", strokeOpacity: 0.08, vertical: false };

interface TooltipPayloadItem {
  name?: string | number;
  value?: number | string | ReadonlyArray<number | string>;
  color?: string;
  dataKey?: string | number;
}

export function ChartTooltip({ active, payload, label, format }: { active?: boolean; payload?: ReadonlyArray<TooltipPayloadItem>; label?: ReactNode; format?: (v: number | string | ReadonlyArray<number | string> | undefined, key?: string | number) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="glass-strong rounded-xl px-3 py-2 text-xs shadow-lg">
      {label !== undefined && <p className="font-medium mb-1">{String(label)}</p>}
      <ul className="space-y-0.5">
        {payload.map((p, i) => (
          <li key={i} className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
            <span className="muted">{String(p.name ?? p.dataKey ?? "")}</span>
            <span className="ml-auto tabular-nums font-medium">{format ? format(p.value, p.dataKey) : String(p.value)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
