"""Regressions discovered during final development/UI evaluation, not heldout labels."""
from copy import deepcopy
from dataclasses import replace

from chat import dialogue_planner, services
from chat.dialogue_clarification import prepare_clarification
from chat.dialogue_state import apply_user_update
from tests.test_chatting_routing import runtime, proposal, call


def test_failed_plan_preserves_memory_but_never_sends_uncertain_case_to_rag(runtime):
    state = services.initial_state()
    apply_user_update(state, user="누나의 전세가 끝났습니다.", topic="보증금반환", updates={
        "contract_type": {"value": "전세", "evidence": "전세"},
        "contract_ended": {"value": "예", "evidence": "끝났습니다"},
    })
    state["messages"] = [{"role": "user", "content": "누나의 전세가 끝났습니다."},
                         {"role": "assistant", "content": "이전 답변", "status": "answered"}]
    old_messages = deepcopy(state["messages"])
    runtime.planner.side_effect = dialogue_planner.PlanningError("invalid_decision:unsupported_or_unstated_fact")
    question = "앞선 것은 누나의 이야기이고 제 월세 수리 문제를 물어볼게요."
    before = deepcopy(state["dialogue"])
    message = call(state, runtime, question)
    assert message["status"] == "clarify"
    assert state["dialogue"]["facts"] == before["facts"]
    assert state["dialogue"]["history"] == before["history"]
    runtime.legacy.assert_not_called()
    runtime.official.assert_not_called()
    assert state["messages"][:2] == old_messages
    assert len(state["messages"]) == 4


def test_document_choice_keeps_the_actual_question_in_its_submitted_message():
    state = services.initial_state()
    state["documents"] = [{"document_id": name, "filename": name + ".pdf", "kind": "contract"} for name in ("a", "b")]
    user = "이 계약서의 보증금과 계약 시작일을 알려주세요."
    decision = proposal(action="clarify", intent="document_question", clarify_field="document", search_query="")
    _, pending = prepare_clarification(state, user, decision)
    assert [c["document_id"] for c in pending["choices"]] == ["a", "b"]
    assert all(c["message"] == user for c in pending["choices"])


def test_explain_retrieves_the_previous_answered_question_without_new_facts(runtime):
    state = services.initial_state()
    original = "임차권등기를 신청할 때 법원에 내는 서류는 무엇인가요?"
    call(state, runtime, original)
    runtime.planner.return_value.decision = proposal(intent="explain", style="simple")
    call(state, runtime, "방금 답변을 더 쉽게 설명해 주세요.")
    query = runtime.official.call_args.args[0]
    assert original in query
    assert query.endswith("방금 답변을 더 쉽게 설명해 주세요.")
    assert state["dialogue"]["facts"] == {}


def test_changed_facts_do_not_reuse_an_obsolete_answer_question(runtime):
    state = services.initial_state()
    runtime.planner.return_value.decision = proposal(updates={"contract_type": {"value": "전세", "evidence": "전세"}})
    call(state, runtime, "전세 계약에서 준비할 서류를 알려주세요.")
    runtime.planner.return_value.decision = proposal(intent="explain", style="simple", updates={"contract_type": {"value": "월세", "evidence": "월세"}})
    call(state, runtime, "월세로 정정해서 쉽게 설명해 주세요.")
    assert "전세" not in runtime.official.call_args.args[0]


def test_explain_can_reuse_question_numbers_without_storing_them_as_facts(runtime):
    state = services.initial_state()
    question = "2027년에 계약할 경우에도 주택임대차보호법이 적용되나요?"
    call(state, runtime, question)
    base_query = state["dialogue"]["last_answer"]["query"]
    runtime.planner.return_value.decision = proposal(intent="explain", style="brief")
    for _ in range(3):
        call(state, runtime, "핵심만 요약해 주세요.")
        assert question in runtime.official.call_args.args[0]
        assert state["dialogue"]["last_answer"]["query"] == base_query
        assert state["dialogue"]["facts"] == {}


def test_unverified_answer_query_is_not_eligible_for_explain(runtime):
    state = services.initial_state()
    state["dialogue"]["last_answer"] = {"query": "UNVERIFIED_QUERY 987654", "validation": "failed"}
    runtime.planner.return_value.decision = proposal(intent="explain", style="simple")
    call(state, runtime, "쉽게 설명해 주세요.")
    assert "UNVERIFIED_QUERY" not in runtime.official.call_args.args[0]


def test_retired_history_cannot_be_restored_from_displayed_messages():
    from chat.dialogue_contract import build_decision_input
    state = services.initial_state()
    state["dialogue"].update(turn=2, epoch=1, history=[])
    state["messages"] = [{"role": "user", "content": "RETIRED_CASE"}, {"role": "assistant", "status": "answered"}]
    assert build_decision_input(state, "새 질문")["history"] == []


def test_switching_owned_document_invalidates_the_previous_answer_query(runtime):
    state = services.initial_state()
    state["documents"] = [{"document_id": name, "kind": "contract"} for name in ("a", "b")]
    runtime.planner.return_value.decision = proposal(document_id="a", intent="document_question")
    call(state, runtime, "첫 계약서 보증금은 얼마인가요?", "a")
    runtime.planner.return_value.decision = proposal(document_id="b", intent="explain", style="simple")
    call(state, runtime, "두 번째 계약서를 쉽게 설명해 주세요.", "b")
    assert "첫 계약서" not in runtime.document.call_args.args[0]
