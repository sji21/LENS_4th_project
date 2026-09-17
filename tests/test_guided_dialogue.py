"""Guidance + a persisted question, including the next answer and case boundary."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from chat import services
from chat.dialogue_contract import DecisionError, _check_meaning
from chat.dialogue_clarification import prepare_clarification
from chat.dialogue_query import grounded_query
from src.generation.models import Answer
from src.generation.prompt import style_guidance
from tests.test_chatting_routing import runtime, proposal, call


USER = "전세 계약이 끝나가는데 보증금을 아직 돌려받지 못했어"


def begin(state, runtime):
    runtime.planner.return_value.decision = proposal(clarify_field="end_date", updates={
        "contract_type": {"value": "전세", "evidence": "전세"},
        "contract_ended": {"value": "아니요", "evidence": "계약이 끝나가는데"},
        "deposit_returned": {"value": "아니요", "evidence": "돌려받지 못했어"},
    })
    return call(state, runtime, USER)


def test_guidance_is_validated_before_a_separate_followup_is_displayed(runtime):
    state = services.initial_state()
    message = begin(state, runtime)
    assert message["status"] == "answered" and message["action"] == "rag"
    assert message["content"] == runtime.official.return_value.text
    assert message["followup_question"] == "계약 종료일을 알려주시겠어요?"
    assert state["dialogue"]["last_answer"]["content"] == message["content"]
    assert message["followup_question"] not in state["dialogue"]["last_answer"]["content"]
    assert state["dialogue"]["pending"]["mode"] == "after_answer"
    assert state["dialogue"]["pending"]["request"] == USER
    assert state["dialogue"]["pending"]["message_id"] == message["id"]
    assert runtime.official.call_args.kwargs["response_style"] == "consult"
    assert "계약 종료 여부: 아니요" in runtime.official.call_args.args[0]


def test_relative_date_answer_keeps_request_and_moves_to_the_next_question(runtime):
    state = services.initial_state()
    begin(state, runtime)
    runtime.planner.return_value.decision = proposal(intent="clarification_answer", clarify_field="landlord_notified", updates={
        "end_date": {"value": "다음 달 말", "evidence": "다음 달 말"},
    })
    message = call(state, runtime, "다음 달 말이에요.")
    assert message["action"] == "clarify"
    assert "알려주신 내용을 반영했어요" in message["content"]
    assert state["dialogue"]["facts"]["end_date"]["value"] == "다음 달 말"
    runtime.official.assert_called_once()
    assert state["dialogue"]["facts"]["contract_ended"]["value"] == "아니요"
    assert state["dialogue"]["pending"]["field"] == "landlord_notified"
    assert state["dialogue"]["pending"]["request"] == USER
    assert "임대인에게 의사를 알리셨나요?" in message["content"]
    assert runtime.planner.call_count == 2


@pytest.mark.parametrize("answer,value", [("네", "예"), ("아니요", "아니요"), ("모르겠어요", "모름")])
def test_guided_short_answers_keep_planning_but_never_repeat_the_answered_field(runtime, answer, value):
    state = services.initial_state()
    runtime.planner.return_value.decision = proposal(clarify_field="landlord_notified")
    call(state, runtime, "보증금 반환 상담을 하고 싶어요.")
    runtime.planner.return_value.decision = proposal(intent="clarification_answer", clarify_field="landlord_notified", updates={
        "landlord_notified": {"value": value, "evidence": answer},
    })
    message = call(state, runtime, answer)
    assert state["dialogue"]["facts"]["landlord_notified"]["value"] == value
    assert state["dialogue"]["pending"] is None
    assert "followup_question" not in message
    assert runtime.planner.call_count == 2


def test_multiple_details_in_one_answer_are_saved_without_reasking(runtime):
    state = services.initial_state()
    begin(state, runtime)
    runtime.planner.return_value.decision = proposal(intent="clarification_answer", clarify_field="landlord_notified", updates={
        "end_date": {"value": "다음 달 말", "evidence": "다음 달 말"},
        "landlord_notified": {"value": "예", "evidence": "문자로 알렸어요"},
        "notice_date": {"value": "지난주", "evidence": "지난주"},
    })
    message = call(state, runtime, "다음 달 말이고 지난주 문자로 알렸어요.")
    assert "followup_question" not in message
    assert state["dialogue"]["facts"]["notice_date"]["value"] == "지난주"


def test_new_case_does_not_reuse_pending_request_or_dates(runtime):
    state = services.initial_state()
    begin(state, runtime)
    runtime.planner.return_value.decision = proposal(intent="topic_change", topic_changed=True, topic="시설수리")
    call(state, runtime, "새로운 월세 집의 누수를 문의할게요.")
    assert USER not in runtime.official.call_args.args[0]
    assert state["dialogue"]["pending"] is None


def test_request_can_carry_original_numbers_without_fabricating_new_date_facts(runtime):
    state = services.initial_state()
    runtime.planner.return_value.decision = proposal(clarify_field="end_date")
    call(state, runtime, "2027년 계약에 관한 상담이에요. 어떤 점을 준비할까요?")
    runtime.planner.return_value.decision = proposal(intent="clarification_answer", updates={
        "end_date": {"value": "모름", "evidence": "모르겠어요"},
    })
    call(state, runtime, "모르겠어요")
    assert "2027" in runtime.official.call_args.args[0]
    assert state["dialogue"]["facts"] == {"end_date": state["dialogue"]["facts"]["end_date"]}


@pytest.mark.parametrize("status", ["abstained", "refused"])
def test_failed_legal_answer_is_never_relabelled_as_verified_guidance(runtime, status):
    state = services.initial_state()
    runtime.official.return_value = Answer(question=USER, text="검증 보류", status=status)
    message = begin(state, runtime)
    assert message["status"] == status
    assert state["dialogue"]["last_answer"] is None
    assert ("followup_question" in message) == (status == "abstained")


def test_graph_failure_does_not_commit_a_new_pending_question(runtime):
    state = services.initial_state()
    before = deepcopy(state)
    runtime.official.side_effect = RuntimeError("private")
    with pytest.raises(RuntimeError):
        begin(state, runtime)
    assert state == before


@pytest.mark.parametrize("prior_status,purpose", [("abstained", "general"), ("answered", "eligibility")])
def test_date_reply_still_uses_rag_without_verified_guidance_or_with_a_new_question(runtime, prior_status, purpose):
    state = services.initial_state()
    runtime.official.return_value = Answer(question=USER, text="기존 응답", status=prior_status)
    begin(state, runtime)
    runtime.planner.return_value.decision = proposal(
        intent="clarification_answer", purpose=purpose, clarify_field="landlord_notified",
        updates={"end_date": {"value": "다음 달 말", "evidence": "다음 달 말"}},
    )
    message = call(state, runtime, "다음 달 말이에요. 지금 가능한가요?" if purpose == "eligibility" else "다음 달 말이에요.")
    assert message["action"] == "rag"
    assert runtime.official.call_count == 2


@pytest.mark.parametrize("phrase", ["계약이 끝나가는데", "계약 만료 예정이에요", "아직 두 달 남았어요"])
def test_approaching_expiry_cannot_be_marked_as_already_ended(phrase):
    with pytest.raises(DecisionError):
        _check_meaning("contract_ended", "예", phrase)
    _check_meaning("contract_ended", "아니요", phrase)


def test_public_pending_hides_request_provenance_and_keeps_guided_mode(runtime):
    state = services.initial_state()
    begin(state, runtime)
    view = services.public_state(SimpleNamespace(state=state, id="fixture"))
    pending = view["conversation"]["pending"]
    assert pending["mode"] == "after_answer"
    assert not {"request", "epoch", "topic", "document_id"}.intersection(pending)


def test_guidance_style_is_only_an_opt_in_formatting_instruction():
    assert style_guidance(None) == ""
    text = style_guidance("consult")
    assert "2~3가지" in text and "확인되지 않은 조건을 확정하지" in text
    assert "새 확인 질문을 임의로 덧붙이지 마세요" in text


@pytest.mark.parametrize("phrase", [
    "갱신하지 않겠다고 알렸어요", "보증금을 못 받았다고 문자로 통지했어요",
    "이사하지 않겠다고 연락했어요",
])
def test_negative_intention_does_not_negate_the_notification_itself(phrase):
    _check_meaning("landlord_notified", "예", phrase)


@pytest.mark.parametrize("phrase", ["갱신하지 않겠다고 알리지 않았어요", "반환하라고 아직 못 알렸어요"])
def test_negative_notification_is_still_rejected_as_positive(phrase):
    with pytest.raises(DecisionError):
        _check_meaning("landlord_notified", "예", phrase)


@pytest.mark.parametrize("value", ["예", "아니요"])
def test_notification_channel_alone_is_not_a_reported_notification(value):
    with pytest.raises(DecisionError, match="unstated_notification"):
        _check_meaning("landlord_notified", value, "문자로")


@pytest.mark.parametrize("phrase", ["아니 아직 알려주지 않았어", "아직 알려드리지 않았어요", "아직 안 알려줬어", "아직 못 알려줬어요"])
def test_notification_negative_with_explanation_is_valid(phrase):
    _check_meaning("landlord_notified", "아니요", phrase)
    with pytest.raises(DecisionError, match="polarity"):
        _check_meaning("landlord_notified", "예", phrase)


@pytest.mark.parametrize("phrase", ["갱신하지 않겠다고 알려줬어요", "이사하지 않겠다고 알려드렸어요"])
def test_negative_intention_can_be_reported_with_inform_variants(phrase):
    _check_meaning("landlord_notified", "예", phrase)
