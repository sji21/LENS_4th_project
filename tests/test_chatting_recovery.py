"""Conversation failures and counterexamples, with legal generation isolated."""
from copy import deepcopy

import pytest

from chat import dialogue_planner, services
from chat.dialogue_planner import PlanningError, plan_turn
from chat.dialogue_state import apply_user_update
from tests.test_chatting_planner import fake_model, payload
from tests.test_chatting_routing import runtime, proposal, call


def ongoing():
    state = services.initial_state()
    apply_user_update(state, user="전세 계약이 끝났는데 보증금을 못 받았어요.", topic="보증금반환", updates={
        "contract_type": {"value": "전세", "evidence": "전세"},
        "contract_ended": {"value": "예", "evidence": "끝났는데"},
        "deposit_returned": {"value": "아니요", "evidence": "못 받았어요"},
    })
    return state


@pytest.mark.parametrize("field,value,evidence", [
    ("subject", "처음 계약하는 사람", "처음 계약하는 사람"),
    ("role", "임차인", "요약해 주세요"),
    ("property_type", "아파트", "요약해 주세요"),
])
def test_optional_extraction_error_does_not_discard_a_valid_explanation(field, value, evidence):
    state = ongoing()
    before = deepcopy(state)
    user = "처음 계약하는 사람도 알아듣게 요약해 주세요."
    result = plan_turn(state, user, llm=fake_model(payload(
        intent="explain", style="brief", statements=[{"field": field, "value": value, "evidence": evidence}],
    )))
    assert result.decision.intent == "explain"
    assert result.decision.updates == {}
    assert "계약 유형: 전세" in result.decision.search_query
    assert result.normalizations[0]["reason"] == "unsupported_optional_statement_removed"
    assert state == before


def test_optional_error_during_new_case_still_retires_every_old_fact():
    state = ongoing()
    user = "그건 친구 이야기고 제 월세 집의 보일러 수리를 문의합니다."
    result = plan_turn(state, user, llm=fake_model(payload(
        intent="topic_change", topic="시설수리", statements=[
            {"field": "contract_type", "value": "월세", "evidence": "월세"},
            {"field": "property_type", "value": "보일러", "evidence": "보일러"},
        ],
    )))
    assert result.decision.topic_changed
    assert result.decision.updates == {"contract_type": {"value": "월세", "evidence": "월세"}}
    assert "계약 유형: 전세" not in result.decision.search_query
    assert "계약 종료 여부" not in result.decision.search_query


def test_duplicate_correction_keeps_other_facts_and_does_not_mutate_source_state():
    state = ongoing()
    before = deepcopy(state)
    user = "정정할게요. 아직 계약이 끝나지 않았어요. 나머지는 그대로예요."
    update = {"field": "contract_ended", "value": "아니요", "evidence": "계약이 끝나지 않았어요"}
    result = plan_turn(state, user, llm=fake_model(payload(intent="correction", statements=[update, update])))
    assert "계약 종료 여부: 아니요" in result.decision.search_query
    assert "보증금 반환 여부: 아니요" in result.decision.search_query
    assert "계약 유형: 전세" in result.decision.search_query
    assert len(result.decision.updates) == 1
    assert state == before


@pytest.mark.parametrize("statements", [
    [{"field": "deposit", "value": "9999만원", "evidence": "보증금"}],
    [{"field": "contract_ended", "value": "예", "evidence": "끝나지 않았어요"}],
    [{"field": "contract_type", "value": v, "evidence": v} for v in ("전세", "월세")],
])
def test_core_fact_errors_are_never_silently_discarded(statements):
    with pytest.raises(PlanningError):
        plan_turn(ongoing(), "전세 월세 보증금 계약이 끝나지 않았어요.",
                  llm=fake_model(payload(statements=statements)))


def test_optional_repair_does_not_authorize_another_sessions_document():
    with pytest.raises(PlanningError):
        plan_turn(ongoing(), "요약해 주세요", llm=fake_model(payload(
            intent="explain", document_id="foreign", statements=[
                {"field": "role", "value": "임차인", "evidence": "요약해 주세요"},
            ],
        )))


def test_optional_repair_cannot_hide_an_uncertain_case_transition():
    with pytest.raises(PlanningError):
        plan_turn(ongoing(), "제 월세 집의 보일러 수리 문제예요", llm=fake_model(payload(
            intent="question", topic="시설수리", statements=[
                {"field": "property_type", "value": "아파트", "evidence": "보일러"},
            ],
        )))


@pytest.mark.parametrize("existing", [False, True])
def test_invented_unknown_is_omitted_only_for_an_unset_nonpending_field(existing):
    state = ongoing()
    if existing:
        apply_user_update(state, user="보증금은 5만원", updates={"deposit": {"value": "5만원", "evidence": "5만원"}})
    user = "계약서에 보증금이 얼마로 적혀 있나요?"
    model = fake_model(payload(intent="document_question", statements=[
        {"field": "deposit", "value": "모름", "evidence": "보증금이 얼마로 적혀 있나요"},
    ]))
    if existing:
        with pytest.raises(PlanningError):
            plan_turn(state, user, llm=model)
    else:
        result = plan_turn(state, user, llm=model)
        assert result.decision.updates == {}
        assert result.normalizations[0]["reason"] == "unstated_unknown_removed"


def test_pending_unknown_keeps_unrelated_facts():
    state = ongoing()
    state["dialogue"]["pending"] = {"field": "landlord_notified", "question": "통지했나요?"}
    user = "문서를 찾아봐야 알 것 같아요. 지금은 확답하기 어렵네요."
    result = plan_turn(state, user, llm=fake_model(payload(intent="clarification_answer", statements=[
        {"field": "landlord_notified", "value": "모름", "evidence": user},
    ])))
    assert result.decision.updates["landlord_notified"]["value"] == "모름"
    assert "계약 유형: 전세" in result.decision.search_query


def test_followup_retains_specific_request_not_just_the_topic(runtime):
    state = services.initial_state()
    user = "임차권등기를 신청할 때 준비할 서류는 무엇인가요?"
    call(state, runtime, user)
    runtime.planner.return_value.decision = proposal(intent="followup")
    call(state, runtime, "그 신청은 어디에 해야 하나요?")
    assert user in runtime.official.call_args.args[0]


@pytest.mark.parametrize("original_turn", [True, False])
def test_restatement_can_quote_original_turn_context_but_not_another_turn(original_turn):
    state = services.initial_state()
    apply_user_update(state, user="보증금 반환: 아니요", topic="보증금반환", updates={
        "deposit_returned": {"value": "아니요", "evidence": "아니요"},
    })
    if not original_turn:
        state["dialogue"]["history"][0]["turn"] = 999
    before = deepcopy(state)
    user = "종료 여부를 모르는 상태에서 확인할 내용을 알려주세요."
    model = fake_model(payload(intent="followup", statements=[
        {"field": "contract_ended", "value": "모름", "evidence": "종료 여부를 모르는 상태"},
        {"field": "deposit_returned", "value": "아니요", "evidence": "보증금 반환: 아니요"},
    ]))
    if original_turn:
        result = plan_turn(state, user, llm=model)
        assert "deposit_returned" not in result.decision.updates
        assert "보증금 반환 여부: 아니요" in result.decision.search_query
        assert "계약 종료 여부: 모름" in result.decision.search_query
    else:
        with pytest.raises(PlanningError):
            plan_turn(state, user, llm=model)
    assert state == before


@pytest.mark.parametrize("new_case", [False, True])
def test_confirmed_recovery_keeps_or_clears_context_before_planning(runtime, new_case):
    state = ongoing()
    state["documents"] = [{"document_id": "old", "kind": "contract"}]
    state["dialogue"]["active_document_id"] = "old"
    before = deepcopy(state["dialogue"])
    runtime.planner.side_effect = PlanningError("invalid_decision:updates")
    message = call(state, runtime, "수리 책임이 궁금해요.")
    assert state["dialogue"]["facts"] == before["facts"]
    assert not runtime.official.called and not runtime.document.called and not runtime.legacy.called
    choice = message["choices"][int(new_case)]
    runtime.planner.side_effect = None
    runtime.planner.return_value.decision = proposal(intent="question", topic="시설수리")
    call(state, runtime, choice["message"])
    planner_state = runtime.planner.call_args.args[0]
    if new_case:
        assert planner_state["dialogue"]["facts"] == {}
        assert planner_state["dialogue"]["active_document_id"] is None
        assert "계약 유형: 전세" not in runtime.official.call_args.args[0]
    else:
        assert planner_state["dialogue"]["facts"] == before["facts"]
        assert planner_state["dialogue"]["active_document_id"] == "old"


def test_recovery_is_atomic_when_confirmed_new_case_planner_is_unavailable(runtime):
    state = ongoing()
    runtime.planner.side_effect = PlanningError("invalid_decision:updates")
    message = call(state, runtime, "다른 계약 문의예요.")
    before = deepcopy(state)
    runtime.planner.side_effect = PlanningError("model_unavailable")
    with pytest.raises(RuntimeError):
        call(state, runtime, message["choices"][1]["message"])
    assert state == before


def test_invalid_optional_fact_does_not_interrupt_owned_document_followup(runtime, monkeypatch):
    state = services.initial_state()
    state["documents"] = [{"document_id": "a", "kind": "contract"}]
    apply_user_update(state, user="계약서를 봐주세요", document_id="a", topic="문서확인")
    model = fake_model(payload(intent="followup", topic="문서확인", statements=[
        {"field": "property_type", "value": "시설물", "evidence": "시설물"},
    ]))
    monkeypatch.setattr(dialogue_planner, "plan_turn", lambda s, u, d: plan_turn(s, u, d, llm=model))
    call(state, runtime, "거기에 시설물에 관해 적힌 문구도 풀어주세요.")
    runtime.document.assert_called_once()
    assert runtime.evidences.call_args.args[2] == "a"
    runtime.official.assert_not_called()
