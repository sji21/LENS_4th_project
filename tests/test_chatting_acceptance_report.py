from copy import deepcopy
from scripts.report_chatting_run import assess


def capture():
    checks = {name: {"passed": True, "expected": ["required"]} for name in
              ("action", "intent", "facts", "forbidden_facts", "query_contains", "query_excludes")}
    row = {"checks": checks, "observation": {"action": "rag"}, "model_calls": 3}
    return {"complete": True, "split": "dev", "rows": [deepcopy(row) for _ in range(4)],
            "summary": {"latency_seconds": {"p95": 30}}}


def test_missing_intents_count_against_the_required_denominator():
    report = capture()
    for row in report["rows"][:2]:
        row["checks"]["intent"]["passed"] = None
    result = assess(report, capture())
    assert result["gates"]["intent"]["required_count"] == 4
    assert result["gates"]["intent"]["unobservable_count"] == 2
    assert not result["automated_gates_passed"]


def test_complete_capture_is_not_a_quality_pass_with_retired_facts_or_slow_calls():
    report = capture()
    report["rows"][0]["checks"]["forbidden_facts"]["passed"] = False
    report["summary"]["latency_seconds"]["p95"] = 70
    result = assess(report, capture())
    assert result["gates"]["capture_integrity"]["passed"]
    assert not result["gates"]["no_forbidden_active_facts"]["passed"]
    assert not result["gates"]["latency"]["passed"]
    assert not result["automated_gates_passed"]


def test_a_passing_automatic_report_still_requires_manual_review():
    result = assess(capture(), capture())
    assert result["automated_gates_passed"]
    assert result["manual_review_required"]
