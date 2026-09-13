"""Mutation operators (SPEC §8.2). Each operator proposes single-line rewrites with a known category
and exact location so the benchmark can score Sentinel against ground truth."""

from bench.inject.operators import ALL_OPERATORS, Candidate, MutationOperator, propose

__all__ = ["ALL_OPERATORS", "Candidate", "MutationOperator", "propose"]
