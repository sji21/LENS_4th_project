import importlib
import json
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

from cases.models import CaseFact, ChecklistItem, ContractCase, Report, ScheduleEvent
from cases.services.conversation_guidance import refresh_conversation_guidance
from cases.services.facts import invalidate_source, record_fact
from cases.services.reports import _fallback, _parse_json, _source_snapshot, generate_report
from cases.services.titles import title_from_question
from cases.services.storage import delete_encrypted, load_decrypted, save_encrypted
from chat.models import Conversation

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user("owner", "owner@example.com", "safe-password-123")


@pytest.fixture
def case(user):
    return ContractCase.objects.create(user=user, title="마포구 A아파트")


def test_case_dashboard_requires_login_and_enforces_owner(client, user, case):
    assert client.get("/cases/").status_code == 302
    client.force_login(user)
    response = client.get(f"/cases/{case.pk}/")
    assert response.status_code == 200
    page = response.content.decode()
    assert "CASE FACTS" not in page
    assert "확인된 계약 정보와 충돌" not in page
    assert "owner@example.com" in page
    assert page.index("전체 대화 일정") < page.index("전체 체크리스트")
    assert "채팅방별 표시" in page
    assert "채팅방별 리포트 파일" in page
    assert 'id="calendar-month"' in page
    assert 'id="calendar-picker"' in page
    assert 'id="calendar-picker-grid"' in page
    assert "<h2>달력 일정</h2>" not in page
    assert "상담 녹음 분석" not in page
    assert "cases/mypage.js" in page
    assert f'href="/cases/{case.pk}/chat/"' in page
    assert f'action="/cases/{case.pk}/rename/"' in page
    assert "채팅방 이름 수정" in page
    assert "현재 대화 리포트 생성" not in page
    assert "해당 채팅방 폴더에 버전별로 저장됩니다" in page
    other = get_user_model().objects.create_user("other", password="safe-password-123")
    client.force_login(other)
    assert client.get(f"/cases/{case.pk}/").status_code == 404


def test_case_creation_and_chat_selection(client, user):
    client.force_login(user)
    response = client.post("/cases/new/", {"title": "성동구 B주택"})
    case = ContractCase.objects.get()
    assert response.status_code == 302 and response.url == "/"
    page = client.get("/")
    assert page.status_code == 200 and "성동구 B주택" in page.content.decode()
    assert Conversation.objects.get().case == case


def test_new_chat_button_opens_draft_without_creating_room(client, user, case):
    client.force_login(user)
    page = client.get("/").content.decode()
    assert 'action="/cases/new/"' in page
    assert 'name="auto_title" value="1"' in page
    response = client.post("/cases/new/", {"auto_title": "1"})
    assert response.status_code == 302 and response.url == "/"
    assert ContractCase.objects.filter(user=user).count() == 1
    assert client.session.get("lens_case_id") is None


def test_logo_opens_fresh_draft_and_preserves_saved_room(client, user, case):
    client.force_login(user)
    session = client.session
    session["lens_case_id"] = str(case.pk)
    session.save()
    page = client.get("/").content.decode()
    assert 'class="brand-new-chat-form"' in page
    assert 'action="/new/"' in page
    response = client.post("/new/", follow=True)
    assert response.status_code == 200
    assert ContractCase.objects.filter(pk=case.pk, user=user).exists()
    assert client.session.get("lens_case_id") is None
    draft = Conversation.objects.get(pk=client.session["lens_conversation_id"])
    assert draft.user == user and draft.case is None


def test_chat_header_and_report_action_positions(client, user, case):
    client.force_login(user)
    session = client.session
    session["lens_case_id"] = str(case.pk)
    session.save()
    page = client.get("/").content.decode()
    assert f"<h1>{case.title}</h1>" in page
    assert "· 임대차 상담" not in page
    assert page.index('class="composer-section"') < page.index('id="report-form"') < page.index('id="chat-form"')


def test_workspace_is_only_the_chat_room_history(client, user, case):
    client.force_login(user)
    page = client.get("/").content.decode()
    assert "WORKSPACE" in page
    assert 'class="nav-current"' not in page
    assert f'href="/cases/{case.pk}/chat/"' in page


def test_mypage_combines_room_calendars_and_report_folders(client, user, case):
    other = ContractCase.objects.create(user=user, title="용산구 상담")
    ScheduleEvent.objects.create(case=case, starts_at="2026-10-10T09:00:00+09:00", rule_code="first", title="잔금일")
    ScheduleEvent.objects.create(case=other, starts_at="2026-10-12T10:00:00+09:00", rule_code="second", title="입주일")
    client.force_login(user)
    page = client.get("/cases/").content.decode()
    assert 'data-room="마포구 A아파트"' in page
    assert 'data-room="용산구 상담"' in page
    assert "room-color-0" in page and "room-color-1" in page
    assert page.count('class="report-folder"') == 2


def test_checklist_uses_room_colored_reminder_dots(client, user, case):
    ChecklistItem.objects.create(case=case, code="deposit", title="보증금 확인")
    client.force_login(user)
    page = client.get("/cases/").content.decode()
    assert 'class="reminder-dot room-color-0"' in page
    assert 'class="check-icon"' not in page


def test_signed_in_user_lands_on_chat_and_profile_opens_mypage(client, user):
    client.force_login(user)
    response = client.get("/")
    assert response.status_code == 200
    assert not ContractCase.objects.filter(user=user).exists()
    assert Conversation.objects.get(user=user).case is None
    page = response.content.decode()
    assert "나의 프로필" in page
    assert 'href="/cases/"' in page
    assert "첫 질문을 보내면 채팅방이 여기에 저장됩니다" in page
    assert "나의 임대차 상담" not in page
    assert "리포트 PDF" not in page
    assert 'id="report-form"' not in page
    assert 'id="toast-stack"' in page


def test_guest_hides_report_action_and_member_session_is_persistent(client):
    page = client.get("/").content.decode()
    assert "로그인 후 리포트 생성" not in page
    assert 'id="report-form"' not in page
    assert settings.SESSION_COOKIE_AGE == 14 * 24 * 60 * 60
    assert settings.SESSION_SAVE_EVERY_REQUEST is True


def test_signup_redirects_to_chat_without_creating_first_room(client):
    response = client.post("/accounts/signup/", {
        "username": "new-member", "email": "new@example.com",
        "password1": "safe-password-123", "password2": "safe-password-123",
    })
    assert response.status_code == 302 and response.url == "/"
    landing = client.get("/")
    user = get_user_model().objects.get(username="new-member")
    assert landing.status_code == 200
    assert not ContractCase.objects.filter(user=user).exists()
    assert Conversation.objects.get(user=user).case is None


def test_login_uses_email_and_always_redirects_to_chat_even_with_mypage_next(client, user):
    response = client.post(
        "/accounts/login/?next=/cases/",
        {"username": user.email.upper(), "password": "safe-password-123"},
    )
    assert response.status_code == 302
    assert response.url == "/"


def test_login_rejects_username_when_email_is_required(client, user):
    response = client.post(
        "/accounts/login/",
        {"username": user.username, "password": "safe-password-123"},
    )
    assert response.status_code == 200
    assert "올바른 이메일 주소" in response.content.decode()


def test_login_does_not_fall_back_to_an_email_shaped_username(client):
    User = get_user_model()
    User.objects.create_user(
        username="member@example.com",
        email="actual-owner@example.com",
        password="safe-password-123",
    )

    response = client.post(
        "/accounts/login/",
        {"username": "member@example.com", "password": "safe-password-123"},
    )

    assert response.status_code == 200
    assert response.context["form"].non_field_errors()


def test_login_claims_empty_guest_draft_without_creating_or_selecting_room(client, user, case):
    client.get("/")
    guest_id = client.session["lens_conversation_id"]
    response = client.post(
        "/accounts/login/",
        {"username": user.email, "password": "safe-password-123"},
    )
    conversation = Conversation.objects.get(pk=guest_id)
    assert response.status_code == 302 and response.url == "/"
    assert conversation.user == user
    assert conversation.case is None
    assert client.session["lens_conversation_id"] == str(conversation.pk)
    assert client.session.get("lens_case_id") is None


def test_first_member_question_creates_named_room_and_enables_report(client, user, monkeypatch):
    client.force_login(user)
    client.get("/")
    conversation = Conversation.objects.get(user=user)

    def fake_respond(state, question, _document_id=None):
        state["messages"].extend([
            {"id": "user-1", "role": "user", "content": question},
            {"id": "assistant-1", "role": "assistant", "status": "answered", "content": "확인했습니다.", "sources": []},
        ])

    monkeypatch.setattr("chat.services.respond", fake_respond)
    monkeypatch.setattr("cases.services.conversation_guidance.refresh_conversation_guidance", lambda *args, **kwargs: {})
    response = client.post("/api/chat/", data=json.dumps({
        "conversation_id": str(conversation.pk),
        "request_id": str(uuid.uuid4()),
        "message": "마포구 아파트 보증금 반환은 어떻게 준비하나요?",
    }), content_type="application/json")
    payload = response.json()
    conversation.refresh_from_db()
    assert response.status_code == 200 and payload["room_created"] is True
    assert conversation.case.title == "마포구 아파트 보증금 반환은 어떻게 준비하나요?"
    page = client.get("/").content.decode()
    assert 'id="report-form"' in page
    assert f'action="/cases/{conversation.case_id}/rename/"' in page


def test_greeting_does_not_become_a_chat_room_title():
    assert title_from_question("안녕") == "새 임대차 상담"
    assert title_from_question("  안녕하세요  ") == "새 임대차 상담"
    assert title_from_question("안녕하세요!") == "새 임대차 상담"
    assert title_from_question("안녕~") == "새 임대차 상담"
    assert title_from_question("Hi!") == "새 임대차 상담"
    assert title_from_question("대항력은 언제 생기나요?") == "대항력은 언제 생기나요?"


def test_report_snapshot_excludes_ungrounded_and_unanswered_turns(case):
    conversation = Conversation.objects.create(
        user=case.user,
        case=case,
        state={
            "documents": [],
            "messages": [
                {"id": "u1", "role": "user", "content": "안녕"},
                {"id": "a1", "role": "assistant", "status": "answered", "content": "안녕하세요.", "sources": []},
                {"id": "u2", "role": "user", "content": "대항력은 언제 생기나요?"},
                {"id": "a2", "role": "assistant", "status": "answered", "content": "전입 다음 날입니다.", "sources": [{"label": "주택임대차보호법 제3조"}]},
                {"id": "u3", "role": "user", "content": "오늘 날짜가 며칠이야?"},
                {"id": "a3", "role": "assistant", "status": "refused", "content": "범위 밖 질문입니다.", "sources": []},
            ],
        },
        expires_at="2036-01-01T00:00:00Z",
    )

    snapshot = _source_snapshot(case, [conversation])

    assert snapshot["dialogue"] == [
        {"role": "user", "content": "대항력은 언제 생기나요?"},
        {"role": "assistant", "content": "전입 다음 날입니다."},
    ]
    content = _fallback(snapshot)
    assert content["user_interests"] == ["대항력은 언제 생기나요?"]
    assert content["case_summary"] == "등록된 계약 정보가 없습니다."


def test_report_detail_uses_stable_navigation_and_summary_labels(client, user):
    case = ContractCase.objects.create(user=user, title="안녕")
    report = Report.objects.create(
        case=case,
        version=1,
        generation_mode="template_fallback",
        content_json={
            "case_summary": "등록된 계약 정보가 없습니다.",
            "user_interests": [],
            "questions_and_answers": [{"question": "대항력은 언제 생겨?", "answer": "전입신고 다음 날입니다."}],
            "confirmed_items": [],
            "unresolved_items": [], "next_checks": [], "source_refs": [],
        },
    )
    client.force_login(user)

    page = client.get(f"/cases/{case.pk}/reports/{report.pk}/").content.decode()

    assert "← 마이페이지" in page
    assert "안녕 마이페이지" not in page
    assert "상담 요약" in page
    assert "계약 기본 정보" not in page
    assert "기본 요약" in page
    assert "질문: 대항력은 언제 생겨?" in page
    assert "확인한 내용: 전입신고 다음 날입니다." in page
    assert "{'question':" not in page
    assert "PDF 다운로드" in page
    assert 'class="report-page-actions"' in page


def test_room_delete_keeps_the_user_on_the_origin_screen(client, user, case):
    client.force_login(user)

    chat = client.get("/").content.decode()
    assert f'action="/cases/{case.pk}/delete/"' in chat
    response = client.post(f"/cases/{case.pk}/delete/", {"next": "chat"})

    assert response.status_code == 302
    assert response.url == "/"
    assert not ContractCase.objects.filter(pk=case.pk).exists()


def test_room_delete_from_mypage_returns_to_mypage(client, user, case):
    client.force_login(user)

    response = client.post(f"/cases/{case.pk}/delete/")

    assert response.status_code == 302
    assert response.url == "/cases/"
    assert not ContractCase.objects.filter(pk=case.pk).exists()


def test_room_delete_controls_reserve_space_for_a_long_title(client, user):
    case = ContractCase.objects.create(user=user, title="긴 채팅방 제목 " * 20)
    client.force_login(user)

    chat = client.get("/").content.decode()
    dashboard = client.get("/cases/").content.decode()

    assert f'action="/cases/{case.pk}/delete/"' in chat
    assert f'action="/cases/{case.pk}/delete/"' in dashboard


def test_report_json_question_answer_objects_are_normalized_for_rendering():
    content = _parse_json(json.dumps({
        "case_summary": "요약",
        "user_interests": ["대항력"],
        "questions_and_answers": [{"question": "대항력은 언제 생겨?", "answer": "전입신고 다음 날"}],
        "confirmed_items": [], "unresolved_items": [], "next_checks": [], "source_refs": [],
    }, ensure_ascii=False))

    assert content["questions_and_answers"] == ["질문: 대항력은 언제 생겨?\n확인한 내용: 전입신고 다음 날"]


def test_report_json_object_items_are_kept_as_readable_text():
    content = _parse_json(json.dumps({
        "case_summary": "요약",
        "user_interests": [{"item": "대항력", "value": "발생 시점"}],
        "questions_and_answers": [],
        "confirmed_items": [{"item": "보증금", "value": "1억원"}],
        "unresolved_items": [{"item": "전입신고일", "status": "확인 필요"}],
        "next_checks": [{"description": "전입신고일을 확인하세요."}],
        "source_refs": [{"label": "주택임대차보호법 제3조", "url": "https://example.test/law"}],
    }, ensure_ascii=False))

    assert content["confirmed_items"] == ["항목: 보증금 · 내용: 1억원"]
    assert content["next_checks"] == ["설명: 전입신고일을 확인하세요."]
    assert content["source_refs"] == ["항목: 주택임대차보호법 제3조 · 출처: https://example.test/law"]


def test_member_can_rename_only_owned_chat_room(client, user, case):
    client.force_login(user)
    response = client.post(f"/cases/{case.pk}/rename/", {"title": "  보증금 반환 준비  ", "next": "chat"})
    case.refresh_from_db()
    assert response.status_code == 302 and response.url == "/"
    assert case.title == "보증금 반환 준비"
    other = get_user_model().objects.create_user("other-room-owner", password="safe-password-123")
    other_case = ContractCase.objects.create(user=other, title="다른 회원 방")
    assert client.post(f"/cases/{other_case.pk}/rename/", {"title": "침범"}).status_code == 404


def test_legacy_cleanup_deletes_only_empty_default_rooms(user):
    empty = ContractCase.objects.create(user=user, title="나의 임대차 상담")
    preserved = ContractCase.objects.create(user=user, title="나의 임대차 상담")
    Conversation.objects.create(
        user=user, case=empty, state={"messages": [], "documents": []},
        expires_at="2036-01-01T00:00:00Z",
    )
    Conversation.objects.create(
        user=user, case=preserved,
        state={"messages": [{"id": "u1", "role": "user", "content": "기존 상담"}], "documents": []},
        expires_at="2036-01-01T00:00:00Z",
    )
    cleanup = importlib.import_module(
        "cases.migrations.0002_remove_empty_legacy_default_cases"
    ).remove_empty_legacy_default_cases
    cleanup(SimpleNamespace(get_model=lambda *_args: ContractCase), None)
    assert not ContractCase.objects.filter(pk=empty.pk).exists()
    assert ContractCase.objects.filter(pk=preserved.pk).exists()


def test_fact_conflicts_are_preserved_and_resolve_after_source_deletion(case):
    first = record_fact(case, key="balance_date", value="2026-10-10", source_type="chat", source_ref="m1")
    second = record_fact(case, key="balance_date", value="2026-10-15", source_type="document", source_ref="d1")
    assert set(case.facts.values_list("status", flat=True)) == {CaseFact.Status.CONFLICT}
    invalidate_source(case, "document", "d1")
    first.refresh_from_db(); second.refresh_from_db()
    assert first.status == CaseFact.Status.ACTIVE
    assert second.status == CaseFact.Status.INVALIDATED


def test_chat_room_llm_drives_checklist_and_candidate_calendar(case):
    payload = {
        "checklist": [{
            "code": "guarantee_check", "title": "반환보증 가입 가능 여부 확인",
            "description": "사용자가 반환보증을 질문했으므로 가입 조건을 확인하세요.", "priority": 10,
        }],
        "calendar": [{
            "code": "balance_day", "date": "2026-10-10", "title": "잔금일",
            "description": "대화에서 확인한 잔금일입니다.",
        }],
    }
    fake = SimpleNamespace(invoke=lambda _prompt: SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))
    messages = [
        {"id": "u1", "role": "user", "content": "반환보증을 알아보고 있어. 잔금일은 2026-10-10이야."},
        {"id": "a1", "role": "assistant", "status": "answered", "content": "가입 조건을 확인하세요."},
    ]
    result = refresh_conversation_guidance(case, messages=messages, llm=fake)
    item = ChecklistItem.objects.get(case=case, code="llm_guarantee_check")
    assert result["updated"] and item.state == ChecklistItem.State.TODO
    assert ScheduleEvent.objects.filter(case=case, rule_code="llm_balance_day", status="candidate").exists()


def test_relative_deadline_prompt_uses_reference_date_and_saves_llm_result(case, monkeypatch):
    captured = {}
    payload = {
        "checklist": [{
            "code": "registry_check", "title": "등기 확인", "description": "이틀 뒤 확인",
            "priority": 10, "date": "2026-09-05",
        }],
        "calendar": [{
            "code": "registry_deadline", "date": "2026-09-05", "title": "등기 확인 기한",
            "description": "9월 3일로부터 이틀 뒤",
        }],
    }

    def invoke(prompt):
        captured["prompt"] = prompt
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    monkeypatch.setattr("cases.services.conversation_guidance.timezone.localdate", lambda: date(2026, 9, 16))
    result = refresh_conversation_guidance(
        case,
        messages=[{"id": "u1", "role": "user", "content": "9월 3일이고 이틀 뒤까지 등기를 확인해야 해."}],
        llm=SimpleNamespace(invoke=invoke),
    )
    assert "오늘 날짜는 2026-09-16" in captured["prompt"]
    assert "2026-09-05" in captured["prompt"]
    assert result["updated"]
    assert ScheduleEvent.objects.filter(case=case, starts_at__date="2026-09-05").exists()


def test_dated_checklist_is_also_added_to_calendar(case):
    payload = {
        "checklist": [{
            "code": "registry_check", "title": "등기부등본 다시 확인",
            "description": "잔금 지급 전 권리변동을 확인하세요.",
            "priority": 10, "date": "2026-10-09",
        }],
        "calendar": [],
    }
    fake = SimpleNamespace(invoke=lambda _prompt: SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))
    result = refresh_conversation_guidance(
        case,
        messages=[{"id": "u1", "role": "user", "content": "2026-10-09에 등기부등본을 다시 확인할게."}],
        llm=fake,
    )
    assert result["updated"] and result["calendar"] == 1
    assert ScheduleEvent.objects.filter(
        case=case,
        rule_code="llm_checklist_registry_check",
        starts_at__date="2026-10-09",
        title="등기부등본 다시 확인",
    ).exists()


def test_schedule_requires_explicit_confirmation(client, user, case):
    event = ScheduleEvent.objects.create(
        case=case, starts_at="2026-10-10T09:00:00+09:00", rule_code="llm_balance_day",
        title="잔금일", description="대화에서 확인한 잔금일입니다.",
    )
    client.force_login(user)
    response = client.post(f"/cases/{case.pk}/events/{event.pk}/", {"action": "confirm"})
    event.refresh_from_db()
    assert response.status_code == 302
    assert event.status == ScheduleEvent.Status.CONFIRMED and event.user_confirmed


def test_report_is_versioned_snapshot_of_verified_inputs(case, client, monkeypatch):
    Conversation.objects.create(
        user=case.user, case=case, state={
            "documents": [], "completed_requests": [],
            "messages": [{"id": "a1", "role": "assistant", "status": "answered", "content": "검증된 답변", "context_content": "검증된 답변", "sources": [{"label": "주택임대차보호법 제3조", "doc_type": "law", "url": "https://law.go.kr"}]}],
        }, expires_at="2036-01-01T00:00:00Z",
    )
    record_fact(case, key="deposit_amount", value=170000000, source_type="chat", source_ref="m1")
    payload = {
        "case_summary": "요약", "user_interests": ["대항력 발생 요건"],
        "questions_and_answers": ["질문: 대항력은 언제 생기나요? / 확인한 내용: 검증된 답변"],
        "confirmed_items": ["보증금"], "unresolved_items": [],
        "next_checks": ["전입신고 가능일 확인"], "source_refs": ["주택임대차보호법 제3조"],
    }
    fake = SimpleNamespace(invoke=lambda _prompt: SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))
    first = generate_report(case, llm=fake)
    second = generate_report(case, llm=fake)
    assert (first.version, second.version) == (1, 2)
    assert first.content_json == payload and first.generation_mode == "llm"
    assert first.source_snapshot["dialogue"][0]["content"] == "검증된 답변"
    client.force_login(case.user)
    download = client.get(f"/cases/{case.pk}/reports/{first.pk}/download/")
    assert download.status_code == 200
    assert download["Content-Type"] == "application/pdf"
    assert "attachment" in download["Content-Disposition"]
    assert download.content.startswith(b"%PDF")
    monkeypatch.setattr("cases.views.generate_report", lambda _case: second)
    created = client.post(f"/cases/{case.pk}/reports/new/")
    assert created.status_code == 200
    assert created.content.startswith(b"%PDF")


def test_private_file_round_trip_is_encrypted(case, settings, tmp_path):
    settings.PRIVATE_UPLOAD_ROOT = tmp_path
    settings.FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()
    raw = b"private lease document"
    key = save_encrypted(case.pk, "attachment-id", raw)
    stored = (tmp_path / key).read_bytes()
    assert raw not in stored
    assert load_decrypted(key) == raw
    delete_encrypted(key)
    assert not (tmp_path / key).exists()
