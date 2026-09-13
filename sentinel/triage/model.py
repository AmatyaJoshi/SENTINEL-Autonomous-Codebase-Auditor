"""Triage classifier interface (SPEC §7). Predicts P(candidate survives verification).

Backends, in preference order:
1. `LoRATriage`   — the fine-tuned Qwen2.5-Coder LoRA served through LiteLLM as `sentinel-triage`
                     (or any local HTTP endpoint that returns {"probability": float}).
2. `LinearTriage`  — a logistic-regression model trained by `training/train_triage_baseline.py`
                     on the same features, stored as JSON weights (CPU, dependency-free).
3. `HeuristicTriage` — calibrated hand-set priors per category × signals. Always available.

All three implement `predict(finding, analyzer_hits, callers) -> float in [0, 1]`.
"""

from __future__ import annotations

import contextlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sentinel.config import Settings
from sentinel.graph.state import CATEGORIES, Finding

FEATURE_NAMES: tuple[str, ...] = (
    "bias",
    "hunter_confidence",
    "analyzer_hits",
    "log_callers",
    "has_concrete_input",
    "desc_len",
    "severity_critical",
    "severity_high",
    "severity_medium",
    *[f"cat_{c}" for c in CATEGORIES],
)

_CONCRETE_MARKERS = (
    "=",
    "(",
    "[",
    '"',
    "'",
    "none",
    "null",
    "undefined",
    "empty",
    "0",
    "-1",
    "negative",
)


def featurize(f: Finding, analyzer_hits: int, callers: int) -> list[float]:
    hyp = f.hypothesis.lower()
    concrete = (
        1.0
        if any(m in hyp for m in _CONCRETE_MARKERS)
        and any(ch.isdigit() or ch in "\"'([" for ch in hyp)
        else 0.0
    )
    feats = [
        1.0,
        float(f.hunter_confidence if f.hunter_confidence is not None else f.confidence),
        min(float(analyzer_hits), 5.0) / 5.0,
        math.log1p(callers) / 4.0,
        concrete,
        min(len(f.description), 600) / 600.0,
        1.0 if f.severity == "critical" else 0.0,
        1.0 if f.severity == "high" else 0.0,
        1.0 if f.severity == "medium" else 0.0,
    ]
    feats += [1.0 if f.category == c else 0.0 for c in CATEGORIES]
    return feats


def serialize_for_llm(f: Finding, analyzer_hits: list[str], callers: int, code_excerpt: str) -> str:
    """Text input for the LoRA classifier and the zero-shot judge baseline (SPEC §7.1)."""
    return (
        f"category: {f.category}\nseverity: {f.severity}\nlocation: {f.file}:{f.line_start}-{f.line_end}\n"
        f"symbol: {f.symbol or '-'}\nhunter_confidence: {f.hunter_confidence or f.confidence:.2f}\n"
        f"callers: {callers}\nanalyzer_hits:\n"
        + ("\n".join(f"  - {h}" for h in analyzer_hits) or "  none")
        + f"\ndescription: {f.description}\nhypothesis: {f.hypothesis}\ncode:\n{code_excerpt}\n"
    )


class TriageModel(Protocol):
    name: str

    def predict(self, f: Finding, analyzer_hits: int, callers: int) -> float: ...


@dataclass
class HeuristicTriage:
    """Priors from the design rationale in SPEC §7.5 (race conditions hardest to verify)."""

    name: str = "heuristic-v1"

    CATEGORY_PRIOR = {  # noqa: RUF012 - constant table
        "off_by_one": 0.62,
        "null_deref": 0.58,
        "type_error": 0.55,
        "logic_error": 0.52,
        "unhandled_exception": 0.50,
        "api_misuse": 0.45,
        "resource_leak": 0.40,
        "security_smell": 0.38,
        "dead_code": 0.30,
        "perf": 0.22,
        "race_condition": 0.18,
    }

    def predict(self, f: Finding, analyzer_hits: int, callers: int) -> float:
        feats = featurize(f, analyzer_hits, callers)
        base = self.CATEGORY_PRIOR.get(f.category, 0.4)
        logit = math.log(base / (1 - base))
        logit += 1.8 * (feats[1] - 0.5)  # hunter confidence
        logit += 0.9 * feats[2]  # analyzer corroboration
        logit += 0.5 * feats[4]  # concrete triggering input
        logit += 0.3 * feats[3]  # callers
        return 1.0 / (1.0 + math.exp(-logit))


@dataclass
class LinearTriage:
    weights: list[float]
    temperature: float = 1.0
    name: str = "logreg-v1"

    def predict(self, f: Finding, analyzer_hits: int, callers: int) -> float:
        x = featurize(f, analyzer_hits, callers)
        z = sum(w * v for w, v in zip(self.weights, x, strict=True)) / max(self.temperature, 1e-6)
        return 1.0 / (1.0 + math.exp(-z))

    @classmethod
    def load(cls, path: Path) -> LinearTriage:
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("features") != list(FEATURE_NAMES):
            raise ValueError("triage model feature set mismatch; retrain")
        return cls(
            weights=[float(w) for w in d["weights"]],
            temperature=float(d.get("temperature", 1.0)),
            name=d.get("name", "logreg"),
        )


@dataclass
class LoRATriage:
    """Calls the fine-tuned model registered in the LiteLLM router as `sentinel-triage`."""

    settings: Settings
    fallback: TriageModel
    name: str = "qwen2.5-coder-lora"

    def predict(self, f: Finding, analyzer_hits: int, callers: int) -> float:
        try:
            import litellm

            resp = litellm.completion(
                model=self.settings.triage_model,
                temperature=0,
                max_tokens=8,
                messages=[{"role": "user", "content": serialize_for_llm(f, [], callers, "")}],
                api_base=self.settings.triage_endpoint,
            )
            text = (resp.choices[0].message.content or "").strip()
            p = float(json.loads(text)["probability"]) if text.startswith("{") else float(text)
            return min(1.0, max(0.0, p))
        except Exception:  # noqa: BLE001 - fall back rather than block the pipeline
            return self.fallback.predict(f, analyzer_hits, callers)


def load_triage_model(settings: Settings) -> TriageModel:
    heuristic = HeuristicTriage()
    linear_path = settings.work_dir / "models" / "triage_logreg.json"
    if not linear_path.exists():
        linear_path = Path(__file__).parent / "triage_logreg.json"
    model: TriageModel = heuristic
    if linear_path.exists():
        with contextlib.suppress(ValueError):
            model = LinearTriage.load(linear_path)
    if settings.triage_endpoint:
        return LoRATriage(settings, fallback=model)
    return model
