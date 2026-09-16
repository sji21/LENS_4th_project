"""Deterministic conversation checks, not legal correctness or safety scores.

Facts use whitespace-normalized equality and query terms use normalized substring
matching. These checks do not recognize synonyms, equivalent numbers, negation
semantics, evidence quality, or whether a generated legal answer is correct.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from math import isfinite
from typing import Any


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _check(passed: bool | None, expected: Any, actual: Any) -> dict:
    return {"passed": passed, "expected": expected, "actual": actual}


def score_turn(expect: Mapping, observed: Mapping) -> dict:
    """Return named checks with True, False, or None (unobservable/inapplicable).

    An exposed empty facts mapping is different from an unavailable mapping:
    it fails a required-fact check but passes an absent-forbidden-fact check.
    Query checks apply only when the observed action is ``rag``. An execution
    error is counted by ``summarize`` independently of any exposed observations.
    """
    action = observed.get("action")
    intent = observed.get("intent")
    expected_actions = expect.get("actions", [])
    expected_intent = expect.get("intent")
    checks = {
        "action": _check(
            action in expected_actions if action is not None and expected_actions else None,
            expected_actions,
            action,
        ),
        "intent": _check(
            intent == expected_intent if intent is not None and expected_intent is not None else None,
            expected_intent,
            intent,
        ),
    }

    facts = observed.get("facts")
    for name, forbidden in (("facts", False), ("forbidden_facts", True)):
        expected = expect.get(name, {})
        passed = None
        if expected and isinstance(facts, Mapping):
            matches = [
                isinstance(facts.get(key), str)
                and _normalize(facts[key]) == _normalize(value)
                for key, value in expected.items()
            ]
            passed = not any(matches) if forbidden else all(matches)
        checks[name] = _check(passed, expected, facts)

    query = observed.get("query")
    for name, excluded in (("query_contains", False), ("query_excludes", True)):
        expected = expect.get(name, [])
        passed = None
        if expected and action == "rag" and isinstance(query, str):
            matches = [_normalize(term) in _normalize(query) for term in expected]
            passed = not any(matches) if excluded else all(matches)
        checks[name] = _check(passed, expected, query)
    return checks


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def summarize(rows: Iterable[Mapping]) -> dict:
    """Aggregate each check separately; do not report a legal-answer accuracy.

    Missing expectations and unavailable observations share ``not_observable``;
    retain individual checks' expected/actual fields to distinguish the reason.
    Missing timing and call counts are excluded, not converted to measurements.
    Percentiles use linear interpolation over the observed sorted durations.
    """
    counts: dict[str, dict[str, int]] = {}
    latencies = []
    total_calls = 0
    observed_call_rows = 0
    errors = 0
    turn_count = 0
    for row in rows:
        turn_count += 1
        for name, check in row.get("checks", {}).items():
            buckets = counts.setdefault(name, {"passed": 0, "failed": 0, "not_observable": 0})
            passed = check["passed"]
            if passed is True:
                buckets["passed"] += 1
            elif passed is False:
                buckets["failed"] += 1
            elif passed is None:
                buckets["not_observable"] += 1
            else:
                raise ValueError("Check passed must be True, False, or None")
        elapsed = row.get("elapsed_seconds")
        if elapsed is not None:
            if type(elapsed) not in (int, float) or not isfinite(elapsed) or elapsed < 0:
                raise ValueError("Elapsed seconds must be a finite non-negative number")
            latencies.append(float(elapsed))
        calls = row.get("model_calls")
        if calls is not None:
            if type(calls) is not int or calls < 0:
                raise ValueError("Model calls must be a non-negative integer")
            total_calls += calls
            observed_call_rows += 1
        errors += bool(row.get("execution_error"))
    latencies.sort()
    return {
        "scope": "Conversation behavior checks only; not legal correctness or safety validation.",
        "turn_count": turn_count,
        "checks": counts,
        "latency_seconds": {
            "sample_count": len(latencies),
            "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies, default=None),
        },
        "total_model_calls": total_calls,
        "model_call_observation_count": observed_call_rows,
        "execution_error_count": errors,
    }
