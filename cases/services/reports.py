from __future__ import annotations

import json
import re
import time

from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import Max

from cases.models import CaseFact, Report
from src.generation.llm import get_llm, strip_reasoning

REPORT_KEYS = (
    "case_summary", "user_interests", "questions_and_answers", "confirmed_items",
    "unresolved_items", "next_checks", "source_refs",
)


def _text(value) -> str:
    """Turn an LLM field into safe, human-readable report text."""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _question_answer_text(value) -> str:
    if isinstance(value, dict):
        question = _text(value.get("question") or value.get("질문"))
        answer = _text(value.get("answer") or value.get("답변") or value.get("확인한 내용"))
        if question and answer:
            return f"질문: {question}\n확인한 내용: {answer}"
        return question or answer
    return _text(value)


def normalize_report_content(content):
    """Keep saved report snapshots presentable even when an LLM returned objects.

    Earlier report snapshots permit list members to be JSON objects.  They are
    valid JSON but must never be rendered with Python dictionary syntax.
    """
    payload = content if isinstance(content, dict) else {}
    normalized = {"case_summary": _text(payload.get("case_summary"))}
    for key in REPORT_KEYS[1:]:
        values = payload.get(key, [])
        if not isinstance(values, list):
            values = []
        if key == "questions_and_answers":
            normalized[key] = [text for value in values if (text := _question_answer_text(value))]
        else:
            normalized[key] = [text for value in values if (text := _text(value))]
    return normalized


def _source_snapshot(case, conversations):
    dialogue = []
    sources = []
    for conversation in conversations:
        pending_question = None
        for message in conversation.state.get("messages", []):
            role = message.get("role")
            if role == "user":
                pending_question = message.get("content", "")[:1200]
                continue
            if role != "assistant":
                continue

            answer_sources = message.get("sources", [])
            if (
                pending_question
                and message.get("status") == "answered"
                and answer_sources
            ):
                # A report is a consultation summary, not a raw chat export.
                # Greetings, test prompts, refusals, and unsupported replies do
                # not have grounded sources and must not become report topics.
                dialogue.append({"role": "user", "content": pending_question})
                dialogue.append({
                    "role": "assistant",
                    "content": (message.get("context_content") or message.get("content", ""))[:1600],
                })
                sources.extend(answer_sources)
            elif message.get("status") == "answered" and answer_sources:
                # Older saved conversations can contain a verified assistant
                # message without its preceding user turn. Preserve its source
                # context for LLM summaries, but do not invent a user topic.
                dialogue.append({
                    "role": "assistant",
                    "content": (message.get("context_content") or message.get("content", ""))[:1600],
                })
                sources.extend(answer_sources)
            pending_question = None
    facts = [{
        "key": f.key, "value": f.value_json, "status": f.status,
        "source_type": f.source_type, "source_ref": f.source_ref, "source_label": f.source_label,
    } for f in case.facts.exclude(status=CaseFact.Status.INVALIDATED)]
    deduped = list({(s.get("doc_type", ""), s.get("label", ""), s.get("url", "")): s for s in sources}.values())
    return {
        "case": {"id": str(case.pk), "title": case.title},
        "dialogue": dialogue[-30:],
        "facts": facts,
        "sources": deduped,
    }


def _fallback(snapshot):
    confirmed = [f for f in snapshot["facts"] if f["status"] == CaseFact.Status.ACTIVE]
    questions, pairs = [], []
    pending_question = None
    for message in snapshot["dialogue"]:
        if message["role"] == "user":
            if pending_question:
                pairs.append(f"질문: {pending_question} / 확인한 내용: 답변이 확인되지 않았습니다.")
            pending_question = message["content"][:500]
            questions.append(pending_question)
        elif pending_question:
            pairs.append(f"질문: {pending_question} / 확인한 내용: {message['content'][:500]}")
            pending_question = None
    if pending_question:
        pairs.append(f"질문: {pending_question} / 확인한 내용: 답변이 확인되지 않았습니다.")
    unresolved = [
        f"출처 간 값 확인 필요: {f['key']}"
        for f in snapshot["facts"] if f["status"] == CaseFact.Status.CONFLICT
    ]
    return {
        # The room title is a navigation label, not a contract fact.
        "case_summary": (
            f"확인된 계약 정보 {len(confirmed)}건이 있습니다."
            if confirmed else "등록된 계약 정보가 없습니다."
        ),
        "user_interests": questions[-8:],
        "questions_and_answers": pairs[-8:],
        "confirmed_items": [f"{f['key']}: {f['value']}" for f in confirmed],
        "unresolved_items": unresolved,
        "next_checks": unresolved.copy(),
        "source_refs": [s.get("label", "") for s in snapshot["sources"] if s.get("label")],
    }


def _parse_json(text):
    cleaned = strip_reasoning(text)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("Report model did not return JSON")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict) or any(key not in payload for key in REPORT_KEYS):
        raise ValueError("Report JSON schema mismatch")
    for key in REPORT_KEYS[1:]:
        if not isinstance(payload[key], list):
            raise ValueError("Report list field mismatch")
    return normalize_report_content(payload)


def build_report_content(snapshot, llm=None):
    prompt = (
        "당신은 임대차 상담 리포트 작성기입니다. 이 채팅방에서 사용자가 궁금해한 내용을 중심으로 질문과 검증된 답변을 정리하세요. "
        "리포트 독자가 바로 확인하고 행동할 수 있게 핵심 결론과 next_checks를 분명히 쓰세요. "
        "사용자가 추가로 알아보거나 확인해야 할 행동은 next_checks에 구체적으로 작성하세요. "
        "새로운 법률 판단을 만들지 말고 제공된 대화, 확인된 사실, 출처만 사용하세요. "
        "충돌하거나 답을 확인하지 못한 내용은 unresolved_items에 넣으세요. 모든 목록 항목은 짧은 문자열로 작성하세요. "
        f"다음 키를 모두 가진 JSON 객체만 출력하세요: {', '.join(REPORT_KEYS)}.\n입력:\n"
        + json.dumps(snapshot, ensure_ascii=False)[:24000]
    )
    model = llm or get_llm(max_tokens=900)
    response = model.invoke(prompt)
    return _parse_json(getattr(response, "content", str(response)))


def generate_report(case, *, llm=None):
    from cases.models import ContractCase
    conversations = list(case.conversations.order_by("created_at"))
    snapshot = _source_snapshot(case, conversations)
    mode = "llm"
    try:
        content = build_report_content(snapshot, llm=llm)
    except Exception:
        content = _fallback(snapshot)
        mode = "template_fallback"
    for attempt in range(5):
        try:
            with transaction.atomic():
                locked_case = ContractCase.objects.select_for_update().get(pk=case.pk)
                version = (locked_case.reports.aggregate(value=Max("version"))["value"] or 0) + 1
                return Report.objects.create(
                    case=locked_case,
                    version=version,
                    content_json=content,
                    source_snapshot=snapshot,
                    generation_mode=mode,
                )
        except IntegrityError:
            if attempt == 4:
                raise
        except OperationalError as error:
            if connection.vendor != "sqlite" or "locked" not in str(error).lower() or attempt == 4:
                raise
        time.sleep(0.04 * (attempt + 1))
