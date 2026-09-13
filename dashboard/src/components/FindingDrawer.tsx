import { useState } from "react";
import clsx from "clsx";
import { Check, ExternalLink, FileCode2, GitPullRequest, X } from "lucide-react";
import type { Finding } from "@/lib/types";
import { absoluteTime, percent, usd } from "@/lib/format";
import { CodeBlock, LogBlock } from "./CodeBlock";
import { DiffView } from "./DiffView";
import { CategoryChip, ConfidenceBar, Drawer, EmptyState, FindingStatusPill, SeverityPill, Spinner, Tabs } from "./ui";

type Tab = "description" | "evidence" | "test" | "patch" | "logs" | "pr";

export function FindingDrawer({
  finding,
  onClose,
  canDecide,
  onDecide,
  deciding,
}: {
  finding: Finding | null;
  onClose: () => void;
  canDecide: boolean;
  onDecide: (f: Finding, decision: "approve" | "reject") => void;
  deciding?: string | null;
}) {
  const [tab, setTab] = useState<Tab>("description");
  const f = finding;
  const pendingDecision = f ? f.status === "fixed" || f.status === "verified" : false;

  return (
    <Drawer
      open={!!f}
      onClose={onClose}
      title={
        f && (
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <SeverityPill severity={f.severity} />
              <CategoryChip category={f.category} />
              <FindingStatusPill status={f.status} />
              <span className="text-xs muted font-mono">{f.id}</span>
            </div>
            <p className="mt-1.5 text-sm font-mono truncate flex items-center gap-1.5">
              <FileCode2 className="h-3.5 w-3.5 muted shrink-0" />
              {f.file}
              <span className="muted">
                :{f.line_start}
                {f.line_end !== f.line_start ? `-${f.line_end}` : ""}
              </span>
              {f.symbol && <span className="muted">· {f.symbol}()</span>}
            </p>
          </div>
        )
      }
    >
      {f && (
        <div className="px-4 sm:px-6 py-4 space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Meta label="Confidence">
              <ConfidenceBar value={f.confidence} />
            </Meta>
            <Meta label="Blast radius">
              <span className="tabular-nums">{f.blast_radius} callers</span>
            </Meta>
            <Meta label="Cost">
              <span className="tabular-nums">{usd(f.cost_usd)}</span>
            </Meta>
            <Meta label="Created">
              <span className="text-xs">{absoluteTime(f.created_at)}</span>
            </Meta>
          </div>

          {canDecide && pendingDecision && (
            <div className="rounded-xl border border-amber-400/40 bg-amber-500/10 p-3 flex flex-col sm:flex-row sm:items-center gap-3">
              <p className="text-sm flex-1 text-amber-800 dark:text-amber-200">This run is awaiting review. Approve to open a PR for this fix, or reject to drop it.</p>
              <div className="flex gap-2">
                <button className="btn-success" onClick={() => onDecide(f, "approve")} disabled={deciding === f.id}>
                  {deciding === f.id ? <Spinner /> : <Check className="h-4 w-4" />} Approve
                </button>
                <button className="btn-danger" onClick={() => onDecide(f, "reject")} disabled={deciding === f.id}>
                  <X className="h-4 w-4" /> Reject
                </button>
              </div>
            </div>
          )}

          <Tabs<Tab>
            ariaLabel="Finding details"
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "description", label: "Description" },
              { id: "evidence", label: "Evidence", badge: <span className="text-[10px] muted">{f.evidence.length}</span> },
              { id: "test", label: "Test" },
              { id: "patch", label: "Patch" },
              { id: "logs", label: "Logs" },
              { id: "pr", label: "PR", badge: f.pr_url ? <span className="h-1.5 w-1.5 rounded-full bg-sky-500" /> : undefined },
            ]}
          />

          <div className="pt-1">
            {tab === "description" && (
              <div className="space-y-4">
                <Section title="Description">
                  <p className="text-sm leading-relaxed">{f.description}</p>
                </Section>
                <Section title="Hypothesis">
                  <p className="text-sm leading-relaxed muted">{f.hypothesis}</p>
                </Section>
                {f.explanation && (
                  <Section title="Explanation">
                    <p className="text-sm leading-relaxed">{f.explanation}</p>
                  </Section>
                )}
              </div>
            )}
            {tab === "evidence" &&
              (f.evidence.length ? (
                <ul className="space-y-1.5">
                  {f.evidence.map((e, i) => (
                    <li key={i} className="code rounded-lg bg-slate-100 dark:bg-white/[0.04] px-3 py-2 break-words">
                      {e}
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="No analyzer evidence" description="This finding came from LLM hunting alone." />
              ))}
            {tab === "test" && (f.test_code ? <CodeBlock code={f.test_code} title={f.test_path ?? "test"} /> : <EmptyState title="No test yet" description="A failing test is generated during the verify stage." />)}
            {tab === "patch" && (f.patch_diff ? <DiffView diff={f.patch_diff} /> : <EmptyState title="No patch yet" description="Patches are produced during the fix stage for verified findings." />)}
            {tab === "logs" && (
              <div className="space-y-3">
                {f.verify_log ? <LogBlock log={f.verify_log} title="verify" /> : <EmptyState title="No verify log" />}
                {f.regress_log && <LogBlock log={f.regress_log} title="regress" />}
              </div>
            )}
            {tab === "pr" &&
              (f.pr_url ? (
                <a href={f.pr_url} target="_blank" rel="noreferrer" className={clsx("btn-outline w-full justify-between")}>
                  <span className="inline-flex items-center gap-2">
                    <GitPullRequest className="h-4 w-4 text-sky-500" />
                    <span className="truncate">{f.pr_url}</span>
                  </span>
                  <ExternalLink className="h-4 w-4 shrink-0" />
                </a>
              ) : (
                <EmptyState title="No pull request" description={f.status === "fixed" ? "PR opening is disabled or pending review." : "PRs are opened only for fixed findings."} />
              ))}
          </div>
          <p className="text-[11px] muted">Confidence {percent(f.confidence)} · status {f.status}</p>
        </div>
      )}
    </Drawer>
  );
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-slate-100/70 dark:bg-white/[0.04] px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider muted mb-1">{label}</p>
      <div className="text-sm">{children}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wider muted mb-1.5">{title}</h3>
      {children}
    </div>
  );
}
