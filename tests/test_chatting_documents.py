from dataclasses import replace
from copy import deepcopy
import pytest
from chat.services import initial_state
from chat.dialogue_documents import select_document, retain_followup_topic
from chat.dialogue_contract import Decision
from chat.dialogue_query import grounded_query
from chat.dialogue_state import apply_user_update


def state():
    data = initial_state()
    data["documents"] = [
        {"document_id": "a", "kind": "contract", "filename": "one.pdf"},
        {"document_id": "b", "kind": "contract", "filename": "two.pdf"},
        {"document_id": "c", "kind": "registry", "filename": "registry.pdf"},
    ]
    return data


@pytest.mark.parametrize("text,expected", [("두 번째 문서", "b"), ("1번 문서", "a"), ("올린 등기를 설명해주세요", "c"), ("two.pdf 내용을 알려줘", "b")])
def test_owned_metadata_resolves_explicit_reference(text, expected):
    assert select_document(state(), text) == (expected, False)


def test_multiple_contracts_without_active_choice_require_clarification():
    assert select_document(state(), "이 계약서를 설명해주세요") == (None, True)
    assert select_document(state(), "5번 문서") == (None, True)


def test_active_contract_continues_but_does_not_hijack_new_general_question():
    data = state()
    data["dialogue"]["active_document_id"] = "b"
    assert select_document(data, "그 부분도 설명해주세요", continue_document=True) == ("b", False)
    assert select_document(data, "대항력의 의미를 알려주세요") == (None, False)
    assert select_document(data, "올린 등기의 내용을 알려주세요") == ("c", False)
    assert data["dialogue"]["active_document_id"] == "b"


def test_foreign_document_id_is_never_used():
    with pytest.raises(ValueError):
        select_document(state(), "설명해주세요", "another-session")


def test_general_question_does_not_add_document_label_or_ocr_facts():
    data = state()
    data["dialogue"]["active_document_id"] = "b"
    data["documents"][1]["context"] = {"amount": "999999999원"}
    decision = Decision("question", "rag", "대항력", False, {}, None, None, "", None, "standard")
    query = grounded_query(data, "대항력을 설명해줘", decision, document_context=False)
    assert "문서:" not in query and "999999999" not in query


def test_correction_keeps_latest_literal_values_and_entire_user_condition():
    data = initial_state()
    apply_user_update(data, user="전세 보증금은 1억5천만원", topic="보증금반환", updates={"contract_type": {"value": "전세", "evidence": "전세"}, "deposit": {"value": "1억5천만원", "evidence": "1억5천만원"}})
    user = "전세가 아니라 월세예요. 만일 이사일이 달라지면요?"
    decision = Decision("correction", "rag", "계약준비", False, {"contract_type": {"value": "월세", "evidence": "월세"}}, None, None, "", None, "standard")
    decision = retain_followup_topic(data, decision)
    query = grounded_query(data, user, decision)
    assert "계약 유형: 월세" in query and "계약 유형: 전세" not in query
    assert "1억5천만원" in query and query.endswith(user)
    assert decision.topic == "보증금반환"
    assert data["dialogue"]["facts"]["contract_type"]["value"] == "전세"


def test_generic_contract_form_question_does_not_force_a_document_choice():
    assert select_document(state(), "임대차계약서 작성 방법을 일반적으로 알려주세요") == (None, False)
