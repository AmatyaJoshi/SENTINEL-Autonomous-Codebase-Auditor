"""Optional MLflow logging (item 18). No-op unless `mlflow` is installed and MLFLOW_TRACKING_URI or
--mlflow is set. Used by train_triage_baseline.py, train_triage_lora.py and eval_triage.py.

    uv sync --extra training            # installs mlflow
    MLFLOW_TRACKING_URI=http://mlflow:5000 uv run python training/eval_triage.py
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def enabled() -> bool:
    if not os.environ.get("MLFLOW_TRACKING_URI") and not os.environ.get("SENTINEL_MLFLOW"):
        return False
    try:
        import mlflow  # noqa: F401
    except ImportError:
        return False
    return True


@contextmanager
def run(
    name: str, params: dict[str, Any] | None = None, tags: dict[str, str] | None = None
) -> Iterator[Any]:
    if not enabled():
        yield None
        return
    import mlflow

    mlflow.set_experiment(os.environ.get("SENTINEL_MLFLOW_EXPERIMENT", "sentinel-triage"))
    with mlflow.start_run(run_name=name) as active:
        if params:
            mlflow.log_params({k: _flat(v) for k, v in params.items()})
        if tags:
            mlflow.set_tags(tags)
        yield active


def log_metrics(
    metrics: dict[str, float | None], step: int | None = None, prefix: str = ""
) -> None:
    if not enabled():
        return
    import mlflow

    clean = {f"{prefix}{k}": float(v) for k, v in metrics.items() if isinstance(v, int | float)}
    if clean:
        mlflow.log_metrics(clean, step=step)


def log_artifact(path: Path | str) -> None:
    if enabled() and Path(path).exists():
        import mlflow

        mlflow.log_artifact(str(path))


def _flat(v: Any) -> str:
    s = str(v)
    return s if len(s) <= 250 else s[:247] + "..."
