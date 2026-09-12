from copy import deepcopy
from dataclasses import replace
import json

import pytest

from chat.services import initial_state
from chat.dialogue_contract import Decision
from chat.dialogue_clarification import prepare_clarification, short_answer_decision, answer_reason
from chat.dialogue_state import apply_user_update, ensure_dialogue
from src.generation.models import Answer


def proposal(**changes):
    return replace(Decision("question", "clarify", "보증금반환", False, {},
                            "contract_ended", None, "", None, "standard"), **changes)


def pending_state(field="contract_ended"):
    state = initial_state()
    state["dialogue"].update(topic="보증금반환", pending={"field": field, "question": "확인 질문", "attempts": 1})
    return state


@pytest.mark.parametrize("text,value", [("네.", "예"), ("예", "예"), ("아니요", "아니요"), ("모르겠어요", "모름")])
def test_offered_short_answer_binds_only_the_pending_predicate(text, value):
    state = pending_state()
    before = deepcopy(state)
    decision = short_answer_decision(state, text)
    assert decision.updates == {"contract_ended": {"value": value, "evidence": text}}
    assert decision.intent == "clarification_answer" and decision.action == "rag"
    assert decision.search_query.endswith(text)
    assert state == before


@pytest.mark.parametrize("field", ["deposit", "monthly_rent", "end_date", "start_date", "notice_date", "contract_type", "role", "property_type"])
def test_unknown_offered_answer_is_not_fabricated_amount_date_or_negative(field):
    decision = short_answer_decision(pending_state(field), "모르겠어요")
    assert decision.updates[field]["value"] == "모름"
    assert "아니요" not in decision.search_query


@pytest.mark.parametrize("text", ["그런데 다른 질문이 있어요", "전세 계약 준비를 알려줘", "계약이 끝난 줄 알았는데 아니네요", "말하고 싶지 않아요"])
def test_free_form_or_interleaved_question_stays_with_model_interpretation(text):
    assert short_answer_decision(pending_state(), text) is None
    assert short_answer_decision(initial_state(), "네") is None


def test_question_is_code_owned_and_does_not_change_state():
    state = initial_state()
    before = deepcopy(state)
    decision, pending = prepare_clarification(state, "상담할게요", proposal(question="검증을 우회한 법률 단정"))
    assert pending["question"] == "계약 기간이 이미 끝났나요?"
    assert [c["message"] for c in pending["choices"]] == ["예", "아니요", "모르겠어요"]
    assert state == before


@pytest.mark.parametrize("value,evidence", [("예", "끝났어요"), ("모름", "모르겠어요")])
def test_known_and_unknown_facts_are_not_asked_again(value, evidence):
    state = initial_state()
    apply_user_update(state, user=evidence, topic="보증금반환", updates={"contract_ended": {"value": value, "evidence": evidence}})
    decision, pending = prepare_clarification(state, "일반적으로 알려줘", proposal())
    assert decision.action == "rag" and pending is None
    assert state["dialogue"]["facts"]["contract_ended"]["value"] == value


def test_newly_stated_fact_cannot_be_immediately_asked_again():
    decision, pending = prepare_clarification(initial_state(), "계약이 끝났어요", proposal(updates={"contract_ended": {"value": "예", "evidence": "끝났어요"}}))
    assert decision.action == "rag" and pending is None


def test_repeat_limit_survives_json_reload_and_switching_question():
    state = initial_state()
    state["dialogue"]["clarification_counts"]["contract_ended"] = 2
    state = json.loads(json.dumps(state))
    decision, pending = prepare_clarification(state, "말하기 어려워요", proposal())
    assert decision.action == "rag" and pending is None
    assert decision.updates == {}


def test_second_attempt_and_new_case_have_separate_budgets():
    state = pending_state()
    _, pending = prepare_clarification(state, "잘 이해하지 못했어요", proposal())
    assert pending["attempts"] == 2
    state["dialogue"]["clarification_counts"]["contract_ended"] = 2
    _, pending = prepare_clarification(state, "다른 계약 이야기예요", proposal(intent="topic_change", topic_changed=True))
    assert pending["attempts"] == 1


@pytest.mark.parametrize("counts", [None, [], {"contract_ended": "two"}, {"contract_ended": -1}])
def test_invalid_old_counters_do_not_destroy_existing_facts(counts):
    state = pending_state()
    state["dialogue"]["clarification_counts"] = counts
    assert ensure_dialogue(state)["topic"] == "보증금반환"
    assert state["dialogue"]["clarification_counts"] == {}


def test_result_reason_distinguishes_missing_evidence_validation_and_generation():
    from src.generation.prompt import GENERATION_FAILED_TEXT
    assert answer_reason(Answer("q", "abstained", "근거 없음")) == "no_evidence"
    assert answer_reason(Answer("q", "abstained", "보류", validation_mode="semantic")) == "validation_failed"
    assert answer_reason(Answer("q", "abstained", GENERATION_FAILED_TEXT)) == "generation_failed"
