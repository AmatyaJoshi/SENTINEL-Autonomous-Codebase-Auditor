import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Rocket } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { Arm, CreateRunRequest } from "@/lib/types";
import { ARMS } from "@/lib/types";
import { useToast } from "@/hooks/useToast";
import { Modal, Spinner, Toggle } from "./ui";

const ARM_HELP: Record<Arm, string> = {
  full: "Complete pipeline: plan, hunt, triage, verify, fix, regress, rank.",
  no_triage: "Skips LLM triage; every candidate goes to verification.",
  single_shot: "One-pass hunt with no planning/triage/ranking (baseline).",
  analyzers: "Static analyzers only, no LLM hunting (control).",
};

export function NewRunModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<CreateRunRequest>({
    repo: "",
    sha: null,
    open_pr: false,
    review: false,
    max_usd: 5,
    max_minutes: 60,
    max_findings: 25,
    arm: "full",
  });
  const [err, setErr] = useState<string | null>(null);

  const update = <K extends keyof CreateRunRequest>(k: K, v: CreateRunRequest[K]) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!form.repo.trim()) {
      setErr("Repository URL or local path is required.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const run = await api.createRun({ ...form, repo: form.repo.trim(), sha: form.sha?.trim() || null });
      toast.success("Audit started", `Run ${run.id} accepted.`);
      onClose();
      nav(`/runs/${run.id}`);
    } catch (e2) {
      setErr(errorMessage(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New audit"
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" form="new-run-form" className="btn-primary" disabled={busy}>
            {busy ? <Spinner /> : <Rocket className="h-4 w-4" />} Start audit
          </button>
        </>
      }
    >
      <form id="new-run-form" onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="nr-repo">
            Repository URL or path
          </label>
          <input id="nr-repo" className="input font-mono" placeholder="https://github.com/org/repo or /local/path" value={form.repo} onChange={(e) => update("repo", e.target.value)} autoComplete="off" spellCheck={false} required />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2 sm:col-span-1">
            <label className="label" htmlFor="nr-sha">
              Commit SHA <span className="normal-case font-normal">(optional)</span>
            </label>
            <input id="nr-sha" className="input font-mono" placeholder="HEAD" value={form.sha ?? ""} onChange={(e) => update("sha", e.target.value || null)} spellCheck={false} />
          </div>
          <div className="col-span-2 sm:col-span-1">
            <label className="label" htmlFor="nr-arm">
              Arm
            </label>
            <select id="nr-arm" className="input" value={form.arm} onChange={(e) => update("arm", e.target.value as Arm)}>
              {[...ARMS].reverse().map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <p className="text-[11px] muted mt-1">{ARM_HELP[form.arm]}</p>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="label" htmlFor="nr-usd">
              Budget USD
            </label>
            <input id="nr-usd" type="number" min={0.1} step={0.5} className="input tabular-nums" value={form.max_usd} onChange={(e) => update("max_usd", Number(e.target.value))} />
          </div>
          <div>
            <label className="label" htmlFor="nr-min">
              Minutes
            </label>
            <input id="nr-min" type="number" min={1} step={5} className="input tabular-nums" value={form.max_minutes} onChange={(e) => update("max_minutes", Number(e.target.value))} />
          </div>
          <div>
            <label className="label" htmlFor="nr-max">
              Max findings
            </label>
            <input id="nr-max" type="number" min={1} step={1} className="input tabular-nums" value={form.max_findings} onChange={(e) => update("max_findings", Number(e.target.value))} />
          </div>
        </div>
        <div className="space-y-3 rounded-xl border border-slate-200/80 dark:border-white/[0.06] p-3">
          <Toggle checked={form.open_pr} onChange={(v) => update("open_pr", v)} label="Open pull requests" description="Push fix branches and open a PR per fixed finding." />
          <Toggle checked={form.review} onChange={(v) => update("review", v)} label="Require review" description="Pause before PRs so an operator can approve or reject each fix." />
        </div>
        {err && (
          <p role="alert" className="text-sm text-rose-600 dark:text-rose-300">
            {err}
          </p>
        )}
      </form>
    </Modal>
  );
}
