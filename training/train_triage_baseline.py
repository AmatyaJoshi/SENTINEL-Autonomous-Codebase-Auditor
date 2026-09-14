"""Dependency-free logistic-regression baseline on the featurized candidates (CPU, seconds).

Produces `triage_logreg.json` loadable by `sentinel.triage.model.LinearTriage`, with a temperature
fitted on the validation split for calibration. This is baseline (a)+ in SPEC §7.4; the LoRA model in
`train_triage_lora.py` is the headline model.

    uv run python training/train_triage_baseline.py --data training/data --out .sentinel/models/triage_logreg.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentinel.triage.model import FEATURE_NAMES  # noqa: E402
from training import mlflow_log  # noqa: E402


def load(path: Path) -> tuple[list[list[float]], list[int]]:
    X, y = [], []
    if not path.exists():
        return X, y
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        X.append([float(v) for v in r["features"]])
        y.append(int(r["label"]))
    return X, y


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def train(
    X: list[list[float]], y: list[int], epochs: int = 300, lr: float = 0.05, l2: float = 1e-3
) -> list[float]:
    d = len(X[0])
    w = [0.0] * d
    pos = sum(y) or 1
    neg = len(y) - pos or 1
    w_pos, w_neg = len(y) / (2 * pos), len(y) / (2 * neg)  # class weighting (SPEC §7.3)
    for _ in range(epochs):
        grad = [0.0] * d
        for xi, yi in zip(X, y, strict=True):
            p = sigmoid(sum(wi * v for wi, v in zip(w, xi, strict=True)))
            g = (p - yi) * (w_pos if yi else w_neg)
            for j in range(d):
                grad[j] += g * xi[j]
        for j in range(d):
            w[j] -= lr * (grad[j] / len(X) + l2 * w[j])
    return w


def fit_temperature(w: list[float], X: list[list[float]], y: list[int]) -> float:
    if not X:
        return 1.0
    best_t, best_nll = 1.0, float("inf")
    for t in [0.5 + 0.1 * i for i in range(30)]:
        nll = 0.0
        for xi, yi in zip(X, y, strict=True):
            p = sigmoid(sum(wi * v for wi, v in zip(w, xi, strict=True)) / t)
            p = min(1 - 1e-6, max(1e-6, p))
            nll -= yi * math.log(p) + (1 - yi) * math.log(1 - p)
        if nll < best_nll:
            best_t, best_nll = t, nll
    return best_t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="training/data")
    ap.add_argument("--out", default=".sentinel/models/triage_logreg.json")
    ap.add_argument("--epochs", type=int, default=300)
    a = ap.parse_args(argv)
    data = Path(a.data)
    X, y = load(data / "train.jsonl")
    if len(X) < 20 or len(set(y)) < 2:
        print(
            f"not enough labelled data to train ({len(X)} rows, classes={set(y)}); run more audits first",
            file=sys.stderr,
        )
        return 2
    w = train(X, y, epochs=a.epochs)
    Xv, yv = load(data / "val.jsonl")
    t = fit_temperature(w, Xv, yv)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "name": "logreg-v1",
                "features": list(FEATURE_NAMES),
                "weights": w,
                "temperature": t,
                "train_rows": len(X),
                "positives": sum(y),
            },
            indent=2,
        )
    )
    print(f"saved {out} (rows={len(X)}, positives={sum(y)}, temperature={t:.2f})")
    with mlflow_log.run(
        "triage-logreg", params={"epochs": a.epochs, "rows": len(X), "positives": sum(y)}
    ):
        mlflow_log.log_metrics({"temperature": t})
        mlflow_log.log_artifact(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
