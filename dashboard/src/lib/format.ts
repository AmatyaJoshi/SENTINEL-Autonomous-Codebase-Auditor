/** Formatting helpers: relative time, USD, percent, durations. */

const rtf =
  typeof Intl !== "undefined" && "RelativeTimeFormat" in Intl
    ? new Intl.RelativeTimeFormat("en", { numeric: "auto" })
    : null;

export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "-";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "-";
  const diff = (t - now) / 1000;
  const abs = Math.abs(diff);
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 60 * 60 * 24 * 365],
    ["month", 60 * 60 * 24 * 30],
    ["week", 60 * 60 * 24 * 7],
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
  ];
  for (const [unit, secs] of units) {
    if (abs >= secs) {
      const v = Math.round(diff / secs);
      return rtf
        ? rtf.format(v, unit)
        : `${Math.abs(v)} ${unit}${Math.abs(v) === 1 ? "" : "s"} ${v < 0 ? "ago" : "from now"}`;
    }
  }
  if (abs < 10) return "just now";
  const v = Math.round(diff);
  return rtf ? rtf.format(v, "second") : `${Math.abs(v)}s ago`;
}

export function absoluteTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function shortTime(iso: string | number | null | undefined): string {
  if (iso == null) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleTimeString(undefined, { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function usd(v: number | null | undefined, opts: { compact?: boolean; digits?: number } = {}): string {
  if (v == null || Number.isNaN(v)) return "-";
  const digits = opts.digits ?? (Math.abs(v) < 1 && v !== 0 ? 3 : 2);
  if (opts.compact && Math.abs(v) >= 1000) {
    return `$${(v / 1000).toFixed(1)}k`;
  }
  return `$${v.toFixed(digits)}`;
}

export function percent(v: number | null | undefined, digits = 0): string {
  if (v == null || Number.isNaN(v)) return "-";
  return `${(v * 100).toFixed(digits)}%`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "-";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${s.toString().padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${(m % 60).toString().padStart(2, "0")}m`;
}

export function compactNumber(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "-";
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(Math.round(v));
}

export function shortSha(sha: string | null | undefined, len = 7): string {
  if (!sha) return "-";
  return sha.slice(0, len);
}

/** "https://github.com/org/repo.git" -> "org/repo"; local paths -> last two segments. */
export function repoName(url: string): string {
  if (!url) return "-";
  const cleaned = url.replace(/\.git$/, "").replace(/[\\/]+$/, "");
  const parts = cleaned.split(/[\\/]/).filter((p) => p && !p.endsWith(":"));
  if (parts.length >= 2) return `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
  return parts[parts.length - 1] ?? url;
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function clamp(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v));
}
