"""Bounded, code-owned clarification questions over attributed user state."""
from copy import deepcopy
from dataclasses import asdict, replace
import json

from .dialogue_contract import BOOL_FIELDS, FACT_FIELDS, Decision, parse_decision, pending_request
from .dialogue_query import grounded_query
from .dialogue_state import apply_user_update, ensure_dialogue

MAX_ASKS = 2
QUESTIONS = {
    "contract_type": "계약 형태가 전세, 월세, 반전세 중 무엇인가요?",
    "contract_ended": "계약 기간이 이미 끝났나요?",
    "deposit_returned": "보증금을 돌려받으셨나요?",
    "living_in_property": "현재 그 집에 거주하고 계신가요?",
    "moved_out": "그 집에서 이미 이사하셨나요?",
    "landlord_notified": "임대인에게 의사를 알리셨나요?",
    "subject": "누구의 계약에 관한 상담인가요?",
    "role": "이 계약에서 임차인, 임대인, 중개사, 대리인 중 어떤 입장이신가요?",
    "property_type": "주택이나 건물의 종류를 알려주시겠어요?",
    "deposit": "계약서에 적힌 보증금은 얼마인가요?",
    "monthly_rent": "계약서에 적힌 월세는 얼마인가요?",
    "start_date": "계약 시작일을 알려주시겠어요?",
    "end_date": "계약 종료일을 알려주시겠어요?",
    "notice_date": "임대인에게 의사를 알린 날짜를 알려주시겠어요?",
    "document": "어느 문서를 확인할까요? 질문할 문서를 선택해 주세요.",
    "details": "어떤 부분이 궁금한지 조금 더 설명해 주시겠어요?",
}


def choices_for(field, documents=()):
    if field == "document":
        return [{"label": doc.get("filename", doc.get("label", "문서")),
                 "message": "선택한 문서에 대한 질문을 이어서 답해주세요.",
                 "document_id": doc["document_id"]} for doc in documents]
    values = ["예", "아니요"] if field in BOOL_FIELDS else []
    if field == "contract_type":
        values = ["전세", "월세", "반전세"]
    return [{"label": value, "message": value} for value in values + ["모르겠어요"]]


def _validated(state, user, decision):
    decision = replace(decision, search_query=grounded_query(state, user, decision))
    return parse_decision(json.dumps(asdict(decision), ensure_ascii=False),
                          state=state, user=user, preserved_user=True)


def short_answer_decision(state, user):
    """Interpret exact offered values only; free-form language still uses Qwen."""
    dialogue = ensure_dialogue(deepcopy(state))
    pending = dialogue["pending"]
    if not pending or not isinstance(pending.get("field"), str) or pending["field"] not in QUESTIONS:
        return None
    field = pending["field"]
    if field in {"details", "document", "subject"}:
        return None
    answer = user.strip().rstrip(".!。!")
    value = None
    if answer == "모르겠어요":
        value = "모름"
    elif field in BOOL_FIELDS:
        value = {"네": "예", "예": "예", "아니요": "아니요"}.get(answer)
    elif field == "contract_type" and answer in {"전세", "월세", "반전세"}:
        value = answer
    if value is None:
        return None
    decision = Decision("clarification_answer", "rag", dialogue["topic"], False,
                        {field: {"value": value, "evidence": user}}, None, None,
                        user, dialogue["active_document_id"], "standard")
    return _validated(state, user, decision)


def prepare_clarification(state, user, decision):
    """Return an executable decision and optional pending question, without writes."""
    after_answer = decision.action == "rag" and decision.clarify_field in FACT_FIELDS and decision.intent not in {"explain", "greeting"}
    if decision.action != "clarify" and not after_answer:
        return decision, None
    trial = deepcopy(state)
    dialogue = apply_user_update(trial, user=user, updates=decision.updates,
                                 topic=decision.topic, topic_changed=decision.topic_changed,
                                 document_id=decision.document_id)
    field = decision.clarify_field
    previous = dialogue["pending"] or {}
    count = dialogue["clarification_counts"].get(field, 0)
    if previous.get("field") == field:
        attempts = previous.get("attempts", 0)
        count = max(count, attempts if type(attempts) is int else 0)
    if field in dialogue["facts"] or count >= MAX_ASKS:
        decision = replace(decision, action="rag", clarify_field=None, question=None)
        return _validated(state, user, decision), None
    question = QUESTIONS[field]
    pending = {"field": field, "question": question, "attempts": count + 1,
               "choices": choices_for(field, trial.get("documents", []))}
    pending.update(request=pending_request(dialogue, decision.intent) or user,
                   epoch=dialogue["epoch"], topic=dialogue["topic"],
                   document_id=dialogue["active_document_id"])
    if after_answer:
        pending["mode"] = "after_answer"
    if field == "document":
        for choice in pending["choices"]:
            choice["message"] = user
    return replace(decision, question=question), pending


def answer_reason(answer):
    from src.generation.prompt import GENERATION_FAILED_TEXT
    if answer.status == "answered":
        return "answered"
    if answer.status == "refused":
        return "request_refused"
    if answer.validation_mode != "not_applicable":
        return "validation_failed"
    if answer.text.startswith(GENERATION_FAILED_TEXT):
        return "generation_failed"
    return "no_evidence"
