"""Serialize validated conversation state without another model or paraphrase."""
from copy import deepcopy

from .dialogue_contract import DecisionError, FACT_FIELDS, previous_answer_query, pending_request
from .dialogue_state import apply_user_update


FACT_LABELS = {
    "subject": "상담 대상", "contract_type": "계약 유형", "role": "사용자 역할",
    "property_type": "건물 유형", "deposit": "보증금", "monthly_rent": "월세",
    "start_date": "계약 시작일", "end_date": "계약 종료일", "notice_date": "통지일",
    "contract_ended": "계약 종료 여부", "deposit_returned": "보증금 반환 여부",
    "living_in_property": "현재 거주 여부", "moved_out": "퇴거 여부",
    "landlord_notified": "임대인 통지 여부",
}
DOCUMENT_LABELS = {"contract": "임대차계약서", "registry": "등기부등본"}
QUERY_LIMIT = 2000


def grounded_query(state, user, decision, *, document_context=True):
    """Build a query from a validated Decision without changing its session.

    Literal values are user statements, not verified legal facts. This adapter
    does not interpret the current input or refine retrieval; the strict caller
    still validates the resulting decision before any action executes.
    """
    if decision.action != "rag":
        return ""
    trial = deepcopy(state)
    try:
        dialogue = apply_user_update(
            trial, user=user, updates=decision.updates, topic=decision.topic,
            topic_changed=decision.topic_changed, document_id=decision.document_id,
        )
    except ValueError:
        raise DecisionError("provenance_or_document") from None

    # Keep current input separate from the preceding request and case facts.
    # Only one preceding request is included, never a recursively nested query.
    parts = []
    active_id = dialogue["active_document_id"]
    selected = next((doc for doc in trial.get("documents", []) if active_id and doc.get("document_id") == active_id), None)
    if document_context and selected and isinstance(selected.get("kind"), str):
        label = DOCUMENT_LABELS.get(selected["kind"])
        if label:
            parts.append(f"선택 문서: {label}")
    if dialogue["topic"]:
        parts.append(f"대화 주제: {dialogue['topic']}")
    for field, label in FACT_LABELS.items():
        fact = dialogue["facts"].get(field)
        if field in FACT_FIELDS and fact and fact.get("source") == "user_statement":
            parts.append(f"{label}: {fact['value']}")
    prior_query = previous_answer_query(dialogue, decision.intent)
    if prior_query:
        parts.append(f"직전 답변 질문: {prior_query}")
    request = pending_request(dialogue, decision.intent)
    if request:
        parts.append(f"이어서 상담할 사용자 질문: {request}")
    parts.append(f"사용자 입력: {user}")
    query = "\n".join(parts)
    if len(query) > QUERY_LIMIT:
        raise DecisionError("query_context_limit")
    return query
