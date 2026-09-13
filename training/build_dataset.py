"""Build the triage dataset (SPEC §7.2/§7.4) from the findings table.

Label = 1 if the candidate reached `verified`/`fixed`/`pr_opened`, 0 if `refuted`. Candidates that
were never verified (sandbox unavailable) are excluded. Split is by *repository* (no leakage), with
entire repos held out for test. Optionally merges injected-bug positives from a bench manifest.

    uv run python training/build_dataset.py --out training/data --test-frac 0.2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlmodel import select  # noqa: E402

from sentinel.config import get_settings  # noqa: E402
from sentinel.db.models import FindingRecord, Run  # noqa: E402
from sentinel.db.session import get_engine, init_db, session_scope  # noqa: E402
from sentinel.graph.state import Finding  # noqa: E402
from sentinel.triage.model import FEATURE_NAMES, featurize, serialize_for_llm  # noqa: E402

POS = {"verified", "fixed", "pr_opened"}
NEG = {"refuted"}


def _excerpt(repo_path: str | None, file: str, line: int, pad: int = 15) -> str:
    if not repo_path:
        return ""
    p = Path(repo_path) / file
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return ""
    s, e = max(1, line - pad), min(len(lines), line + pad)
    return "\n".join(f"{i:5d} | {lines[i - 1]}" for i in range(s, e + 1))


def build(
    out: Path, test_frac: float, val_frac: float, seed: int, manifest: Path | None
) -> dict[str, int]:
    settings = get_settings()
    engine = get_engine(settings)
    init_db(engine)
    rows: list[dict[str, Any]] = []
    with session_scope(engine) as s:
        runs = {r.id: r for r in s.exec(select(Run)).all()}
        for fr in s.exec(select(FindingRecord)).all():
            if fr.status not in POS | NEG:
                continue
            if "triage:" in (fr.verify_log or ""):  # dropped by triage, never executed → no label
                continue
            run = runs.get(fr.run_id)
            f = Finding.model_validate(fr.model_dump())
            repo = run.repo_url if run else "unknown"
            rows.append(
                {
                    "id": fr.id,
                    "repo": repo,
                    "label": int(fr.status in POS),
                    "category": fr.category,
                    "features": featurize(
                        f, analyzer_hits=len(fr.evidence), callers=fr.blast_radius
                    ),
                    "text": serialize_for_llm(
                        f,
                        fr.evidence,
                        fr.blast_radius,
                        _excerpt(run.repo_path if run else None, fr.file, fr.line_start),
                    ),
                    "dedupe_key": hashlib.sha1(
                        f"{repo}:{fr.file}:{fr.line_start}".encode()
                    ).hexdigest(),  # noqa: S324
                }
            )
    if manifest and manifest.exists():
        # every injected mutation is a guaranteed positive at a known location (SPEC §7.2 #2)
        for inst in json.loads(manifest.read_text(encoding="utf-8"))["instances"]:
            f = Finding(
                id=inst["id"],
                category=inst["category"],
                severity="medium",
                file=inst["file"],
                line_start=inst["line_start"],
                line_end=inst["line_end"],
                description=inst["description"],
                hypothesis=f"mutation: {inst['original'].strip()} -> {inst['mutated'].strip()}",
                confidence=0.7,
                hunter_confidence=0.7,
            )
            rows.append(
                {
                    "id": inst["id"],
                    "repo": inst["repo"],
                    "label": 1,
                    "category": inst["category"],
                    "features": featurize(f, 1, 0),
                    "text": serialize_for_llm(
                        f,
                        [],
                        0,
                        _excerpt(inst["mutated_repo_path"], inst["file"], inst["line_start"]),
                    ),
                    "dedupe_key": hashlib.sha1(
                        f"{inst['repo']}:{inst['file']}:{inst['line_start']}".encode()
                    ).hexdigest(),
                }
            )  # noqa: S324
    seen: set[str] = set()
    rows = [r for r in rows if not (r["dedupe_key"] in seen or seen.add(r["dedupe_key"]))]  # type: ignore[func-returns-value]

    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_repo[r["repo"]].append(r)
    repos = sorted(by_repo)
    random.Random(seed).shuffle(repos)
    n_test = max(1, round(len(repos) * test_frac)) if len(repos) > 2 else 0
    n_val = max(1, round(len(repos) * val_frac)) if len(repos) > 3 else 0
    split = {
        "test": repos[:n_test],
        "val": repos[n_test : n_test + n_val],
        "train": repos[n_test + n_val :],
    }
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, rs in split.items():
        items = [r for repo in rs for r in by_repo[repo]]
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for r in items:
                fh.write(json.dumps({k: v for k, v in r.items() if k != "dedupe_key"}) + "\n")
        counts[name] = len(items)
    (out / "meta.json").write_text(
        json.dumps(
            {
                "features": list(FEATURE_NAMES),
                "split_repos": split,
                "counts": counts,
                "positives": sum(r["label"] for r in rows),
                "total": len(rows),
            },
            indent=2,
        )
    )
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", default="training/data")
    ap.add_argument("--test-frac", type=float, default=0.2)
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=20260914)
    ap.add_argument(
        "--manifest", default=None, help="bench manifest.json to add injected positives"
    )
    a = ap.parse_args(argv)
    counts = build(
        Path(a.out), a.test_frac, a.val_frac, a.seed, Path(a.manifest) if a.manifest else None
    )
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
