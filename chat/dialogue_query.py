"""Serialize validated conversation state without another model or paraphrase."""
from copy import deepcopy
import re

from .dialogue_contract import DecisionError, FACT_FIELDS, previous_answer_query, pending_request
from .dialogue_state import apply_user_update
from .dialogue_documents import registry_review_request


FACT_LABELS = {
    "subject": "상담 대상", "contract_type": "계약 유형", "role": "사용자 역할",
    "property_type": "건물 유형", "deposit": "보증금", "monthly_rent": "월세",
    "start_date": "계약 시작일", "end_date": "계약 종료일", "notice_date": "통지일",
    "contract_ended": "계약 종료 여부", "deposit_returned": "보증금 반환 여부",
    "living_in_property": "현재 거주 여부", "moved_out": "퇴거 여부",
    "landlord_notified": "임대인 통지 여부", "contract_signed": "계약 체결 여부",
}
DOCUMENT_LABELS = {"contract": "임대차계약서", "registry": "등기부등본"}
QUERY_LIMIT = 2000
PURPOSE_LABELS = {
    "procedure": "진행 절차: 요청 시기, 전달 방법, 실행 순서, 확인 항목",
    "timing": "시기와 기한 및 적용 조건",
    "eligibility": "이번 질문에서 묻는 구체적인 행동이나 방법의 가능 여부와 조건",
    "definition": "개념의 의미와 차이",
    "documents": "필요한 서류와 준비 항목",
    "source": "직전 설명의 근거와 출처",
    "summary": "직전 설명의 요약 또는 쉬운 설명",
}


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
    if decision.purpose in PURPOSE_LABELS:
        parts.append(f"질문 목적: {PURPOSE_LABELS[decision.purpose]}")
    for field, label in FACT_LABELS.items():
        fact = dialogue["facts"].get(field)
        if field in FACT_FIELDS and fact and fact.get("source") == "user_statement":
            parts.append(f"{label}: {fact['value']}")
    review = bool(document_context and selected and selected.get("kind") == "registry" and registry_review_request(user))
    prior_query = previous_answer_query(dialogue, decision.intent)
    if prior_query and not review:
        parts.append(f"직전 답변 질문: {prior_query}")
    request = pending_request(dialogue, decision.intent)
    if request and not review:
        parts.append(f"이어서 상담할 사용자 질문: {request}")
    parts.append(f"사용자 입력: {user}")
    query = "\n".join(parts)
    if len(query) > QUERY_LIMIT:
        raise DecisionError("query_context_limit")
    return query


def registry_review_query(query, user):
    """Normalize only after validating the original user and all fact quotes."""
    suffix = f"사용자 입력: {user}"
    if not query.endswith(suffix):
        raise DecisionError("preserved_user_suffix")
    # Keep amounts, conditions and additional questions; rewrite the verdict
    # wording only, never discard the rest of the user's request.
    review_text = re.sub(r"계약\s*(?:해도|하면)\s*(?:괜찮(?:을까요|을까|나요|아)|될까요|될까|되나요|되|될|안\s*되나요|안\s*될까요)?", "계약 전 확인사항", user)
    review_text = re.sub(r"안전(?:할까요|할까|한가요|한가|한지)|위험(?:할까요|할까|한가요|한가|한지)", "확인할 위험요소", review_text)
    return query[:-len(suffix)] + f"검토 요청: {review_text}\n" + "사용자 요청: 첨부 등기부등본에 기재된 위험요소와 계약 전 확인사항을 페이지 근거와 함께 설명해 주세요. 말소 여부나 현재 효력이 불분명한 표시는 확인 대상으로 구분해 주세요."
