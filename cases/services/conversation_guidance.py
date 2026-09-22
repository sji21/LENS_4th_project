from __future__ import annotations

from datetime import datetime, time
from hashlib import sha256
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from threading import Lock

from django.db import close_old_connections, transaction
from django.db.models import F
from django.utils import timezone

from cases.models import ChecklistItem, ScheduleEvent
from src.generation.llm import get_llm, strip_reasoning

logger = logging.getLogger(__name__)
MAX_MESSAGES = 30
MAX_ITEMS = 8
_GUIDANCE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lens-guidance")
_GUIDANCE_LOCKS = defaultdict(Lock)


def _messages_digest(messages):
    payload = json.dumps(messages or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def conversation_messages(case, messages=None):
    """Return the selected chat room's user questions and verified answers."""
    if messages is None:
        conversation = case.conversations.order_by("created_at").first()
        messages = conversation.state.get("messages", []) if conversation else []
    selected = []
    for message in messages[-MAX_MESSAGES:]:
        if message.get("context_excluded"):
            continue
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        if role == "assistant" and message.get("status") != "answered":
            continue
        content = (message.get("context_content") or message.get("content") or "").strip()
        if content:
            selected.append({"id": str(message.get("id", ""))[:32], "role": role, "content": content[:1200]})
    return selected


def _parse_payload(text):
    match = re.search(r"\{.*\}", strip_reasoning(text), re.DOTALL)
    if not match:
        raise ValueError("Guidance model did not return JSON")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("Guidance JSON must be an object")
    checklist = payload.get("checklist", [])
    calendar = payload.get("calendar", [])
    if not isinstance(checklist, list) or not isinstance(calendar, list):
        raise ValueError("Guidance lists are missing")
    return checklist[:MAX_ITEMS], calendar[:MAX_ITEMS]


def _stable_code(prefix, item):
    raw = re.sub(r"[^a-z0-9_]+", "_", str(item.get("code", "")).strip().lower()).strip("_")
    if not raw:
        raw = sha256(str(item.get("title", "")).strip().encode("utf-8")).hexdigest()[:16]
    return f"{prefix}{raw[:60]}"


def _priority(value, default=50):
    try:
        return max(1, min(int(value), 100))
    except (TypeError, ValueError):
        return default


def _calendar_datetime(value):
    try:
        day = datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return timezone.make_aware(datetime.combine(day, time(hour=9)))


def _calendar_from_dated_checklists(checklist, calendar):
    """Keep a dated follow-up action visible in both checklist and calendar."""
    merged = [item for item in calendar if isinstance(item, dict)]
    signatures = {
        (str(item.get("date", "")), str(item.get("title", "")).strip())
        for item in merged
    }
    for item in checklist:
        if not isinstance(item, dict):
            continue
        date_value = item.get("date") or item.get("due_date")
        if not date_value:
            text = f'{item.get("title", "")} {item.get("description", "")}'
            match = re.search(r"20\d{2}-\d{1,2}-\d{1,2}", text)
            date_value = match.group(0) if match else None
        if _calendar_datetime(date_value) is None:
            continue
        normalized_date = _calendar_datetime(date_value).date().isoformat()
        title = str(item.get("title", "")).strip()
        if not title or (normalized_date, title) in signatures:
            continue
        merged.append({
            "code": f'checklist_{item.get("code", "follow_up")}',
            "date": normalized_date,
            "title": title,
            "description": str(item.get("description", "")).strip(),
        })
        signatures.add((normalized_date, title))
    return merged


@transaction.atomic
def _sync_checklist(case, items):
    active_codes = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()[:240]
        description = str(item.get("description", "")).strip()[:1200]
        if not title:
            continue
        code = _stable_code("llm_", item)
        active_codes.append(code)
        row, _ = ChecklistItem.objects.get_or_create(
            case=case,
            code=code,
            defaults={"title": title, "description": description, "priority": _priority(item.get("priority"))},
        )
        changed = False
        for field, value in {
            "title": title,
            "description": description,
            "priority": _priority(item.get("priority")),
            "auto_completed": False,
        }.items():
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        # DISMISSED is used when an LLM item temporarily disappears. If the
        # same stable item returns, show it again without overwriting DONE.
        if row.state == ChecklistItem.State.DISMISSED:
            row.state = ChecklistItem.State.TODO
            changed = True
        if changed:
            row.save()
    case.checklist_items.filter(
        code__startswith="llm_", state=ChecklistItem.State.TODO,
    ).exclude(code__in=active_codes).update(state=ChecklistItem.State.DISMISSED)


@transaction.atomic
def _sync_calendar(case, items):
    active = []
    for item in items:
        if not isinstance(item, dict):
            continue
        starts_at = _calendar_datetime(item.get("date"))
        title = str(item.get("title", "")).strip()[:240]
        description = str(item.get("description", "")).strip()[:1200]
        if starts_at is None or not title:
            continue
        code = _stable_code("llm_", item)
        active.append((code, starts_at))
        row, _ = ScheduleEvent.objects.get_or_create(
            case=case,
            rule_code=code,
            starts_at=starts_at,
            defaults={"title": title, "description": description},
        )
        changed = False
        for field, value in {"title": title, "description": description}.items():
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        if changed:
            row.save()
    for row in case.schedule_events.filter(rule_code__startswith="llm_", status=ScheduleEvent.Status.CANDIDATE):
        if (row.rule_code, row.starts_at) not in active:
            row.delete()
    # Remove old fixed-rule candidates; confirmed history remains visible.
    case.schedule_events.filter(status=ScheduleEvent.Status.CANDIDATE).exclude(rule_code__startswith="llm_").delete()


@transaction.atomic
def clear_generated_guidance(case):
    """Remove guidance whose source can no longer be proven after document deletion."""
    # A document refresh may invalidate tentative guidance, but must not erase
    # work the user already completed or calendar decisions they confirmed or
    # dismissed. A later refresh can reconcile the remaining tentative rows.
    case.checklist_items.filter(code__startswith="llm_").exclude(
        state=ChecklistItem.State.DONE,
    ).delete()
    case.schedule_events.filter(
        rule_code__startswith="llm_",
        status=ScheduleEvent.Status.CANDIDATE,
    ).delete()


def refresh_conversation_guidance(case, *, messages=None, llm=None, freshness_guard=None):
    """Generate checklist and calendar candidates from one member-owned chat room."""
    dialogue = conversation_messages(case, messages)
    if not dialogue:
        return {"checklist": 0, "calendar": 0, "updated": False}
    reference_day = timezone.localdate()
    prompt = (
        "당신은 임대차 상담 후속 행동 정리기입니다. 아래 한 채팅방의 대화만 근거로 사용하세요. "
        f"오늘 날짜는 {reference_day.isoformat()}이고 기준 연도는 {reference_day.year}년입니다. "
        "사용자가 확인하거나 실행해야 할 일을 checklist에 1~8개 작성하세요. "
        "체크리스트에 명시된 날짜나 기한이 있으면 해당 항목의 date에 YYYY-MM-DD를 넣고, 없으면 null을 넣으세요. "
        "연도가 생략된 월·일은 기준 연도를 사용하세요. 기준 날짜와 '이틀 뒤', '3일 이내'처럼 계산 가능한 상대 기한이 함께 나오면 정확한 날짜로 계산하세요. "
        f"예를 들어 '9월 3일로부터 이틀 뒤까지'는 {reference_day.year}-09-05입니다. "
        "날짜가 대화에서 명시되었거나 검증된 답변에서 명확히 계산된 일정만 calendar에 작성하세요. "
        "날짜를 추측하지 말고, 법률 판단이나 계약 안전 여부를 새로 만들지 마세요. "
        "각 항목의 code는 같은 의미면 재생성해도 유지되는 짧은 영문 snake_case로 작성하세요. "
        "priority는 1~100이며 숫자가 작을수록 우선입니다. "
        "JSON 객체만 출력하세요. 형식: "
        '{"checklist":[{"code":"registry_check","title":"...","description":"대화상 필요한 이유와 할 일","priority":10,"date":null}],'
        '"calendar":[{"code":"balance_registry_check","date":"YYYY-MM-DD","title":"...","description":"..."}]}\n'
        + json.dumps({"dialogue": dialogue}, ensure_ascii=False)
    )
    try:
        model = llm or get_llm(max_tokens=1000)
        response = model.invoke(prompt)
        checklist, calendar = _parse_payload(getattr(response, "content", str(response)))
        calendar = _calendar_from_dated_checklists(checklist, calendar)
        with _GUIDANCE_LOCKS[str(case.pk)], transaction.atomic():
            from cases.models import ContractCase
            locked_case = ContractCase.objects.select_for_update().get(pk=case.pk)
            if freshness_guard is not None:
                from chat.models import Conversation
                conversation_id, expected_digest, expected_revision = freshness_guard
                if locked_case.guidance_revision != expected_revision:
                    return {"checklist": 0, "calendar": 0, "updated": False, "stale": True}
                state = Conversation.objects.filter(pk=conversation_id).values_list("state", flat=True).first()
                if state is None or _messages_digest(state.get("messages", [])) != expected_digest:
                    return {"checklist": 0, "calendar": 0, "updated": False, "stale": True}
            _sync_checklist(locked_case, checklist)
            _sync_calendar(locked_case, calendar)
        return {"checklist": len(checklist), "calendar": len(calendar), "updated": True}
    except Exception as error:
        # A secondary personalization failure must not discard a verified chat answer.
        logger.warning("Conversation guidance refresh failed: %s", type(error).__name__)
        return {"checklist": case.checklist_items.count(), "calendar": case.schedule_events.count(), "updated": False}


def _background_refresh(case_id, conversation_id, messages, expected_digest, expected_revision):
    from cases.models import ContractCase
    close_old_connections()
    try:
        case = ContractCase.objects.get(pk=case_id)
        refresh_conversation_guidance(
            case,
            messages=messages,
            freshness_guard=(conversation_id, expected_digest, expected_revision),
        )
    finally:
        close_old_connections()


def schedule_conversation_guidance(case, *, messages=None, conversation=None):
    """Run the secondary LLM call after the chat transaction has committed."""
    snapshot = json.loads(json.dumps(messages or [], ensure_ascii=False))
    if conversation is None:
        conversation = case.conversations.order_by("created_at").first()
    if conversation is None:
        return
    expected_digest = _messages_digest(snapshot)
    from cases.models import ContractCase
    ContractCase.objects.filter(pk=case.pk).update(guidance_revision=F("guidance_revision") + 1)
    expected_revision = ContractCase.objects.values_list("guidance_revision", flat=True).get(pk=case.pk)
    transaction.on_commit(lambda: _GUIDANCE_EXECUTOR.submit(
        _background_refresh,
        case.pk,
        conversation.pk,
        snapshot,
        expected_digest,
        expected_revision,
    ))
