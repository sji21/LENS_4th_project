"""The planner supplies data; validated contracts decide what may execute."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json

import pytest

from chat.dialogue_contract import DecisionError, build_decision_input, parse_decision
from chat.dialogue_state import apply_user_update, ensure_dialogue, record_answer


def session():
    return {"messages": [], "documents": [], "completed_requests": []}


def decision_payload(**changes):
    payload = {
        "intent": "question",
        "action": "rag",
        "topic": "보증금반환",
        "topic_changed": False,
        "updates": {},
        "clarify_field": None,
        "question": None,
        "search_query": "월세 보증금 반환 시 확인할 내용",
        "document_id": None,
        "style": "standard",
    }
    payload.update(changes)
    return payload


def parse(payload, *, state=None, user="월세 보증금 반환을 문의할게요."):
    return parse_decision(json.dumps(payload, ensure_ascii=False), state=session() if state is None else state, user=user)


def test_valid_decision_is_immutable_and_parsing_does_not_apply_user_updates():
    state = session()
    before = deepcopy(state)
    result = parse(
        decision_payload(updates={"contract_type": {"value": "월세", "evidence": "월세"}}),
        state=state,
    )

    assert result.intent == "question"
    assert result.action == "rag"
    assert result.updates["contract_type"] == {"value": "월세", "evidence": "월세"}
    assert result.search_query == "월세 보증금 반환 시 확인할 내용"
    assert state == before
    with pytest.raises(FrozenInstanceError):
        result.action = "social"


def test_current_residence_is_not_negated_by_the_word_still():
    with pytest.raises(DecisionError, match="polarity"):
        parse(decision_payload(updates={"living_in_property": {"value": "아니요", "evidence": "아직 살고 있어요"}}), user="아직 살고 있어요")


@pytest.mark.parametrize("query", ["보증금 300억원 반환", "보증금 오백만원 반환"])
def test_query_cannot_invent_units_or_hangul_amounts(query):
    with pytest.raises(DecisionError):
        parse(decision_payload(search_query=query), user="월세 보증금 300만원 반환을 문의해요.")


def test_query_cannot_reverse_an_active_negative_user_statement():
    state = session()
    apply_user_update(state, user="계약이 끝나지 않았어요.", updates={"contract_ended": {"value": "아니요", "evidence": "끝나지 않았어요"}})
    with pytest.raises(DecisionError, match="query_polarity"):
        parse(decision_payload(search_query="계약이 끝났고 보증금은 못 받은 상황"), state=state, user="그 조건대로 알려주세요.")


def test_compound_amount_preserves_its_first_magnitude():
    user = "보증금은 1억5천만원입니다. 반환 절차를 알려주세요."
    with pytest.raises(DecisionError):
        parse(decision_payload(search_query="보증금 1조5천만원 반환 절차"), user=user)
    assert parse(decision_payload(search_query="보증금 1억 5천만원 반환 절차"), user=user).action == "rag"


def test_moving_date_and_conditional_word_are_not_hangul_amounts():
    assert parse(decision_payload(search_query="만일 이사일이 다르면 보증금 반환에 어떤 영향이 있나요?")).action == "rag"


def test_json_integer_conversion_limit_has_the_typed_error_contract():
    raw = json.dumps(decision_payload(), ensure_ascii=False).replace('"topic": "보증금반환"', '"topic": ' + "1" * 5000)
    with pytest.raises(DecisionError):
        parse_decision(raw, state=session(), user="보증금 반환")


@pytest.mark.parametrize(
    "key",
    ["intent", "action", "topic", "topic_changed", "updates", "clarify_field", "question", "search_query", "document_id", "style"],
)
def test_missing_contract_field_is_not_silently_defaulted(key):
    payload = decision_payload()
    del payload[key]

    with pytest.raises(DecisionError) as exc:
        parse(payload)

    assert isinstance(exc.value.code, str)
    assert exc.value.code


@pytest.mark.parametrize("extra", ["execute", "tool_call", "raw_text", "answer"])
def test_unknown_output_fields_cannot_extend_the_execution_contract(extra):
    payload = decision_payload()
    payload[extra] = "unexpected instruction"

    with pytest.raises(DecisionError):
        parse(payload)


@pytest.mark.parametrize(
    "raw",
    ["not json", "[]", "null", "42", '{"intent": "question"', '{"intent": "question", "intent": "greeting"}'],
)
def test_invalid_json_and_duplicate_top_level_keys_have_typed_failures(raw):
    state = session()
    before = deepcopy(state)

    with pytest.raises(DecisionError) as exc:
        parse_decision(raw, state=state, user="월세 보증금을 문의해요.")

    assert exc.value.code
    assert state == before


def test_duplicate_nested_evidence_is_not_last_value_wins():
    payload = decision_payload(updates={"contract_type": {"value": "월세", "evidence": "월세"}})
    raw = json.dumps(payload, ensure_ascii=False).replace('"evidence": "월세"', '"evidence": "전세", "evidence": "월세"')

    with pytest.raises(DecisionError):
        parse_decision(raw, state=session(), user="월세에 살아요.")


@pytest.mark.parametrize(
    "changes",
    [
        {"intent": "execute_shell"},
        {"action": "answer_legal_without_validation"},
        {"style": "unverified"},
        {"intent": ["question"]},
        {"action": None},
        {"topic": 3},
        {"topic_changed": "false"},
        {"topic_changed": 0},
        {"updates": []},
        {"clarify_field": True},
        {"question": ["질문"]},
        {"search_query": None},
        {"document_id": 1},
        {"style": False},
    ],
)
def test_unknown_enums_and_wrong_types_are_rejected(changes):
    with pytest.raises(DecisionError):
        parse(decision_payload(**changes))


@pytest.mark.parametrize(
    "intent,changed",
    [("topic_change", False), ("question", True), ("correction", True)],
)
def test_topic_reset_requires_consistent_intent_and_boolean(intent, changed):
    with pytest.raises(DecisionError):
        parse(decision_payload(intent=intent, topic_changed=changed))


@pytest.mark.parametrize("intent", ["question", "followup", "explain", "document_question"])
def test_legal_or_document_requests_cannot_use_social_action(intent):
    with pytest.raises(DecisionError):
        parse(decision_payload(intent=intent, action="social", search_query=""))


def test_social_greeting_and_clarifying_ambiguous_request_are_valid():
    greeting = parse(
        decision_payload(intent="greeting", action="social", topic=None, search_query=""),
        user="안녕하세요.",
    )
    clarification = parse(
        decision_payload(action="clarify", clarify_field="details", question="어떤 임대차 상황인지 알려주시겠어요?", search_query=""),
        user="제가 어떻게 해야 하죠?",
    )

    assert greeting.action == "social"
    assert clarification.action == "clarify"
    assert clarification.clarify_field == "details"


@pytest.mark.parametrize("query", ["", "   "])
def test_rag_requires_an_actual_search_question(query):
    with pytest.raises(DecisionError):
        parse(decision_payload(search_query=query))


def test_document_ownership_is_checked_without_mutating_selected_document():
    state = session()
    state["documents"] = [{"document_id": "doc-owned", "kind": "contract", "label": "계약서"}]
    ensure_dialogue(state)
    before = deepcopy(state)
    valid = parse(decision_payload(intent="document_question", document_id="doc-owned"), state=state)

    assert valid.document_id == "doc-owned"
    with pytest.raises(DecisionError):
        parse(decision_payload(intent="document_question", document_id="doc-foreign"), state=state)
    assert state == before


@pytest.mark.parametrize(
    "updates",
    [
        {"unsupported_legal_conclusion": {"value": "확정", "evidence": "월세"}},
        {"contract_type": {"value": "매매", "evidence": "월세"}},
        {"contract_ended": {"value": "yes", "evidence": "월세"}},
        {"contract_ended": {"value": True, "evidence": "월세"}},
        {"contract_type": {"value": "월세", "evidence": "전혀 제공하지 않은 문장"}},
        {"contract_type": {"value": "월세", "evidence": ""}},
        {"contract_type": {"value": "월세"}},
        {"contract_type": {"value": "월세", "evidence": "월세", "source": "official_law"}},
    ],
)
def test_update_field_value_and_current_user_provenance_are_checked(updates):
    state = session()
    before = deepcopy(state)

    with pytest.raises(DecisionError):
        parse(decision_payload(updates=updates), state=state)

    assert state == before


def test_quote_from_old_history_cannot_be_attributed_to_current_user_turn():
    state = session()
    apply_user_update(
        state,
        user="월세 계약이에요.",
        updates={"contract_type": {"value": "월세", "evidence": "월세"}},
    )

    with pytest.raises(DecisionError):
        parse(
            decision_payload(updates={"contract_type": {"value": "월세", "evidence": "월세"}}),
            state=state,
            user="다음에 무엇을 준비할까요?",
        )


def test_amount_not_in_the_source_quote_is_not_accepted_as_a_fact():
    with pytest.raises(DecisionError):
        parse(
            decision_payload(updates={"deposit": {"value": "50000000원", "evidence": "보증금은 30000000원"}}),
            user="보증금은 30000000원입니다.",
        )


@pytest.mark.parametrize(
    "field,value,evidence,user",
    [
        ("contract_type", "전세", "전세가 아니라 월세", "정정할게요. 전세가 아니라 월세입니다."),
        ("contract_ended", "예", "계약이 아직 안 끝났어요", "계약이 아직 안 끝났어요."),
        ("contract_ended", "예", "계약은 종료되지 않았어요", "계약은 종료되지 않았어요."),
    ],
)
def test_obvious_negation_or_correction_cannot_be_flipped(field, value, evidence, user):
    with pytest.raises(DecisionError):
        parse(decision_payload(updates={field: {"value": value, "evidence": evidence}}), user=user)


def test_explicit_correction_to_monthly_rent_and_unknown_value_are_accepted():
    corrected = parse(
        decision_payload(
            intent="correction",
            action="social",
            search_query="",
            updates={"contract_type": {"value": "월세", "evidence": "전세가 아니라 월세"}},
        ),
        user="전세가 아니라 월세예요.",
    )
    unknown = parse(
        decision_payload(
            intent="clarification_answer",
            action="clarify",
            clarify_field="details",
            question="확인할 수 있는 계약 내용을 알려주시겠어요?",
            search_query="",
            updates={"contract_ended": {"value": "모름", "evidence": "잘 모르겠어요"}},
        ),
        user="계약 종료 여부는 잘 모르겠어요.",
    )

    assert corrected.updates["contract_type"]["value"] == "월세"
    assert unknown.updates["contract_ended"]["value"] == "모름"


@pytest.mark.parametrize("evidence", [
    "전세 계약이 끝났는데 보증금을 못 받았어요",
    "전세 계약이 끝났지만 보증금을 못 받았어요",
    "전세 계약이 끝났고 보증금을 못 받았어요",
    "전세 계약이 끝났어요. 보증금을 못 받았어요",
])
def test_whole_quote_can_record_ended_contract_and_unpaid_deposit(evidence):
    state = session()
    before = deepcopy(state)
    result = parse(
        decision_payload(action="clarify", search_query="", clarify_field="details", updates={
            "contract_ended": {"value": "예", "evidence": evidence},
            "deposit_returned": {"value": "아니요", "evidence": evidence},
        }),
        state=state, user=evidence,
    )
    assert result.updates["contract_ended"]["value"] == "예"
    assert result.updates["deposit_returned"]["value"] == "아니요"
    assert state == before


def test_unended_contract_does_not_negate_a_separate_returned_deposit_clause():
    evidence = "계약은 끝나지 않았지만 보증금은 돌려받았어요"
    result = parse(
        decision_payload(action="clarify", search_query="", clarify_field="details", updates={
            "contract_ended": {"value": "아니요", "evidence": evidence},
            "deposit_returned": {"value": "예", "evidence": evidence},
        }),
        user=evidence,
    )
    assert result.updates["deposit_returned"]["value"] == "예"


@pytest.mark.parametrize("field,value,evidence", [
    ("contract_ended", "아니요", "계약이 끝났는데 보증금을 못 받았어요"),
    ("deposit_returned", "예", "계약이 끝났는데 보증금을 못 받았어요"),
    ("contract_ended", "예", "계약은 끝나지 않았지만 보증금은 돌려받았어요"),
    ("deposit_returned", "아니요", "계약은 끝나지 않았지만 보증금은 돌려받았어요"),
    ("living_in_property", "아니요", "보증금은 못 받았지만 지금 살고 있어요"),
    ("living_in_property", "아니요", "아직 살고 있어요"),
    ("living_in_property", "예", "보증금을 돌려받았지만 지금 살고 있지 않아요"),
])
def test_other_clause_cannot_hide_an_explicit_polarity_reversal(field, value, evidence):
    with pytest.raises(DecisionError, match="polarity"):
        parse(
            decision_payload(action="clarify", search_query="", clarify_field="details", updates={field: {"value": value, "evidence": evidence}}),
            user=evidence,
        )


@pytest.mark.parametrize("field,value,evidence", [
    ("living_in_property", "예", "보증금은 못 받았지만 지금 살고 있어요"),
    ("living_in_property", "아니요", "보증금을 돌려받았지만 지금 살고 있지 않아요"),
    ("contract_ended", "예", "네."),
    ("contract_ended", "아니요", "아니요."),
    ("contract_ended", "모름", "잘 모르겠어요."),
])
def test_relevant_living_clause_and_short_pending_answers_remain_valid(field, value, evidence):
    result = parse(
        decision_payload(action="clarify", search_query="", clarify_field="details", updates={field: {"value": value, "evidence": evidence}}),
        user=evidence,
    )
    assert result.updates[field]["value"] == value


@pytest.mark.parametrize("value,evidence", [("아니요", "네."), ("아니요", "맞습니다."), ("예", "아니요."), ("예", "아뇨.")])
def test_explicit_short_pending_answer_cannot_be_reversed(value, evidence):
    with pytest.raises(DecisionError, match="polarity"):
        parse(
            decision_payload(action="clarify", search_query="", clarify_field="details", updates={"contract_ended": {"value": value, "evidence": evidence}}),
            user=evidence,
        )


def test_model_input_contains_only_selected_public_context_and_is_a_copy():
    state = session()
    state["expect"] = {"answer": "GOLD_MUST_NOT_BE_SENT"}
    state["evaluation_seed"] = {"secret": "SEED_MUST_NOT_BE_SENT"}
    state["messages"] = [
        {"role": "assistant", "content": "OLD_MESSAGE_MUST_NOT_BE_SENT", "raw_text": "RAW_MUST_NOT_BE_SENT", "context_content": "CONTEXT_MUST_NOT_BE_SENT"},
    ]
    state["documents"] = [{
        "document_id": "doc-owned",
        "kind": "contract",
        "label": "임대차계약서",
        "text": "DOCUMENT_BODY_MUST_NOT_BE_SENT",
        "context": {"chunks": ["DOCUMENT_CHUNKS_MUST_NOT_BE_SENT"]},
        "analysis": {"summary": "DOCUMENT_ANALYSIS_MUST_NOT_BE_SENT"},
    }]
    apply_user_update(
        state,
        user="월세 계약이에요.",
        updates={"contract_type": {"value": "월세", "evidence": "월세"}},
        topic="보증금반환",
    )
    state["dialogue"]["pending"] = {"field": "contract_ended", "question": "계약은 끝났나요?"}
    state["dialogue"]["changes"] = [{"private": "OLD_CHANGE_MUST_NOT_BE_SENT"}]
    record_answer(state, {"status": "answered", "content": "공개 검증 답변", "sources": [{"label": "공개 근거"}]})
    before = deepcopy(state)

    model_input = build_decision_input(state, "네, 끝났어요.", document_id="doc-owned")
    serialized = json.dumps(model_input, ensure_ascii=False)

    for forbidden in (
        "GOLD_MUST_NOT_BE_SENT", "SEED_MUST_NOT_BE_SENT", "RAW_MUST_NOT_BE_SENT",
        "CONTEXT_MUST_NOT_BE_SENT", "DOCUMENT_BODY_MUST_NOT_BE_SENT", "DOCUMENT_CHUNKS_MUST_NOT_BE_SENT",
        "DOCUMENT_ANALYSIS_MUST_NOT_BE_SENT", "OLD_CHANGE_MUST_NOT_BE_SENT", "OLD_MESSAGE_MUST_NOT_BE_SENT",
    ):
        assert forbidden not in serialized
    for required in ("네, 끝났어요.", "월세", "계약은 끝났나요?", "공개 검증 답변", "doc-owned", "임대차계약서"):
        assert required in serialized
    assert state == before
    state["dialogue"]["facts"]["contract_type"]["value"] = "전세"
    state["dialogue"]["pending"]["question"] = "수정한 질문"
    assert json.dumps(model_input, ensure_ascii=False) == serialized


@pytest.mark.parametrize("validation", [None, "failed", "unverified"])
def test_unverified_answer_cache_is_not_included_in_model_context(validation):
    state = session()
    ensure_dialogue(state)
    state["dialogue"]["last_answer"] = {"content": "UNVERIFIED_ANSWER_MUST_NOT_BE_SENT", "sources": [], "validation": validation}

    result = build_decision_input(state, "쉽게 설명해 주세요.")

    assert "UNVERIFIED_ANSWER_MUST_NOT_BE_SENT" not in json.dumps(result)


def test_input_builder_does_not_initialize_or_mutate_old_session():
    state = session()
    before = deepcopy(state)

    build_decision_input(state, "상담을 시작할게요.")

    assert state == before


def test_input_builder_rejects_foreign_document_before_model_call():
    state = session()
    state["documents"] = [{"document_id": "owned", "kind": "contract", "label": "계약서"}]
    before = deepcopy(state)

    with pytest.raises(DecisionError):
        build_decision_input(state, "이 계약서를 봐주세요.", document_id="foreign")

    assert state == before


@pytest.mark.parametrize("field,value,evidence", [
    ("property_type", "보일러", "보일러가 고장났어요"),
    ("property_type", "주택", "집주인입니다"),
    ("property_type", "아파트", "아파트가 아니라 빌라예요"),
    ("role", "임차인", "세입자가 아니라 집주인입니다"),
    ("property_type", "주택", "보일러가 고장났어요"),
    ("role", "임차인", "월세 집 수리가 궁금해요"),
    ("role", "임대인", "세입자예요"),
    ("subject", "제", "보일러가 고장났어요"),
])
def test_optional_fields_require_their_own_meaning_and_source(field, value, evidence):
    with pytest.raises(DecisionError):
        parse(decision_payload(action="social", intent="correction", search_query="",
              updates={field: {"value": value, "evidence": evidence}}), user=evidence)


@pytest.mark.parametrize("field,value,evidence", [
    ("property_type", "주택", "월세 집"),
    ("property_type", "오피스텔", "오피스텔입니다"),
    ("role", "임차인", "저는 세입자예요"),
    ("role", "임대인", "집주인입니다"),
    ("subject", "친구", "친구의 상황이에요"),
])
def test_explicit_optional_fields_and_documented_synonyms_are_accepted(field, value, evidence):
    result = parse(decision_payload(action="social", intent="correction", search_query="",
                   updates={field: {"value": value, "evidence": evidence}}), user=evidence)
    assert result.updates[field]["value"] == value



def test_pending_answer_requires_a_binding_instead_of_an_invented_query_condition():
    state = session()
    dialogue = ensure_dialogue(state)
    dialogue["pending"] = {"field": "contract_ended", "question": "계약이 끝났나요?", "attempts": 1}
    with pytest.raises(DecisionError, match="pending_answer_missing"):
        parse(decision_payload(intent="clarification_answer", updates={}, search_query="계약이 종료되지 않은 경우 보증금 반환"),
              state=state, user="잘 모르겠어요.")
    accepted = parse(decision_payload(intent="clarification_answer", updates={"contract_ended": {"value": "모름", "evidence": "잘 모르겠어요"}},
                     search_query="계약 종료 여부를 모르는 경우 보증금 반환의 일반 원칙"), state=state, user="잘 모르겠어요.")
    assert accepted.updates["contract_ended"]["value"] == "모름"



@pytest.mark.parametrize("query", ["계약이 종료되지 않았고 보증금을 못 받은 경우 반환 절차", "계약이 종료되었고 보증금을 못 받은 경우 반환 절차"])
def test_unknown_fact_cannot_become_a_positive_or_negative_search_condition(query):
    with pytest.raises(DecisionError, match="unknown_query_condition"):
        parse(decision_payload(intent="clarification_answer", search_query=query,
              updates={"contract_ended": {"value": "모름", "evidence": "모르겠어요"}}), user="계약 종료는 모르겠어요.")


def test_question_about_how_to_check_an_unknown_fact_is_not_an_assertion():
    result = parse(decision_payload(intent="clarification_answer", search_query="계약이 끝났는지 확인하는 방법",
                   updates={"contract_ended": {"value": "모름", "evidence": "모르겠어요"}}), user="계약 종료는 모르겠어요.")
    assert result.action == "rag"
