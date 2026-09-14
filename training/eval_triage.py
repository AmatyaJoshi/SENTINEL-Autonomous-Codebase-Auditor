"""Evaluate triage models on the held-out test split (SPEC §7.4 / §7.5).

Reports precision/recall/F1 at 0.5, AUROC, Brier score and a 10-bin calibration table for:
  (a) hunter-confidence-only baseline
  (b) heuristic priors (shipped default)
  (c) logistic-regression baseline (training/train_triage_baseline.py)
  (d) LoRA model via a LiteLLM/vLLM endpoint (--lora-endpoint), if provided
  (e) frontier LLM zero-shot judge (--llm-judge), if API keys are configured
plus per-category F1 (honest failure analysis) and the cost-impact estimate: sandbox runs avoided at
the configured threshold. Dependency-free (no sklearn) so it runs in CI.

    uv run python training/eval_triage.py --data training/data --out training/eval.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentinel.graph.state import Finding  # noqa: E402
from sentinel.triage.model import HeuristicTriage, LinearTriage  # noqa: E402
from training import mlflow_log  # noqa: E402


def auroc(scores: list[float], labels: list[int]) -> float | None:
    pos = [s for s, y in zip(scores, labels, strict=True) if y == 1]
    neg = [s for s, y in zip(scores, labels, strict=True) if y == 0]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def metrics(scores: list[float], labels: list[int], threshold: float = 0.5) -> dict[str, Any]:
    tp = sum(1 for s, y in zip(scores, labels, strict=True) if s >= threshold and y == 1)
    fp = sum(1 for s, y in zip(scores, labels, strict=True) if s >= threshold and y == 0)
    fn = sum(1 for s, y in zip(scores, labels, strict=True) if s < threshold and y == 1)
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    brier = (
        sum((s - y) ** 2 for s, y in zip(scores, labels, strict=True)) / len(scores)
        if scores
        else None
    )
    bins: list[dict[str, Any]] = []
    for b in range(10):
        lo, hi = b / 10, (b + 1) / 10
        idx = [i for i, s in enumerate(scores) if lo <= s < hi or (b == 9 and s == 1.0)]
        if idx:
            bins.append(
                {
                    "bin": f"{lo:.1f}-{hi:.1f}",
                    "n": len(idx),
                    "mean_pred": round(sum(scores[i] for i in idx) / len(idx), 3),
                    "frac_pos": round(sum(labels[i] for i in idx) / len(idx), 3),
                }
            )
    return {
        "n": len(scores),
        "precision": p,
        "recall": r,
        "f1": f1,
        "auroc": auroc(scores, labels),
        "brier": brier,
        "calibration": bins,
    }


def _finding(r: dict[str, Any]) -> Finding:
    # reconstruct enough of the finding for the model interfaces from the serialized text + features
    x = r["features"]
    sev = "critical" if x[6] else "high" if x[7] else "medium" if x[8] else "low"
    text = r["text"]
    desc = next(
        (ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("description:")),
        "n/a " * 4,
    )
    hyp = next(
        (ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("hypothesis:")),
        "n/a " * 4,
    )
    return Finding(
        id=r["id"],
        category=r["category"],
        severity=sev,
        file="f",
        line_start=1,
        line_end=1,
        description=desc or "n/a n/a n/a n/a",
        hypothesis=hyp or "n/a n/a n/a n/a",
        confidence=x[1],
        hunter_confidence=x[1],
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="training/data")
    ap.add_argument("--out", default="training/eval.json")
    ap.add_argument("--logreg", default=".sentinel/models/triage_logreg.json")
    ap.add_argument("--threshold", type=float, default=0.35)
    ap.add_argument("--lora-endpoint", default=None)
    ap.add_argument("--llm-judge", action="store_true")
    a = ap.parse_args(argv)
    rows = [
        json.loads(ln)
        for ln in (Path(a.data) / "test.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    if not rows:
        print("empty test split", file=sys.stderr)
        return 2
    labels = [int(r["label"]) for r in rows]
    fs = [_finding(r) for r in rows]
    arms: dict[str, list[float]] = {"hunter_confidence": [r["features"][1] for r in rows]}
    heur = HeuristicTriage()
    arms["heuristic"] = [
        heur.predict(
            f, int(round(r["features"][2] * 5)), int(round(math.expm1(r["features"][3] * 4)))
        )
        for f, r in zip(fs, rows, strict=True)
    ]
    lp = Path(a.logreg)
    if lp.exists():
        lin = LinearTriage.load(lp)
        arms["logreg"] = [
            1
            / (
                1
                + math.exp(
                    -sum(w * v for w, v in zip(lin.weights, r["features"], strict=True))
                    / lin.temperature
                )
            )
            for r in rows
        ]
    if a.lora_endpoint:
        import litellm

        preds = []
        for r in rows:
            try:
                resp = litellm.completion(
                    model="openai/sentinel-triage",
                    api_base=a.lora_endpoint,
                    api_key="none",
                    messages=[{"role": "user", "content": r["text"]}],
                    temperature=0,
                    max_tokens=8,
                )
                preds.append(float(json.loads(resp.choices[0].message.content)["probability"]))
            except Exception:  # noqa: BLE001
                preds.append(0.5)
        arms["lora"] = preds
    if a.llm_judge:
        from sentinel.config import get_settings
        from sentinel.llm.router import LLMRouter
        from sentinel.llm.schemas import TriageOutput

        router = LLMRouter(get_settings())
        preds = []
        for r in rows:
            try:
                out, _ = router.structured(
                    TriageOutput,
                    "Will this candidate bug survive verification by a failing unit test? "
                    "Answer as JSON {will_verify, probability, reason}.\n\n" + r["text"],
                    tier="strong",
                    prompt_name="triage_judge",
                )
                preds.append(out.probability)
            except Exception:  # noqa: BLE001
                preds.append(0.5)
        arms["llm_judge"] = preds

    result: dict[str, Any] = {
        "n_test": len(rows),
        "positives": sum(labels),
        "threshold": a.threshold,
        "arms": {},
        "per_category": {},
        "cost_impact": {},
    }
    for name, scores in arms.items():
        result["arms"][name] = metrics(scores, labels)
        avoided = sum(1 for s in scores if s < a.threshold)
        missed = sum(1 for s, y in zip(scores, labels, strict=True) if s < a.threshold and y == 1)
        result["cost_impact"][name] = {
            "sandbox_runs_avoided": avoided,
            "avoided_frac": round(avoided / len(rows), 3),
            "true_bugs_dropped": missed,
        }
    by_cat: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_cat[r["category"]].append(i)
    for cat, idx in sorted(by_cat.items()):
        result["per_category"][cat] = {
            name: metrics([scores[i] for i in idx], [labels[i] for i in idx])
            | {"calibration": None}
            for name, scores in arms.items()
        }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    with mlflow_log.run("triage-eval", params={"n_test": len(rows), "threshold": a.threshold}):
        for name, m in result["arms"].items():
            mlflow_log.log_metrics(
                {k: v for k, v in m.items() if k != "calibration"}, prefix=f"{name}."
            )
        mlflow_log.log_artifact(a.out)
    print(f"{'arm':18s} {'AUROC':>7s} {'F1':>7s} {'Brier':>7s} avoided")
    for name, m in result["arms"].items():
        ci = result["cost_impact"][name]
        print(
            f"{name:18s} {m['auroc'] if m['auroc'] is None else round(m['auroc'], 3)!s:>7} "
            f"{m['f1'] if m['f1'] is None else round(m['f1'], 3)!s:>7} {round(m['brier'], 3)!s:>7} {ci['avoided_frac']:.0%}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
