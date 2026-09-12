from types import SimpleNamespace
import json
import pytest
from src.evaluation.chatting_run import seed_state, run, observed_state, capture_calls


def test_seed_is_independent_and_never_contains_gold():
    case = {"id": "synthetic", "initial_state": {"topic": "상담", "facts": {"contract_type": "월세"}, "pending_question": "계약은 끝났나요?"}, "documents": [], "turns": [{"expect": {"private_gold": "never send"}}]}
    first = seed_state(case)
    first["evaluation_seed"]["facts"]["contract_type"] = "변경"
    second = seed_state(case)
    assert second["evaluation_seed"]["facts"]["contract_type"] == "월세"
    assert "private_gold" not in json.dumps(second)
    assert second["messages"][-1]["content"] == "계약은 끝났나요?"


def test_legacy_missing_observation_is_not_an_empty_fact_set():
    result = observed_state({}, {"status": "abstained"}, "질문")
    assert result == {"action": "rag", "intent": None, "facts": None, "query": "질문"}


def test_upgrade_seed_restores_only_provided_prior_user_statements():
    case = {"initial_state": {"topic": "보증금반환", "facts": {"contract_type": "월세"}, "pending_question": "계약은 끝났나요?", "pending_field": "contract_ended"}}
    state = seed_state(case, dialogue_enabled=True)
    assert state["dialogue"]["facts"]["contract_type"]["source"] == "user_statement"
    assert state["dialogue"]["pending"]["field"] == "contract_ended"
    assert state["dialogue"]["last_answer"] is None
    assert observed_state(state, {}, "query", dialogue_enabled=False)["facts"] is None


def test_execution_failure_is_not_a_successful_rag_action():
    assert observed_state({}, {"status": "error"}, "attempted query")["action"] is None
    assert observed_state({}, {"status": "abstained"}, "")["action"] is None


@pytest.mark.parametrize("body", [b"not json", None])
def test_model_call_capture_observes_body_and_decode_failures(monkeypatch, body):
    import urllib.request
    from io import BytesIO
    class Response(BytesIO):
        def read(self, *args, **kwargs):
            if body is None:
                raise TimeoutError("private transport detail")
            return body
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: Response())
    with capture_calls() as calls:
        with pytest.raises((ValueError, TimeoutError)):
            with urllib.request.urlopen("http://localhost:11434/api/chat") as response:
                response.read()
    assert calls["failed_model_calls"] == calls["model_calls"] == 1


def test_model_call_capture_counts_failed_calls(monkeypatch):
    import urllib.request
    def fail(*args, **kwargs):
        raise OSError("test")
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with capture_calls() as calls:
        with pytest.raises(OSError):
            urllib.request.urlopen("http://localhost:11434/api/chat")
        with pytest.raises(OSError):
            urllib.request.urlopen("http://localhost:11434/api/tags")
    assert calls["model_calls"] == calls["failed_model_calls"] == 1


@pytest.mark.parametrize("source_changed", [False, True])
def test_capture_preserves_public_results_and_source_provenance(monkeypatch, tmp_path, source_changed):
    from chat import services
    from src.evaluation import chatting_run
    from src.generation import llm
    monkeypatch.setattr(llm, "probe", lambda **kwargs: (True, "ready"))
    monkeypatch.setattr(chatting_run, "capture_ollama_identity", lambda *a, **kw: {"available": True})
    monkeypatch.setattr(chatting_run, "capture_retrieval_provenance", lambda *a, **kw: {"ready": True})
    monkeypatch.setattr(chatting_run, "retrieval_semantically_equal", lambda a, b: a == b)
    loader = SimpleNamespace(result=lambda: object())
    loader.start = lambda: loader
    monkeypatch.setattr(services, "retrieval_loader", lambda: loader)
    def respond(state, question):
        assert "expect" not in state
        return {"status": "abstained", "content": "공개 답변", "context_content": "private internal"}
    monkeypatch.setattr(services, "respond", respond)
    hashes = iter([{"file": "a"}, {"file": "b" if source_changed else "a"}])
    monkeypatch.setattr(chatting_run, "fingerprints", lambda: next(hashes))
    output = tmp_path / "capture.json"
    report = run("dev", "legacy", output, ["DEV-001"])
    assert report["complete"] is not source_changed
    assert report["rows"][0]["observation"]["facts"] is None
    assert "private internal" not in output.read_text()
    with pytest.raises(ValueError, match="exists"):
        run("dev", "legacy", output, ["DEV-001"])


def test_missing_model_does_not_become_a_quality_score(monkeypatch, tmp_path):
    from src.generation import llm
    monkeypatch.setattr(llm, "probe", lambda **kwargs: (False, "private connection details"))
    output = tmp_path / "missing.json"
    with pytest.raises(RuntimeError, match="preflight"):
        run("dev", "legacy", output)
    assert not output.exists()


def test_lexical_fallback_does_not_become_a_hybrid_baseline(monkeypatch, tmp_path):
    from chat import services
    from src.evaluation import chatting_run
    from src.generation import llm
    monkeypatch.setattr(llm, "probe", lambda **kwargs: (True, "ready"))
    monkeypatch.setattr(chatting_run, "capture_ollama_identity", lambda *a, **kw: {"available": True})
    monkeypatch.setattr(chatting_run, "capture_retrieval_provenance", lambda *a, **kw: {"ready": False, "issues": ["dense_missing"]})
    loader = SimpleNamespace(result=lambda: object())
    loader.start = lambda: loader
    monkeypatch.setattr(services, "retrieval_loader", lambda: loader)
    monkeypatch.setattr(services, "respond", lambda *a: pytest.fail("must stop before inference"))
    output = tmp_path / "unready.json"
    with pytest.raises(RuntimeError, match="Hybrid retrieval preflight"):
        run("dev", "legacy", output)
    report = json.loads(output.read_text())
    assert report["complete"] is False
    assert report["rows"] == []
    assert report["retrieval"]["ready"] is False
