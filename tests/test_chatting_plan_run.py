"""Planner calibration uses synthetic inputs and never runs retrieval or models."""

from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from src.evaluation import chatting_plan_run


def proposal(**changes):
    value = {
        "intent": "question", "action": "clarify", "topic": "보증금반환",
        "topic_changed": False,
        "updates": {
            "contract_type": {"value": "월세", "evidence": "월세"},
            "contract_ended": {"value": "아니요", "evidence": "끝나지 않았어요"},
        },
        "clarify_field": "deposit_returned", "question": "보증금을 받으셨나요?",
        "search_query": "", "document_id": None, "style": "standard",
    }
    value.update(changes)
    if isinstance(value["updates"], dict):
        value["updates"] = [{"field": field, **update} for field, update in value["updates"].items()]
    value["statements"] = value.pop("updates")
    for key in ("topic_changed", "question", "search_query"):
        value.pop(key)
    return json.dumps(value, ensure_ascii=False)


class FakeModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.inputs = []

    def invoke(self, messages):
        self.inputs.append(deepcopy(messages))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(content=output, response_metadata={})


@pytest.fixture
def harness(monkeypatch):
    from chat import dialogue_planner, services
    from src.generation import graph

    expectation = {
        "actions": ["clarify", "rag"], "intent": "question",
        "facts": {"contract_type": "월세", "contract_ended": "아니요"},
        "forbidden_facts": {"contract_ended": "예"},
        "query_contains": [], "query_excludes": [],
        "annotation": "GOLD_EXPECTATION_MUST_NOT_ENTER_MODEL",
    }
    suite = {
        "schema_version": 1, "split": "dev", "cases": [{
            "id": "SYNTHETIC-001", "initial_state": {}, "documents": [],
            "turns": [
                {"user": "월세 계약이 아직 끝나지 않았어요.", "expect": deepcopy(expectation)},
                {"user": "그럼 먼저 확인할 내용은 무엇인가요?", "expect": {**deepcopy(expectation), "intent": "followup"}},
            ],
        }],
    }
    model = FakeModel([
        proposal(),
        proposal(intent="followup", action="rag", updates={}, clarify_field=None, question=None,
                 search_query="계약이 끝나지 않은 월세 상담에서 먼저 확인할 내용"),
    ])
    identities = []
    loaded_splits = []

    def identity(*args, **kwargs):
        result = {"available": True, "model": "synthetic-model", "digest": "a" * 64}
        identities.append(result)
        return result

    def load(split):
        loaded_splits.append(split)
        assert split == "dev", "Calibration must never load acceptance cases"
        return deepcopy(suite)

    stats = {"model_calls": 1, "failed_model_calls": 0, "connection_failures": 0, "query": ""}

    @contextmanager
    def calls():
        yield dict(stats)

    def forbidden(*args, **kwargs):
        pytest.fail("Planner calibration must not run RAG, legal answers, or network")

    monkeypatch.setattr(chatting_plan_run, "capture_ollama_identity", identity)
    monkeypatch.setattr(chatting_plan_run, "fingerprints", lambda: {"synthetic_source": "stable-hash"})
    monkeypatch.setattr(chatting_plan_run, "load_suite", load)
    monkeypatch.setattr(chatting_plan_run, "capture_calls", calls)
    monkeypatch.setattr(dialogue_planner, "create_planner_model", lambda document_ids: model)
    monkeypatch.setattr(services, "respond", forbidden)
    monkeypatch.setattr(services, "retrieval_loader", forbidden)
    monkeypatch.setattr(graph, "answer_question", forbidden)
    monkeypatch.setattr(graph, "answer_document_question", forbidden)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    return SimpleNamespace(model=model, suite=suite, stats=stats, identities=identities, loaded_splits=loaded_splits)


def test_complete_capture_contains_every_turn_and_only_simulates_accepted_state(harness, tmp_path):
    output = tmp_path / "planner.json"
    report = chatting_plan_run.run(output)

    assert report["complete"] is True
    assert report["source_unchanged"] is True
    assert report["model_unchanged"] is True
    assert len(report["rows"]) == sum(len(case["turns"]) for case in harness.suite["cases"])
    first, second = report["rows"]
    assert first["after_state"]["facts"]["contract_ended"]["value"] == "아니요"
    assert first["after_state"]["pending"]["field"] == "deposit_returned"
    assert second["before_state"] == first["after_state"]
    assert second["after_state"]["pending"] is None
    assert second["after_state"]["facts"]["contract_type"]["value"] == "월세"
    assert all(row["after_state"]["last_status"] == "not_executed" for row in report["rows"])
    assert all(row["after_state"]["last_answer"] is None for row in report["rows"])
    assert report["summary"]["turn_count"] == 2
    assert report["summary"]["total_model_calls"] == 2
    assert json.loads(output.read_text()) == report
    assert "No RAG, legal validation or public answer" in report["limits"]


def test_only_dev_is_loaded_and_expected_labels_never_reach_inference(harness, tmp_path):
    original = deepcopy(harness.suite)
    report = chatting_plan_run.run(tmp_path / "planner.json")
    assert harness.loaded_splits == ["dev"]
    assert report["split"] == "dev"
    assert report["mode"] == "planner_only"
    assert harness.suite == original
    for row in report["rows"]:
        assert "GOLD_EXPECTATION_MUST_NOT_ENTER_MODEL" not in json.dumps(row["after_state"])
    assert len(harness.model.inputs) == 2
    for messages in harness.model.inputs:
        serialized = "\n".join(message.content for message in messages)
        assert "GOLD_EXPECTATION_MUST_NOT_ENTER_MODEL" not in serialized
        payload = json.loads(messages[-1].content)
        assert "expect" not in payload
        assert "evaluation_seed" not in payload
    later_input = json.loads(harness.model.inputs[1][-1].content)
    assert later_input["context"]["facts"]["contract_type"] == "월세"
    assert later_input["context"]["pending"]["field"] == "deposit_returned"


def test_source_change_keeps_full_rows_but_prevents_completion(harness, monkeypatch, tmp_path):
    snapshots = iter([{"source": "before"}, {"source": "changed"}])
    monkeypatch.setattr(chatting_plan_run, "fingerprints", lambda: next(snapshots))
    report = chatting_plan_run.run(tmp_path / "source-change.json")
    assert len(report["rows"]) == 2
    assert report["source_unchanged"] is False
    assert report["complete"] is False


@pytest.mark.parametrize("after", [
    {"available": True, "digest": "changed"},
    {"available": False, "reason": "unavailable"},
])
def test_changed_or_unavailable_final_model_identity_prevents_completion(harness, monkeypatch, tmp_path, after):
    identities = iter([{"available": True, "digest": "initial"}, after])
    monkeypatch.setattr(chatting_plan_run, "capture_ollama_identity", lambda *args: next(identities))
    report = chatting_plan_run.run(tmp_path / "model-change.json")
    assert len(report["rows"]) == 2
    assert report["model_unchanged"] is False
    assert report["complete"] is False


def test_initial_model_identity_failure_does_not_create_a_capture(harness, monkeypatch, tmp_path):
    monkeypatch.setattr(chatting_plan_run, "capture_ollama_identity", lambda *args: {"available": False})
    output = tmp_path / "no-model.json"
    with pytest.raises(RuntimeError, match="Model identity"):
        chatting_plan_run.run(output)
    assert not output.exists()
    assert harness.model.inputs == []
    assert harness.loaded_splits == []


@pytest.mark.parametrize("raw", ["not json", proposal(action="unsupported"), None])
def test_invalid_decision_preserves_state_and_prevents_completion(harness, tmp_path, raw):
    harness.suite["cases"][0]["turns"] = harness.suite["cases"][0]["turns"][:1]
    harness.model.outputs = [raw]
    report = chatting_plan_run.run(tmp_path / "invalid.json")
    row = report["rows"][0]
    assert report["complete"] is False
    assert row["execution_error"]
    assert row["decision"] is None
    assert row["before_state"] == row["after_state"]
    assert row["observation"]["action"] is None
    assert row["raw_proposal"] == raw


def test_planning_error_without_http_failure_is_still_incomplete(harness, monkeypatch, tmp_path):
    from chat import dialogue_planner
    harness.stats.update(model_calls=0, failed_model_calls=0, connection_failures=0)
    def fail(*args, **kwargs):
        raise dialogue_planner.PlanningError("invalid_input:user")
    monkeypatch.setattr(dialogue_planner, "plan_turn", fail)
    report = chatting_plan_run.run(tmp_path / "no-http.json")
    assert len(report["rows"]) == 2
    assert report["complete"] is False
    assert all(row["execution_error"] == "invalid_input:user" for row in report["rows"])
    assert all(row["model_calls"] == row["failed_model_calls"] == row["connection_failures"] == 0 for row in report["rows"])
    assert all(row["decision"] is None and row["raw_proposal"] is None for row in report["rows"])


@pytest.mark.parametrize("failure_field", ["failed_model_calls", "connection_failures"])
def test_instrumented_http_failures_prevent_completion_even_with_valid_decisions(harness, tmp_path, failure_field):
    harness.stats[failure_field] = 1
    report = chatting_plan_run.run(tmp_path / "transport.json")
    assert all(row["execution_error"] is None for row in report["rows"])
    assert report["complete"] is False


def test_second_model_failure_cannot_reuse_the_previous_raw_proposal(harness, tmp_path):
    harness.model.outputs[1] = OSError("DO_NOT_RECORD_PRIVATE_TRANSPORT_DETAIL")
    report = chatting_plan_run.run(tmp_path / "failed-followup.json")
    first, second = report["rows"]
    assert first["raw_proposal"] is not None
    assert second["raw_proposal"] is None
    assert second["decision"] is None
    assert second["execution_error"] == "model_unavailable"
    assert second["before_state"] == second["after_state"]
    assert report["complete"] is False
    assert "DO_NOT_RECORD_PRIVATE_TRANSPORT_DETAIL" not in json.dumps(report)


def test_failure_before_second_inference_does_not_reuse_the_previous_proposal(harness, monkeypatch, tmp_path):
    from chat import dialogue_planner
    original = dialogue_planner.plan_turn
    seen = []
    def plan(state, user, **kwargs):
        seen.append(user)
        if len(seen) == 2:
            raise dialogue_planner.PlanningError("invalid_input:user")
        return original(state, user, **kwargs)
    monkeypatch.setattr(dialogue_planner, "plan_turn", plan)
    report = chatting_plan_run.run(tmp_path / "no-second-inference.json")
    first, second = report["rows"]
    assert first["raw_proposal"] is not None
    assert second["raw_proposal"] is None
    assert second["decision"] is None
    assert second["before_state"] == second["after_state"]
    assert report["complete"] is False


def test_existing_capture_is_never_overwritten_or_used_for_model_calls(harness, tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("KEEP_EXISTING_CAPTURE")
    with pytest.raises(ValueError, match="exists"):
        chatting_plan_run.run(output)
    assert output.read_text() == "KEEP_EXISTING_CAPTURE"
    assert harness.identities == []
    assert harness.model.inputs == []


def test_unknown_selected_case_fails_before_creating_output(harness, tmp_path):
    output = tmp_path / "unknown.json"
    with pytest.raises(ValueError, match="Unknown development"):
        chatting_plan_run.run(output, ["not-in-dev"])
    assert not output.exists()
    assert harness.model.inputs == []


def test_separate_cases_do_not_inherit_previous_simulated_user_facts(harness, tmp_path):
    harness.suite["cases"].append({
        "id": "SYNTHETIC-002", "initial_state": {}, "documents": [],
        "turns": [{"user": "안녕하세요.", "expect": {"actions": ["social"], "intent": "greeting", "facts": {}}}],
    })
    harness.model.outputs.append(proposal(
        intent="greeting", action="social", topic=None, updates=[],
        clarify_field=None, question=None, search_query="",
    ))
    report = chatting_plan_run.run(tmp_path / "two-cases.json")
    assert report["complete"] is True
    assert len(report["rows"]) == 3
    first_case_last, second_case = report["rows"][1:]
    assert first_case_last["after_state"]["facts"]["contract_type"]["value"] == "월세"
    assert second_case["before_state"]["facts"] == {}
    assert second_case["before_state"]["history"] == []
    assert second_case["after_state"]["facts"] == {}
    model_input = json.loads(harness.model.inputs[2][-1].content)
    assert model_input["context"]["facts"] == {}
    assert model_input["context"]["pending"] is None


def test_rejected_later_wire_update_cannot_partially_commit_earlier_valid_fact(harness, tmp_path):
    harness.suite["cases"][0]["turns"] = harness.suite["cases"][0]["turns"][:1]
    harness.model.outputs = [proposal(updates=[
        {"field": "contract_type", "value": "월세", "evidence": "월세"},
        {"field": "contract_ended", "value": "아니요", "evidence": "NEVER_IN_THIS_USER_INPUT"},
    ])]
    report = chatting_plan_run.run(tmp_path / "rejected-update.json")
    row = report["rows"][0]
    assert report["complete"] is False
    assert row["execution_error"].startswith("invalid_decision:")
    assert row["decision"] is None
    assert row["before_state"] == row["after_state"]
    assert row["after_state"]["facts"] == {}



def test_raw_model_errors_remain_visible_after_source_preserving_normalization(harness, tmp_path):
    harness.model.outputs[1] = proposal(
        intent="question", action="rag", clarify_field=None, question=None,
        updates={"contract_type": {"value": "월세", "evidence": "월세"}},
        search_query="계약이 끝나지 않은 월세 상담에서 먼저 확인할 내용",
    )
    output = tmp_path / "normalized.json"
    report = chatting_plan_run.run(output)
    first, second = report["rows"]
    assert report["complete"] is True
    assert second["raw_output_valid"] is False
    assert second["raw_intent_check"]["passed"] is False
    assert second["checks"]["intent"]["passed"] is True
    assert second["decision"]["updates"] == {}
    assert second["after_state"]["facts"]["contract_type"] == first["after_state"]["facts"]["contract_type"]
    assert any(item.get("field") == "contract_type" for item in second["normalizations"])
    assert any(item.get("reason") == "context_label_consistency" for item in second["normalizations"])
    assert report["raw_output_valid_turns"] == 1
    assert report["normalization_turns"] == 1
    assert report["raw_intent_summary"] == {"passed": 1, "failed": 1, "not_observable": 0}
    assert json.loads(output.read_text()) == report
