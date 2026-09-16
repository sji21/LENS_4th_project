"""Exercise the real dialogue serializer at the tax selection boundary."""
from copy import deepcopy

import pytest

from chat.dialogue_contract import Decision
from chat.dialogue_query import grounded_query
from chat.dialogue_state import apply_user_update, record_answer
from src.retrieval.multi_evidence import TaxLookupSelector


@pytest.mark.parametrize("origin,topic,prior,current,expected", [
    ("answer", "계약준비", "미납국세 열람 방법은요?",
     "계약 준비 서류가 궁금해요.", "ordinary"),
    ("answer", "계약준비", "미납국세 열람 방법은요?",
     "미납지방세 열람 방법은요?", "local"),
    ("answer", "계약준비", "이전 대화: 세금 상담\n사용자 질문: 미납국세 열람 방법은요?",
     "미납지방세 열람 방법은요?", "local"),
    ("answer", "계약준비", "대화 주제: 계약준비\n사용자 입력: 미납국세 열람 방법은요?",
     "계약 준비 서류가 궁금해요.", "ordinary"),
    ("answer", "계약갱신", "갱신 절차가 궁금해요.",
     "미납국세 열람 방법은요?", "national"),
    ("pending", "계약준비", "미납국세 열람 방법은요?",
     "전세 계약입니다.", "ordinary"),
    ("pending", "계약준비", "미납국세 열람 방법은요?",
     "미납지방세 열람 방법은요?", "local"),
])
def test_serialized_dialogue_selects_only_explicit_current_tax_scope(
    origin, topic, prior, current, expected,
):
    state = {"messages": [], "documents": []}
    dialogue = apply_user_update(state, user=prior, topic=topic)
    if origin == "answer":
        record_answer(state, {"status": "answered", "content": "검증된 답변"},
                      query=prior, request=prior)
        intent = "followup"
        prior_label = "직전 답변 질문:"
    else:
        dialogue["pending"] = {"request": prior, "epoch": dialogue["epoch"],
                               "topic": topic, "document_id": None}
        intent = "clarification_answer"
        prior_label = "이어서 상담할 사용자 질문:"
    decision = Decision(intent=intent, action="rag", topic=topic,
                        topic_changed=False, updates={}, clarify_field=None,
                        question=None, search_query="", document_id=None,
                        style="standard")
    before = deepcopy(state)
    query = grounded_query(state, current, decision)
    assert prior_label in query and query.endswith("사용자 입력: " + current)

    chunks = [
        {"chunk_id": "ordinary", "text": "계약 준비 일반 서류", "metadata": {}},
        {"chunk_id": "national", "text": "미납국세 열람 신청", "metadata": {}},
        {"chunk_id": "local", "text": "미납지방세 열람 신청", "metadata": {}},
    ]
    ranked = [("ordinary", .3), ("national", .2), ("local", .1)]
    selected = TaxLookupSelector(chunks).select(query, ranked, 1)

    assert selected == [(expected, dict(ranked)[expected])]
    assert state == before
