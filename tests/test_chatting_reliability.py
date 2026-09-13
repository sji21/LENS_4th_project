from copy import deepcopy
from unittest.mock import Mock

import pytest

from chat import dialogue_planner, dialogue_router, services
from chat.dialogue_contract import DecisionError, build_decision_input
from chat.dialogue_planner import bounded_model_input
from src.generation import call_budget


def test_optional_context_is_removed_whole_without_changing_current_conditions():
    payload = build_decision_input(services.initial_state(), "계약이 끝나지 않았고 보증금을 못 받았어요.")
    payload["history"] = [{"role": "user", "content": "옛 이야기" * 1000} for _ in range(4)]
    payload["last_answer"] = {"content": "이전 답변" * 1000}
    payload["facts"] = {"contract_ended": {"value": "아니요"}}
    before = deepcopy(payload)
    result = bounded_model_input(payload)
    assert result["user"] == payload["user"]
    assert result["context"]["facts"] == {"contract_ended": "아니요"}
    assert result["context"]["history"] == []
    assert result["context"]["last_answer"] is None
    assert payload == before


def test_required_context_overflow_fails_before_a_model_call(monkeypatch):
    monkeypatch.setattr(dialogue_planner, "PLANNER_INPUT_BYTES", 1)
    factory = Mock()
    monkeypatch.setattr(dialogue_planner, "create_planner_model", factory)
    with pytest.raises(dialogue_planner.PlanningError, match="context_limit"):
        dialogue_planner.plan_turn(services.initial_state(), "계약 기간을 알고 싶어요")
    factory.assert_not_called()


def test_rejected_input_never_reenters_migrated_planner_history():
    state = services.initial_state()
    state["messages"] = [
        {"role": "user", "content": "월세 보증금 문의"},
        {"role": "assistant", "status": "abstained", "content": "보류"},
        {"role": "user", "content": "UNSAFE_INSTRUCTIONS"},
        {"role": "assistant", "status": "refused", "content": "거절"},
    ]
    result = build_decision_input(state, "다시 질문할게요")
    assert result["history"] == [{"role": "user", "content": "월세 보증금 문의"}]


def test_call_limit_and_deadline_are_request_local(monkeypatch):
    clock = Mock(return_value=10.0)
    monkeypatch.setattr(call_budget.time, "monotonic", clock)
    assert call_budget.reserve_call(200) == 200
    with call_budget.conversation_budget(seconds=30, calls=2):
        assert call_budget.reserve_call(180) == 30
        clock.return_value = 20.0
        assert call_budget.reserve_call(90) == 20
        with pytest.raises(RuntimeError, match="call limit"):
            call_budget.reserve_call(90)
    with call_budget.conversation_budget(seconds=2):
        clock.return_value = 23.0
        with pytest.raises(RuntimeError, match="time budget"):
            call_budget.check_deadline()
    assert call_budget.reserve_call(200) == 200


def test_overdue_work_cannot_commit_partial_state(monkeypatch):
    clock = Mock(return_value=0.0)
    monkeypatch.setattr(call_budget.time, "monotonic", clock)
    state = {"original": True}
    with call_budget.conversation_budget(seconds=1):
        clock.return_value = 2.0
        with pytest.raises(RuntimeError, match="time budget"):
            dialogue_router._commit(state, {"partial": True})
    assert state == {"original": True}


def test_direct_conversation_calls_mask_inputs_before_planning_or_storage(monkeypatch):
    from types import SimpleNamespace
    from chat.dialogue_contract import Decision
    state = services.initial_state()
    decision = Decision(intent="greeting", action="social", topic=None, topic_changed=False,
                        updates={}, clarify_field=None, question=None, search_query="", document_id=None, style="standard")
    planner = Mock(return_value=SimpleNamespace(decision=decision))
    monkeypatch.setattr(dialogue_planner, "plan_turn", planner)
    user = "안녕하세요 제 번호는 010-1234-5678입니다"
    dialogue_router.respond_conversational(state, user, legacy=Mock())
    assert "010-1234-5678" not in str(planner.call_args)
    assert "010-1234-5678" not in str(state)


def test_native_llm_consumes_budget_and_redacts_transport_errors(monkeypatch, caplog):
    from src.generation import llm
    monkeypatch.setattr(llm, "_select_ollama_base", lambda *args: "http://localhost:11434")
    transport = Mock(side_effect=OSError("PRIVATE_DOCUMENT_TEXT"))
    monkeypatch.setattr(llm.urllib.request, "urlopen", transport)
    model = llm.get_llm(timeout=180, allow_route_fallback=False)
    with call_budget.conversation_budget(seconds=3, calls=1):
        with pytest.raises(RuntimeError) as caught:
            model.invoke("보증금 질문")
        assert "PRIVATE_DOCUMENT_TEXT" not in str(caught.value)
        with pytest.raises(RuntimeError, match="call limit"):
            model.invoke("재시도")
    transport.assert_called_once()
    assert 0 < transport.call_args.kwargs["timeout"] <= 3
    assert "PRIVATE_DOCUMENT_TEXT" not in caplog.text
