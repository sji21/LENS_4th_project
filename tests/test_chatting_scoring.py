from copy import deepcopy

import pytest

from src.evaluation.chatting_scoring import score_turn, summarize


@pytest.fixture
def expectation():
    return {
        "actions": ["rag", "clarify"],
        "intent": "followup",
        "facts": {"contract_type": "월세", "topic": "보증금 반환"},
        "forbidden_facts": {"contract_ended": "예"},
        "query_contains": ["월세", "보증금 반환"],
        "query_excludes": ["퇴거한 상태"],
    }


def test_missing_baseline_observations_are_not_failures(expectation):
    checks = score_turn(expectation, {"action": "rag", "intent": None, "facts": None, "query": "월세 보증금 반환"})
    assert checks["action"]["passed"] is True
    assert checks["intent"]["passed"] is None
    assert checks["facts"]["passed"] is None
    assert checks["forbidden_facts"]["passed"] is None


def test_exposed_empty_facts_fail_required_but_pass_forbidden(expectation):
    checks = score_turn(expectation, {"facts": {}})
    assert checks["facts"]["passed"] is False
    assert checks["facts"]["actual"] == {}
    assert checks["forbidden_facts"]["passed"] is True


def test_query_checks_are_inapplicable_for_clarification(expectation):
    checks = score_turn(expectation, {"action": "clarify", "query": "퇴거한 상태"})
    assert checks["query_contains"]["passed"] is None
    assert checks["query_excludes"]["passed"] is None


def test_missing_rag_query_is_unobservable_but_empty_query_is_measured(expectation):
    missing = score_turn(expectation, {"action": "rag", "query": None})
    empty = score_turn(expectation, {"action": "rag", "query": ""})
    assert missing["query_contains"]["passed"] is None
    assert empty["query_contains"]["passed"] is False
    assert empty["query_excludes"]["passed"] is True


def test_query_requires_all_terms_and_no_forbidden_terms(expectation):
    checks = score_turn(expectation, {"action": "rag", "query": "월세 퇴거한 상태"})
    assert checks["query_contains"]["passed"] is False
    assert checks["query_excludes"]["passed"] is False


def test_facts_require_exact_value_and_reject_any_forbidden_value(expectation):
    checks = score_turn(expectation, {"facts": {"contract_type": "월세", "topic": "보증금 반환", "contract_ended": "예"}})
    assert checks["facts"]["passed"] is True
    assert checks["forbidden_facts"]["passed"] is False
    checks = score_turn(expectation, {"facts": {"contract_type": "월세 계약", "topic": "보증금 반환"}})
    assert checks["facts"]["passed"] is False


def test_only_whitespace_is_normalized(expectation):
    checks = score_turn(expectation, {"action": "rag", "facts": {"contract_type": " 월세 ", "topic": "보증금\n 반환"}, "query": "월세\t보증금  반환은?"})
    assert checks["facts"]["passed"] is True
    assert checks["query_contains"]["passed"] is True
    numeric = {"facts": {"amount": "10000000원"}, "query_contains": ["보증금"]}
    checks = score_turn(numeric, {"action": "rag", "facts": {"amount": "1천만원"}, "query": "임대차 담보금"})
    assert checks["facts"]["passed"] is False
    assert checks["query_contains"]["passed"] is False


def test_wrong_action_and_intent_are_observed_failures(expectation):
    checks = score_turn(expectation, {"action": "social", "intent": "greeting"})
    assert checks["action"]["passed"] is False
    assert checks["intent"]["passed"] is False


def test_no_expectation_does_not_produce_vacuous_success():
    checks = score_turn({"actions": ["social"], "intent": "greeting"}, {"action": "social", "intent": "greeting", "facts": {}, "query": ""})
    for name in ("facts", "forbidden_facts", "query_contains", "query_excludes"):
        assert checks[name]["passed"] is None


def test_summary_keeps_unobservable_failures_and_errors_separate():
    rows = [
        {"checks": {"facts": {"passed": passed}}, "elapsed_seconds": elapsed, "model_calls": calls, "execution_error": error}
        for passed, elapsed, calls, error in [(True, 1, 1, None), (False, 2, 0, "TimeoutError"), (None, 3, 2, None)]
    ]
    summary = summarize(rows)
    assert summary["checks"]["facts"] == {"passed": 1, "failed": 1, "not_observable": 1}
    assert summary["latency_seconds"] == {"sample_count": 3, "p50": 2, "p95": 2.9, "max": 3}
    assert summary["total_model_calls"] == 3
    assert summary["execution_error_count"] == 1
    assert summary["turn_count"] == 3


def test_missing_measurements_are_not_reported_as_zero_latency():
    summary = summarize([{"checks": {}, "elapsed_seconds": None, "model_calls": None}])
    assert summary["latency_seconds"] == {"sample_count": 0, "p50": None, "p95": None, "max": None}
    assert summary["model_call_observation_count"] == 0
    assert summarize([])["turn_count"] == 0


def test_summary_does_not_claim_legal_correctness_or_aggregate_accuracy():
    summary = summarize([{"checks": {"action": {"passed": True}}}])
    assert "not legal correctness" in summary["scope"]
    assert "accuracy" not in summary
    assert "success_rate" not in summary
    assert "legal_accuracy" not in summary


@pytest.mark.parametrize("field,value", [("elapsed_seconds", -1), ("elapsed_seconds", float("nan")), ("elapsed_seconds", True), ("model_calls", -1), ("model_calls", 1.5)])
def test_invalid_measurements_are_not_silently_counted(field, value):
    with pytest.raises(ValueError):
        summarize([{field: value}])


def test_scoring_does_not_mutate_inputs(expectation):
    observed = {"action": "rag", "facts": {"contract_type": "월세"}, "execution_error": "TimeoutError"}
    before_expect, before_observed = deepcopy(expectation), deepcopy(observed)
    score_turn(expectation, observed)
    assert expectation == before_expect
    assert observed == before_observed
