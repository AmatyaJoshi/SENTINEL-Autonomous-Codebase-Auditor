"""Default adapter set."""

from __future__ import annotations

from sentinel.analyzers.bandit import BanditAdapter
from sentinel.analyzers.base import AnalyzerAdapter
from sentinel.analyzers.eslint import EslintAdapter
from sentinel.analyzers.mypy import MypyAdapter
from sentinel.analyzers.ruff import RuffAdapter
from sentinel.analyzers.semgrep import SemgrepAdapter
from sentinel.analyzers.tsc import TscAdapter


def default_adapters(include_semgrep: bool = True) -> list[AnalyzerAdapter]:
    adapters: list[AnalyzerAdapter] = [
        RuffAdapter(),
        BanditAdapter(),
        MypyAdapter(),
        EslintAdapter(),
        TscAdapter(),
    ]
    if include_semgrep:
        adapters.append(SemgrepAdapter())
    return adapters
