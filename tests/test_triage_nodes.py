"""Unit tests for triage model + dedupe, verify failure matching, plan heuristics."""

from __future__ import annotations

from pathlib import Path

from sentinel.graph.nodes.triage import dedupe
from sentinel.graph.nodes.verify import failure_matches
from sentinel.graph.state import Finding
from sentinel.sandbox.test_runner import TestCase, TestReport
from sentinel.triage.model import FEATURE_NAMES, HeuristicTriage, LinearTriage, featurize


def _f(**kw: object) -> Finding:
    base = dict(
        id="f",
        category="off_by_one",
        severity="high",
        file="a.py",
        line_start=10,
        line_end=12,
        description="loop drops last element of the list",
        hypothesis="first_n([1,2,3], 2) returns [1]",
        confidence=0.8,
    )
    base.update(kw)
    return Finding(**base)  # type: ignore[arg-type]


def test_heuristic_orders_categories_sensibly() -> None:
    m = HeuristicTriage()
    p_off = m.predict(_f(), analyzer_hits=1, callers=3)
    p_race = m.predict(
        _f(
            category="race_condition",
            hypothesis="maybe concurrent access",
            confidence=0.3,
            hunter_confidence=0.3,
        ),
        0,
        0,
    )
    assert 0 < p_race < 0.3 < 0.6 < p_off < 1


def test_featurize_and_linear(tmp_path: Path) -> None:
    x = featurize(_f(), 2, 4)
    assert len(x) == len(FEATURE_NAMES) and x[0] == 1.0 and x[4] == 1.0  # concrete input detected
    path = tmp_path / "m.json"
    import json

    path.write_text(
        json.dumps(
            {
                "features": list(FEATURE_NAMES),
                "weights": [0.0] * len(FEATURE_NAMES),
                "temperature": 1.0,
            }
        )
    )
    lin = LinearTriage.load(path)
    assert lin.predict(_f(), 0, 0) == 0.5


def test_dedupe_keeps_highest_confidence_overlap() -> None:
    a = _f(id="a", confidence=0.6)
    b = _f(id="b", line_start=11, line_end=13, confidence=0.9)
    c = _f(id="c", category="null_deref")
    kept = dedupe([a, b, c])
    assert {k.id for k in kept} == {"b", "c"}


def _rep(status: str, ftype: str = "", msg: str = "") -> TestReport:
    r = TestReport(cases=[TestCase("t", "m", status, 0.0, msg, ftype)])  # type: ignore[arg-type]
    r.total, r.failed, r.errors = 1, int(status == "failed"), int(status == "error")
    return r


def test_failure_matches_requires_hypothesised_reason() -> None:
    assert failure_matches("TypeError", _rep("failed", "TypeError", "unsupported operand"))[0]
    assert not failure_matches("TypeError", _rep("failed", "AssertionError", "assert 1 == 2"))[0]
    assert failure_matches(
        "assertion: expected [1, 2] got [1]",
        _rep("failed", "AssertionError", "assert [1] == [1, 2]"),
    )[0]
    assert not failure_matches("ValueError", _rep("error", "ImportError", "No module named app"))[0]
    assert not failure_matches("ValueError", _rep("passed"))[0]
