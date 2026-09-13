import clsx from "clsx";
import { CopyButton } from "./ui";

type LineKind = "add" | "del" | "hunk" | "meta" | "ctx";

function classify(line: string): LineKind {
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  if (line.startsWith("diff ") || line.startsWith("index ")) return "meta";
  return "ctx";
}

const STYLE: Record<LineKind, string> = {
  add: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-200",
  del: "bg-rose-500/15 text-rose-800 dark:text-rose-200",
  hunk: "bg-indigo-500/10 text-indigo-700 dark:text-indigo-300",
  meta: "text-slate-500 dark:text-slate-400 font-semibold",
  ctx: "",
};

/** Renders a unified diff with +/- colouring and old/new line gutters. */
export function DiffView({ diff, className }: { diff: string; className?: string }) {
  const lines = diff.replace(/\n$/, "").split("\n");
  let oldNo = 0;
  let newNo = 0;
  const rows = lines.map((line, i) => {
    const kind = classify(line);
    let o = "";
    let n = "";
    if (kind === "hunk") {
      const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
      if (m) {
        oldNo = Number(m[1]);
        newNo = Number(m[2]);
      }
    } else if (kind === "add") {
      n = String(newNo++);
    } else if (kind === "del") {
      o = String(oldNo++);
    } else if (kind === "ctx") {
      o = String(oldNo++);
      n = String(newNo++);
    }
    return { key: i, kind, line, o, n };
  });
  const added = rows.filter((r) => r.kind === "add").length;
  const removed = rows.filter((r) => r.kind === "del").length;

  return (
    <div className={clsx("rounded-xl border border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-black/30 overflow-hidden", className)}>
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-slate-200 dark:border-white/10 bg-white/50 dark:bg-white/[0.03]">
        <span className="text-xs font-mono muted">
          <span className="text-emerald-600 dark:text-emerald-400">+{added}</span> <span className="text-rose-600 dark:text-rose-400">-{removed}</span>
        </span>
        <CopyButton text={diff} label="Copy patch" />
      </div>
      <div className="overflow-auto max-h-[480px]" tabIndex={0}>
        <table className="code w-full border-collapse">
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className={STYLE[r.kind]}>
                <td className="select-none w-10 text-right pr-2 pl-2 text-slate-400 dark:text-slate-600 align-top">{r.o}</td>
                <td className="select-none w-10 text-right pr-3 text-slate-400 dark:text-slate-600 align-top border-r border-slate-200 dark:border-white/10">{r.n}</td>
                <td className="pl-3 pr-3 whitespace-pre align-top">{r.line || " "}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
