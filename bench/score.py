"""Scoring (SPEC §8.3): lenient (file + ±5 lines) and strict (+ category) precision/recall/F1,
verified rate, patch pass rate, cost/time, per-category confusion."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bench.inject.injector import Injected

LINE_SLACK = 5
REPORTED_STATUSES = {
    "verified",
    "fixed",
    "pr_opened",
    "regressed",
}  # what Sentinel *claims* as a bug


@dataclass
class Reported:
    """A finding as reported by an arm (normalised across arms)."""

    file: str
    line_start: int
    line_end: int
    category: str | None
    status: str = "verified"
    fixed: bool = False


@dataclass
class ArmScore:
    arm: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tp_strict: int = 0
    fp_strict: int = 0
    candidates: int = 0
    verified: int = 0
    fixed: int = 0
    cost_usd: float = 0.0
    wall_clock_s: float = 0.0
    repos: int = 0
    per_category: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    )
    confusion: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )

    @staticmethod
    def _prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
        p = tp / (tp + fp) if tp + fp else None
        r = tp / (tp + fn) if tp + fn else None
        f1 = (
            (2 * p * r / (p + r))
            if p is not None and r is not None and (p + r)
            else (0.0 if p is not None and r is not None else None)
        )
        return p, r, f1

    def to_dict(self) -> dict[str, Any]:
        p, r, f1 = self._prf(self.tp, self.fp, self.fn)
        ps, rs, f1s = self._prf(
            self.tp_strict, self.fp_strict, self.fn + (self.tp - self.tp_strict)
        )
        return {
            "arm": self.arm,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": _r(p),
            "recall": _r(r),
            "f1": _r(f1),
            "precision_strict": _r(ps),
            "recall_strict": _r(rs),
            "f1_strict": _r(f1s),
            "candidates": self.candidates,
            "verified": self.verified,
            "fixed": self.fixed,
            "verified_rate": _r(self.verified / self.candidates) if self.candidates else None,
            "patch_pass_rate": _r(self.fixed / self.verified) if self.verified else None,
            "cost_usd": round(self.cost_usd, 4),
            "wall_clock_s": round(self.wall_clock_s, 1),
            "cost_per_repo": _r(self.cost_usd / self.repos) if self.repos else None,
            "minutes_per_repo": _r(self.wall_clock_s / 60 / self.repos) if self.repos else None,
            "cost_per_verified_bug": _r(self.cost_usd / self.verified) if self.verified else None,
            "per_category": {k: dict(v) for k, v in self.per_category.items()},
            "confusion": {k: dict(v) for k, v in self.confusion.items()},
        }


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def _matches(rep: Reported, inj: Injected) -> bool:
    if rep.file.replace("\\", "/") != inj.file.replace("\\", "/"):
        return False
    return (
        rep.line_start <= inj.line_end + LINE_SLACK and inj.line_start <= rep.line_end + LINE_SLACK
    )


def score_instance(
    score: ArmScore,
    injected: Injected,
    reported: list[Reported],
    *,
    candidates: int,
    verified: int,
    fixed: int,
    cost_usd: float,
    wall_clock_s: float,
) -> dict[str, Any]:
    """Score one mutated repo (exactly one injected bug) for one arm; accumulates into `score`."""
    score.repos += 1
    score.candidates += candidates
    score.verified += verified
    score.fixed += fixed
    score.cost_usd += cost_usd
    score.wall_clock_s += wall_clock_s
    hits = [r for r in reported if _matches(r, injected)]
    found = bool(hits)
    strict = any(r.category == injected.category for r in hits)
    fps = [r for r in reported if not _matches(r, injected)]
    if found:
        score.tp += 1
        score.per_category[injected.category]["tp"] += 1
        if strict:
            score.tp_strict += 1
        for r in hits:
            score.confusion[injected.category][r.category or "none"] += 1
    else:
        score.fn += 1
        score.per_category[injected.category]["fn"] += 1
    score.fp += len(fps)
    score.fp_strict += len(fps) + (len(hits) if found and not strict else 0)
    for r in fps:
        score.per_category[r.category or "none"]["fp"] += 1
    return {
        "instance": injected.id,
        "category": injected.category,
        "found": found,
        "strict": strict,
        "false_positives": len(fps),
        "reported": len(reported),
        "cost_usd": round(cost_usd, 4),
    }


def from_findings(
    findings: list[dict[str, Any]], *, statuses: set[str] | None = None
) -> list[Reported]:
    statuses = statuses or REPORTED_STATUSES
    return [
        Reported(
            file=f["file"],
            line_start=int(f["line_start"]),
            line_end=int(f["line_end"]),
            category=f.get("category"),
            status=f.get("status", "verified"),
            fixed=f.get("status") in ("fixed", "pr_opened"),
        )
        for f in findings
        if f.get("status", "verified") in statuses
    ]
