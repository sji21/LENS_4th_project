"""Grounded queries retain current conditions and exclude private memory."""
from copy import deepcopy
from dataclasses import replace

import pytest

from chat.dialogue_contract import Decision, DecisionError
from chat.dialogue_query import grounded_query
from chat.dialogue_state import apply_user_update


def session():
    return {"messages": [], "documents": []}


def decision(**changes):
    original = Decision(
        intent="followup", action="rag", topic=None, topic_changed=False,
        updates={}, clarify_field=None, question=None, search_query="모델 검색문은 사용하지 않음",
        document_id=None, style="standard",
    )
    return replace(original, **changes)


def fact(state, field, value, *, evidence=None, topic=None):
    evidence = value if evidence is None else evidence
    apply_user_update(state, user=evidence, updates={field: {"value": value, "evidence": evidence}}, topic=topic)


def test_full_current_input_and_literal_current_facts_are_preserved():
    state = session()
    fact(state, "contract_type", "전세", topic="보증금반환")
    fact(state, "deposit", "1억 5천만원")
    fact(state, "end_date", "내년 3월 말")
    user = "계약 종료 전에도 가능한가요?\n단, 보증금을 받은 경우는 제외해 주세요."
    before = deepcopy(state)

    query = grounded_query(state, user, decision())

    assert "계약 유형: 전세" in query
    assert "보증금: 1억 5천만원" in query
    assert "계약 종료일: 내년 3월 말" in query
    assert "대화 주제: 보증금반환" in query
    assert query.endswith(user)
    assert user in query
    assert "150000000" not in query
    assert "2027" not in query
    assert "모델 검색문" not in query
    assert state == before


def test_latest_correction_replaces_the_previous_value_in_query_only():
    state = session()
    fact(state, "contract_type", "전세", topic="보증금반환")
    before = deepcopy(state)
    user = "잘못 말했어요. 월세 계약입니다."
    proposal = decision(intent="correction", updates={"contract_type": {"value": "월세", "evidence": "월세"}})

    query = grounded_query(state, user, proposal)

    assert "계약 유형: 월세" in query
    assert "전세" not in query
    assert state == before


def test_topic_change_excludes_old_facts_and_old_active_document():
    state = session()
    state["documents"] = [{"document_id": "old", "kind": "registry"}]
    apply_user_update(state, user="누나의 전세 보증금 8천만원", topic="보증금반환", document_id="old",
                      updates={"subject": {"value": "누나", "evidence": "누나"},
                               "deposit": {"value": "8천만원", "evidence": "8천만원"}})
    before = deepcopy(state)
    user = "이제 친구의 월세 계약 준비를 물어볼게요."
    proposal = decision(intent="topic_change", topic="계약준비", topic_changed=True,
                        updates={"subject": {"value": "친구", "evidence": "친구"},
                                 "contract_type": {"value": "월세", "evidence": "월세"}})

    query = grounded_query(state, user, proposal)

    assert "상담 대상: 친구" in query
    assert "계약 유형: 월세" in query
    assert "계약준비" in query
    for old_text in ("누나", "8천만원", "보증금반환", "등기부등본"):
        assert old_text not in query
    assert state == before


def test_unknown_is_retained_as_unknown_without_turning_it_into_a_negative():
    state = session()
    fact(state, "contract_ended", "모름", evidence="종료 여부는 모름")
    query = grounded_query(state, "일반적인 절차를 알려주세요.", decision())
    assert "계약 종료 여부: 모름" in query
    assert "아니요" not in query
    assert "계약이 끝나지" not in query


@pytest.mark.parametrize("field,label,value", [
    ("contract_ended", "계약 종료 여부", "예"),
    ("deposit_returned", "보증금 반환 여부", "아니요"),
    ("living_in_property", "현재 거주 여부", "모름"),
    ("moved_out", "퇴거 여부", "아니요"),
    ("landlord_notified", "임대인 통지 여부", "예"),
])
def test_boolean_values_are_literal_and_not_rewritten(field, label, value):
    state = session()
    fact(state, field, value)
    query = grounded_query(state, "확인할 내용을 알려주세요.", decision())
    assert f"{label}: {value}" in query


def test_unknown_fields_provenance_and_private_session_content_are_excluded():
    state = session()
    fact(state, "contract_type", "전세", evidence="비공개 인용 전세")
    fact(state, "internal_note", "PRIVATE_UNKNOWN_FIELD")
    state["dialogue"]["last_answer"] = {"content": "PRIVATE_ANSWER", "raw_text": "PRIVATE_OCR"}
    state["dialogue"]["pending"] = {"question": "PRIVATE_PENDING"}
    state["dialogue"]["changes"] = [{"previous": "PRIVATE_CHANGE"}]
    state["messages"] = [{"role": "assistant", "content": "PRIVATE_HISTORY"}]
    state["evaluation_seed"] = {"expected": "PRIVATE_GOLD"}

    query = grounded_query(state, "절차를 알려주세요.", decision())

    assert "계약 유형: 전세" in query
    for private in ("비공개 인용", "PRIVATE_", "source_turn", "user_statement", "certainty", "internal_note"):
        assert private not in query


def test_document_source_is_never_output_as_a_user_fact():
    state = session()
    fact(state, "contract_type", "전세")
    state["dialogue"]["facts"]["contract_type"]["source"] = "document"
    assert "전세" not in grounded_query(state, "절차를 알려주세요.", decision())


@pytest.mark.parametrize("kind,label", [("contract", "임대차계약서"), ("registry", "등기부등본")])
def test_only_owned_document_kind_is_rendered_with_a_fixed_prefix(kind, label):
    state = session()
    state["documents"] = [{"document_id": "owned-private-id", "kind": kind, "label": "PRIVATE_FILENAME",
                           "raw_text": "PRIVATE_OCR", "context_content": "PRIVATE_CONTENT"}]
    before = deepcopy(state)
    query = grounded_query(state, "이 문서에서 확인할 내용을 알려주세요.", decision(document_id="owned-private-id"))
    assert query.startswith(f"선택 문서: {label}\n")
    for private in ("PRIVATE_", "owned-private-id", "raw_text", "context_content"):
        assert private not in query
    assert state == before


def test_existing_owned_active_document_is_used_when_selection_is_unchanged():
    state = session()
    state["documents"] = [{"document_id": "owned", "kind": "contract"}]
    apply_user_update(state, user="문서 질문입니다.", document_id="owned")
    assert grounded_query(state, "다시 설명해주세요.", decision()).startswith("선택 문서: 임대차계약서\n")


def test_explicit_document_clear_removes_the_existing_document_prefix():
    state = session()
    state["documents"] = [{"document_id": "owned", "kind": "registry"}]
    apply_user_update(state, user="문서 질문입니다.", document_id="owned")
    query = grounded_query(state, "일반적인 절차를 알려주세요.", decision(document_id=""))
    assert "선택 문서" not in query


def test_foreign_document_selection_fails_without_changing_state():
    state = session()
    before = deepcopy(state)
    with pytest.raises(DecisionError, match="provenance_or_document"):
        grounded_query(state, "문서를 설명해주세요.", decision(document_id="someone-elses"))
    assert state == before


def test_unsupported_document_kind_does_not_inject_its_label_or_content():
    state = session()
    state["documents"] = [{"document_id": "owned", "kind": "PRIVATE_KIND", "label": "PRIVATE_LABEL"}]
    query = grounded_query(state, "내용을 알려주세요.", decision(document_id="owned"))
    assert "PRIVATE_" not in query
    assert "선택 문서" not in query


@pytest.mark.parametrize("action", ["social", "clarify", "refuse"])
def test_non_rag_returns_empty_without_applying_any_state(action):
    state = session()
    before = deepcopy(state)
    assert grounded_query(state, "문의", decision(action=action)) == ""
    assert state == before


def test_exact_limit_preserves_whole_input_and_one_more_character_fails():
    state = session()
    prefix_size = len(grounded_query(state, "가", decision())) - 1
    user = "가" * (2000 - prefix_size)
    query = grounded_query(state, user, decision())
    assert len(query) == 2000
    assert query.endswith(user)
    with pytest.raises(DecisionError, match="query_context_limit"):
        grounded_query(state, user + "나", decision())


def test_combined_context_overflow_fails_instead_of_dropping_user_conditions():
    state = session()
    fact(state, "subject", "상담대상" * 35)
    before = deepcopy(state)
    user = "설명" * 940 + " 계약이 끝나지 않은 경우에 한해서 알려주세요."
    assert len(user) <= 2000
    with pytest.raises(DecisionError, match="query_context_limit"):
        grounded_query(state, user, decision())
    assert state == before


def test_invalid_new_source_quote_cannot_enter_query():
    state = session()
    before = deepcopy(state)
    with pytest.raises(DecisionError, match="provenance_or_document"):
        grounded_query(state, "절차를 알려주세요.", decision(updates={"deposit": {"value": "3억원", "evidence": "3억원"}}))
    assert state == before


def test_legacy_messages_are_not_reconstructed_as_current_facts():
    state = session()
    state["messages"] = [{"role": "user", "content": "전세 보증금 9억원"}]
    query = grounded_query(state, "준비물을 알려주세요.", decision())
    assert "전세" not in query
    assert "9억원" not in query
    assert "dialogue" not in state
