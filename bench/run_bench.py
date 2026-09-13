"""Benchmark harness entrypoint (SPEC.md §8). Full implementation lands in Phase 5."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_bench")
    parser.add_argument("--suite", choices=["small", "full"], default="small")
    parser.add_argument(
        "--arm", choices=["analyzers", "single_shot", "no_triage", "full"], default="full"
    )
    parser.add_argument("--out", default="bench/out")
    args = parser.parse_args(argv)
    print(f"bench suite={args.suite} arm={args.arm} out={args.out}: not implemented (Phase 5)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
