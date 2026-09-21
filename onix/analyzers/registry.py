"""Default adapter set."""

from __future__ import annotations

from onix.analyzers.bandit import BanditAdapter
from onix.analyzers.base import AnalyzerAdapter
from onix.analyzers.eslint import EslintAdapter
from onix.analyzers.mypy import MypyAdapter
from onix.analyzers.ruff import RuffAdapter
from onix.analyzers.semgrep import SemgrepAdapter
from onix.analyzers.tsc import TscAdapter


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
