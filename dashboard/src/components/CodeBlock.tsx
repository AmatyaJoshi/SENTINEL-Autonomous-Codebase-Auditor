import clsx from "clsx";
import { CopyButton } from "./ui";

export function CodeBlock({ code, title, className, maxHeight = "max-h-[420px]", lineNumbers = true }: { code: string; title?: string; className?: string; maxHeight?: string; lineNumbers?: boolean }) {
  const lines = code.replace(/\n$/, "").split("\n");
  return (
    <div className={clsx("rounded-xl border border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-black/30 overflow-hidden", className)}>
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-slate-200 dark:border-white/10 bg-white/50 dark:bg-white/[0.03]">
        <span className="text-xs font-mono muted truncate">{title ?? "code"}</span>
        <CopyButton text={code} />
      </div>
      <pre className={clsx("code overflow-auto p-3", maxHeight)} tabIndex={0}>
        {lines.map((l, i) => (
          <div key={i} className="flex">
            {lineNumbers && <span className="select-none w-8 shrink-0 text-right pr-3 text-slate-400 dark:text-slate-600">{i + 1}</span>}
            <span className="whitespace-pre">{l || " "}</span>
          </div>
        ))}
      </pre>
    </div>
  );
}

export function LogBlock({ log, title, className }: { log: string; title?: string; className?: string }) {
  const lines = log.replace(/\n$/, "").split("\n");
  return (
    <div className={clsx("rounded-xl border border-slate-200 dark:border-white/10 bg-slate-950 text-slate-200 overflow-hidden", className)}>
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10 bg-white/[0.03]">
        <span className="text-xs font-mono text-slate-400 truncate">{title ?? "log"}</span>
        <CopyButton text={log} className="!text-slate-300 !border-white/15" />
      </div>
      <pre className="code overflow-auto p-3 max-h-[420px]" tabIndex={0}>
        {lines.map((l, i) => {
          const cls = /FAIL|Error|failed|exit_code=[1-9]|AssertionError/.test(l)
            ? "text-rose-300"
            : /passed|PASS|exit_code=0|ok/.test(l)
              ? "text-emerald-300"
              : l.startsWith("$")
                ? "text-sky-300"
                : l.startsWith(">") || l.startsWith("E ")
                  ? "text-amber-200"
                  : "";
          return (
            <div key={i} className={clsx("whitespace-pre", cls)}>
              {l || " "}
            </div>
          );
        })}
      </pre>
    </div>
  );
}
