import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.forms import SignUpForm
from cases.models import Attachment, CaseFact, ChecklistItem, ContractCase, Report, ScheduleEvent
from cases.services.attachments import (
    promote_pending_documents,
    record_document,
    record_pending_document,
    storage_rollback_guard,
)
from cases.services.conversation_guidance import (
    _messages_digest,
    clear_generated_guidance,
    refresh_conversation_guidance,
)
from cases.services.facts import extract_rule_facts
from cases.services.reports import _fallback, generate_report
from chat.models import Conversation, PendingDocument
from chat import services as chat_services

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user("review-owner", "review@example.com", "safe-password-123")


@pytest.fixture
def case(user):
    return ContractCase.objects.create(user=user, title="검토 채팅방")


def test_member_room_survives_ttl_purge(user, case):
    room = Conversation.objects.create(
        user=user, case=case, state={"messages": [], "documents": []},
        expires_at=timezone.now() - timedelta(hours=1),
    )
    draft = Conversation.objects.create(
        user=user, state={"messages": [], "documents": []},
        expires_at=timezone.now() - timedelta(hours=1),
    )
    call_command("purge_chats")
    assert Conversation.objects.filter(pk=room.pk).exists()
    assert not Conversation.objects.filter(pk=draft.pk).exists()


def test_pending_upload_is_encrypted_and_promoted(user, case, settings, tmp_path):
    settings.PRIVATE_UPLOAD_ROOT = tmp_path
    settings.FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()
    conversation = Conversation.objects.create(
        user=user, state={"messages": [], "documents": [{
            "document_id": "doc1", "kind": "contract", "analysis": {},
            "context": {"chunks": [{"text": "임대차계약서 보증금 3천만원"}]},
        }]}, expires_at=timezone.now() + timedelta(days=1),
    )
    pending = record_pending_document(
        conversation, data=b"private bytes", filename="contract.pdf",
        content_type="application/pdf", document_id="doc1",
    )
    assert b"private bytes" not in (tmp_path / pending.storage_key).read_bytes()
    promote_pending_documents(conversation, case)
    assert not PendingDocument.objects.filter(pk=pending.pk).exists()
    assert case.attachments.filter(context_json__document_id="doc1").exists()


def test_outer_transaction_rollback_removes_new_encrypted_file(case, settings, tmp_path):
    settings.PRIVATE_UPLOAD_ROOT = tmp_path
    settings.FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()
    with pytest.raises(RuntimeError):
        with storage_rollback_guard(), transaction.atomic():
            record_document(
                case,
                data=b"private bytes",
                filename="contract.pdf",
                content_type="application/pdf",
                document={"document_id": "rollback-doc", "analysis": {}, "context": {}},
                extracted_text="임대차계약서",
            )
            raise RuntimeError("force rollback")
    assert not Attachment.objects.filter(case=case).exists()
    assert not list(tmp_path.rglob("*.bin"))


def test_outer_transaction_rollback_removes_pending_encrypted_file(user, settings, tmp_path):
    settings.PRIVATE_UPLOAD_ROOT = tmp_path
    settings.FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()
    conversation = Conversation.objects.create(
        user=user,
        state={"messages": [], "documents": []},
        expires_at=timezone.now() + timedelta(days=1),
    )
    with pytest.raises(RuntimeError):
        with storage_rollback_guard(), transaction.atomic():
            record_pending_document(
                conversation,
                data=b"pending bytes",
                filename="contract.pdf",
                content_type="application/pdf",
                document_id="pending-rollback",
            )
            raise RuntimeError("force rollback")
    assert not PendingDocument.objects.filter(conversation=conversation).exists()
    assert not list(tmp_path.rglob("*.bin"))


def test_pending_upload_fails_closed_without_encryption_key(user, settings, tmp_path):
    settings.PRIVATE_UPLOAD_ROOT = tmp_path
    settings.FILE_ENCRYPTION_KEY = ""
    conversation = Conversation.objects.create(
        user=user,
        state={"messages": [], "documents": []},
        expires_at=timezone.now() + timedelta(days=1),
    )
    with pytest.raises(ImproperlyConfigured):
        record_pending_document(
            conversation,
            data=b"private bytes",
            filename="contract.pdf",
            content_type="application/pdf",
            document_id="no-key",
        )
    assert not PendingDocument.objects.filter(conversation=conversation).exists()
    assert not list(tmp_path.rglob("*.bin"))


def test_encrypted_delete_failure_is_logged_without_breaking_commit(
    case,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    attachment = Attachment.objects.create(
        case=case,
        kind=Attachment.Kind.DOCUMENT,
        original_name="contract.pdf",
        size=1,
        sha256="a" * 64,
        storage_key="missing/file.bin",
    )

    def fail_delete(_storage_key):
        raise OSError("simulated delete failure")

    monkeypatch.setattr("cases.signals.delete_encrypted", fail_delete)
    with django_capture_on_commit_callbacks(execute=True):
        attachment.delete()
    assert not Attachment.objects.filter(pk=attachment.pk).exists()


def test_done_and_fixed_checklist_items_are_preserved(case):
    done = ChecklistItem.objects.create(case=case, code="llm_old", title="완료", state=ChecklistItem.State.DONE)
    fixed = ChecklistItem.objects.create(case=case, code="fixed_rule", title="고정")
    fake = SimpleNamespace(invoke=lambda _prompt: SimpleNamespace(content=json.dumps({
        "checklist": [{"code": "new", "title": "신규", "description": "설명", "priority": 10}],
        "calendar": [],
    }, ensure_ascii=False)))
    refresh_conversation_guidance(case, messages=[{"role": "user", "content": "질문"}], llm=fake)
    done.refresh_from_db()
    assert done.state == ChecklistItem.State.DONE
    assert ChecklistItem.objects.filter(pk=fixed.pk).exists()


def test_generated_checklist_item_reappears_after_automatic_dismissal(case):
    item = ChecklistItem.objects.create(
        case=case,
        code="llm_registry_check",
        title="등기부 확인",
        state=ChecklistItem.State.DISMISSED,
    )
    fake = SimpleNamespace(invoke=lambda _prompt: SimpleNamespace(content=json.dumps({
        "checklist": [{
            "code": "registry_check",
            "title": "등기부 확인",
            "description": "다시 확인",
            "priority": 10,
        }],
        "calendar": [],
    }, ensure_ascii=False)))

    refresh_conversation_guidance(case, messages=[{"role": "user", "content": "등기부를 확인할게요"}], llm=fake)

    item.refresh_from_db()
    assert item.state == ChecklistItem.State.TODO


def test_document_guidance_cleanup_preserves_user_decisions(case):
    done = ChecklistItem.objects.create(
        case=case, code="llm_done", title="완료한 일", state=ChecklistItem.State.DONE,
    )
    todo = ChecklistItem.objects.create(
        case=case, code="llm_todo", title="임시 할 일", state=ChecklistItem.State.TODO,
    )
    confirmed = ScheduleEvent.objects.create(
        case=case,
        starts_at=timezone.now(),
        title="확정 일정",
        rule_code="llm_confirmed",
        status=ScheduleEvent.Status.CONFIRMED,
        user_confirmed=True,
    )
    dismissed = ScheduleEvent.objects.create(
        case=case,
        starts_at=timezone.now() + timedelta(days=1),
        title="제외 일정",
        rule_code="llm_dismissed",
        status=ScheduleEvent.Status.DISMISSED,
    )
    candidate = ScheduleEvent.objects.create(
        case=case,
        starts_at=timezone.now() + timedelta(days=2),
        title="후보 일정",
        rule_code="llm_candidate",
        status=ScheduleEvent.Status.CANDIDATE,
    )

    clear_generated_guidance(case)

    assert ChecklistItem.objects.filter(pk=done.pk).exists()
    assert not ChecklistItem.objects.filter(pk=todo.pk).exists()
    assert ScheduleEvent.objects.filter(pk=confirmed.pk).exists()
    assert ScheduleEvent.objects.filter(pk=dismissed.pk).exists()
    assert not ScheduleEvent.objects.filter(pk=candidate.pk).exists()


def test_dismissed_generated_checklist_items_are_not_counted_or_rendered(client, user, case):
    ChecklistItem.objects.create(case=case, code="llm_old", title="이전 항목", state=ChecklistItem.State.DISMISSED)
    client.force_login(user)
    page = client.get("/cases/").content.decode()
    assert "이전 항목" not in page


def test_member_draft_keeps_one_day_expiry_after_upload_request(client, user, monkeypatch):
    def fake_add_document(state, *_args, **_kwargs):
        state["documents"].append({"document_id": "doc1", "kind": "contract"})
        return "첨부됨"

    monkeypatch.setattr("chat.services.add_document", fake_add_document)
    client.force_login(user)
    client.get("/")
    conversation = Conversation.objects.get(user=user, case__isnull=True)
    response = client.post("/api/documents/", data={
        "conversation_id": str(conversation.pk),
        "request_id": "88888888-8888-8888-8888-888888888888",
        "file": SimpleUploadedFile("contract.pdf", b"%PDF-1.4", content_type="application/pdf"),
    })
    assert response.status_code == 200
    conversation.refresh_from_db()
    assert timezone.now() + timedelta(hours=23) < conversation.expires_at < timezone.now() + timedelta(days=2)


def test_signup_unique_constraint_race_returns_email_feedback(client, monkeypatch):
    user_model = get_user_model()
    original_save = SignUpForm.save

    def racing_save(form, *args, **kwargs):
        user_model.objects.create_user("race-winner", form.cleaned_data["email"], "Safe-password-123!")
        return original_save(form, *args, **kwargs)

    monkeypatch.setattr(SignUpForm, "save", racing_save)
    response = client.post("/accounts/signup/", {
        "first_name": "경쟁 사용자", "email": "race@example.com",
        "password1": "Safe-password-123!", "password2": "Safe-password-123!",
    })
    assert response.status_code == 200
    assert "이미 가입된 이메일입니다" in response.content.decode()


@pytest.mark.parametrize("text, expected", [
    ("근저당은 없습니다.", False),
    ("근저당 말소가 완료됐습니다.", False),
    ("저당권이 설정되지 않았습니다.", False),
    ("근저당이 있는지 모르겠습니다.", None),
    ("근저당을 잔금일에 말소할 예정입니다.", True),
    ("근저당이 없어요.", False),
    ("근저당이 있지 않습니다.", False),
    ("근저당이 안 걸려 있어요.", False),
    ("근저당이 없는지 모르겠습니다.", None),
])
def test_mortgage_negation_and_uncertainty(text, expected):
    values = [value for key, value, *_ in extract_rule_facts(text) if key == "mortgage_present"]
    assert (values[0] if values else None) is expected


@pytest.mark.parametrize("text", [
    "근저당 말소 예정은 없습니다.",
    "근저당을 없애기로 하지 않았습니다.",
    "저당권 해지 약속은 없어요.",
])
def test_negated_mortgage_removal_is_not_saved_as_a_promise(text):
    keys = [key for key, *_ in extract_rule_facts(text)]
    assert "mortgage_removal_promise" not in keys
    assert "mortgage_present" in keys


def test_report_fallback_keeps_question_answer_alignment():
    snapshot = {
        "case": {"title": "상담"}, "facts": [], "sources": [],
        "dialogue": [
            {"role": "user", "content": "질문 A"},
            {"role": "user", "content": "질문 B"},
            {"role": "assistant", "content": "답변 B"},
        ],
    }
    pairs = _fallback(snapshot)["questions_and_answers"]
    assert "질문 A" in pairs[0] and "확인되지 않았습니다" in pairs[0]
    assert "질문 B" in pairs[1] and "답변 B" in pairs[1]


def test_report_version_retries_unique_conflict(case, monkeypatch):
    payload = {
        "case_summary": "요약",
        "user_interests": [],
        "questions_and_answers": [],
        "confirmed_items": [],
        "unresolved_items": [],
        "next_checks": [],
        "source_refs": [],
    }
    fake = SimpleNamespace(invoke=lambda _prompt: SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))
    original_create = Report.objects.create
    calls = {"count": 0}

    def conflicting_create(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise IntegrityError("simulated version collision")
        return original_create(**kwargs)

    monkeypatch.setattr(Report.objects, "create", conflicting_create)
    report = generate_report(case, llm=fake)
    assert report.version == 1
    assert calls["count"] == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_report_generation_uses_distinct_versions(case):
    payload = {
        "case_summary": "요약",
        "user_interests": [],
        "questions_and_answers": [],
        "confirmed_items": [],
        "unresolved_items": [],
        "next_checks": [],
        "source_refs": [],
    }

    def create_report(_index):
        fake = SimpleNamespace(invoke=lambda _prompt: SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))
        local_case = ContractCase.objects.get(pk=case.pk)
        return generate_report(local_case, llm=fake).version

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = sorted(executor.map(create_report, range(2)))
    assert versions == [1, 2]


def test_dashboard_detail_selects_requested_case(client, user, case):
    other = ContractCase.objects.create(user=user, title="두 번째")
    client.force_login(user)
    response = client.get(f"/cases/{other.pk}/")
    assert response.status_code == 200
    assert client.session["lens_case_id"] == str(other.pk)


def test_calendar_candidate_has_confirm_and_dismiss_ui(client, user, case):
    event = ScheduleEvent.objects.create(
        case=case, starts_at=timezone.now(), title="잔금일", rule_code="llm_balance",
    )
    client.force_login(user)
    page = client.get("/cases/").content.decode()
    assert f'action="/cases/{case.pk}/events/{event.pk}/"' in page
    assert 'value="confirm"' in page and 'value="dismiss"' in page


def test_new_draft_does_not_delete_other_tab_draft(client, user):
    client.force_login(user)
    client.get("/")
    first_id = client.session["lens_conversation_id"]
    second = client.__class__()
    second.force_login(user)
    second.get("/")
    assert Conversation.objects.filter(pk=first_id).exists()
    assert Conversation.objects.filter(user=user, case__isnull=True).count() == 2


def test_chat_persistence_failure_rolls_back_room_and_answer(client, user, monkeypatch):
    client.force_login(user)
    client.get("/")
    conversation = Conversation.objects.get(user=user)

    def fake_respond(state, question, _document_id=None):
        state["messages"].extend([
            {"id": "u1", "role": "user", "content": question},
            {"id": "a1", "role": "assistant", "status": "answered", "content": "답변", "sources": []},
        ])

    monkeypatch.setattr(chat_services, "respond", fake_respond)
    monkeypatch.setattr(chat_services, "sync_persistent_messages", lambda *_: (_ for _ in ()).throw(RuntimeError("db fail")))
    response = client.post("/api/chat/", data=json.dumps({
        "conversation_id": str(conversation.pk), "request_id": "11111111-1111-1111-1111-111111111111",
        "message": "첫 질문",
    }), content_type="application/json")
    conversation.refresh_from_db()
    assert response.status_code == 503
    assert conversation.case_id is None and conversation.state["messages"] == []
    assert not ContractCase.objects.filter(user=user).exists()


def test_member_document_delete_removes_derived_messages(client, user, case, monkeypatch):
    conversation = Conversation.objects.create(
        user=user, case=case, expires_at=timezone.now() + timedelta(days=1),
        state={"documents": [{"document_id": "doc1"}], "completed_requests": [],
               "document_provenance_version": 1, "messages": [
            {"id": "u1", "role": "user", "content": "문서 질문", "document_ids": ["doc1"]},
            {"id": "a1", "role": "assistant", "status": "answered", "content": "문서 답변", "document_ids": ["doc1"]},
            {"id": "u2", "role": "user", "content": "일반 질문"},
        ]},
    )
    monkeypatch.setattr("cases.services.conversation_guidance.schedule_conversation_guidance", lambda *_args, **_kwargs: None)
    client.force_login(user)
    session = client.session
    session["lens_conversation_id"] = str(conversation.pk)
    session["lens_case_id"] = str(case.pk)
    session.save()
    response = client.post("/api/documents/doc1/delete/", data=json.dumps({
        "conversation_id": str(conversation.pk), "request_id": "22222222-2222-2222-2222-222222222222",
    }), content_type="application/json")
    assert response.status_code == 200
    assert [message["id"] for message in response.json()["messages"]] == ["u2"]


def test_member_document_delete_invalidates_removed_chat_facts(client, user, case, monkeypatch):
    conversation = Conversation.objects.create(
        user=user, case=case, expires_at=timezone.now() + timedelta(days=1),
        state={"documents": [{"document_id": "doc1"}], "completed_requests": [],
               "document_provenance_version": 1, "messages": [
            {"id": "u1", "role": "user", "content": "문서 질문", "document_ids": ["doc1"]},
        ]},
    )
    fact = CaseFact.objects.create(
        case=case, key="deposit_amount", value_json=100, normalized_value="100",
        source_type=CaseFact.SourceType.CHAT, source_ref="u1",
    )
    monkeypatch.setattr("cases.services.conversation_guidance.schedule_conversation_guidance", lambda *_args, **_kwargs: None)
    client.force_login(user)
    session = client.session
    session["lens_conversation_id"] = str(conversation.pk)
    session["lens_case_id"] = str(case.pk)
    session.save()
    response = client.post("/api/documents/doc1/delete/", data=json.dumps({
        "conversation_id": str(conversation.pk), "request_id": "77777777-7777-7777-7777-777777777777",
    }), content_type="application/json")
    assert response.status_code == 200
    fact.refresh_from_db()
    assert fact.status == CaseFact.Status.INVALIDATED


def test_mixed_legacy_document_delete_clears_unproven_history_and_guidance(client, user, case, monkeypatch):
    conversation = Conversation.objects.create(
        user=user,
        case=case,
        expires_at=timezone.now() + timedelta(days=1),
        state={"documents": [{"document_id": "doc1"}], "completed_requests": [], "messages": [
            {"id": "legacy-u", "role": "user", "content": "예전 문서 질문"},
            {"id": "legacy-a", "role": "assistant", "status": "answered", "content": "예전 문서 답변"},
            {"id": "tagged-u", "role": "user", "content": "새 문서 질문", "document_ids": ["doc1"]},
            {"id": "tagged-a", "role": "assistant", "status": "answered", "content": "새 문서 답변", "document_ids": ["doc1"]},
        ]},
    )
    ChecklistItem.objects.create(case=case, code="llm_document", title="문서 기반 확인", state=ChecklistItem.State.DONE)
    ScheduleEvent.objects.create(
        case=case,
        starts_at=timezone.now(),
        title="문서 기반 일정",
        rule_code="llm_document",
        status=ScheduleEvent.Status.CONFIRMED,
    )
    monkeypatch.setattr("cases.services.conversation_guidance.schedule_conversation_guidance", lambda *_args, **_kwargs: None)
    client.force_login(user)
    session = client.session
    session["lens_conversation_id"] = str(conversation.pk)
    session["lens_case_id"] = str(case.pk)
    session.save()
    response = client.post("/api/documents/doc1/delete/", data=json.dumps({
        "conversation_id": str(conversation.pk),
        "request_id": "33333333-3333-3333-3333-333333333333",
    }), content_type="application/json")
    assert response.status_code == 200
    assert response.json()["messages"] == []
    assert case.checklist_items.filter(code="llm_document", state=ChecklistItem.State.DONE).exists()
    assert case.schedule_events.filter(
        rule_code="llm_document", status=ScheduleEvent.Status.CONFIRMED,
    ).exists()


def test_stale_guidance_result_is_discarded(case):
    current_messages = [{"id": "new", "role": "user", "content": "최신 질문"}]
    conversation = Conversation.objects.create(
        user=case.user,
        case=case,
        state={"documents": [], "completed_requests": [], "messages": current_messages},
        expires_at=timezone.now() + timedelta(days=1),
    )
    old_messages = [{"id": "old", "role": "user", "content": "과거 질문"}]
    fake = SimpleNamespace(invoke=lambda _prompt: SimpleNamespace(content=json.dumps({
        "checklist": [{"code": "old", "title": "오래된 항목"}],
        "calendar": [],
    }, ensure_ascii=False)))
    result = refresh_conversation_guidance(
        case,
        messages=old_messages,
        llm=fake,
        freshness_guard=(conversation.pk, _messages_digest(old_messages), case.guidance_revision),
    )
    assert result["stale"] is True
    assert not case.checklist_items.filter(code="llm_old").exists()


def test_old_guidance_revision_is_discarded_even_when_messages_match(case):
    messages = [{"id": "same", "role": "user", "content": "질문"}]
    conversation = Conversation.objects.create(
        user=case.user,
        case=case,
        state={"documents": [], "completed_requests": [], "messages": messages},
        expires_at=timezone.now() + timedelta(days=1),
    )
    ContractCase.objects.filter(pk=case.pk).update(guidance_revision=2)
    fake = SimpleNamespace(invoke=lambda _prompt: SimpleNamespace(content=json.dumps({
        "checklist": [{"code": "old", "title": "오래된 항목"}],
        "calendar": [],
    }, ensure_ascii=False)))
    result = refresh_conversation_guidance(
        case,
        messages=messages,
        llm=fake,
        freshness_guard=(conversation.pk, _messages_digest(messages), 1),
    )
    assert result["stale"] is True
    assert not case.checklist_items.filter(code="llm_old").exists()


def test_signup_duplicate_email_shows_reason_popup(client, user):
    response = client.post("/accounts/signup/", {
        "first_name": "같은 이름", "email": user.email.upper(),
        "password1": "Safe-password-123!", "password2": "Safe-password-123!",
    })
    page = response.content.decode()
    assert response.status_code == 200
    assert 'role="alertdialog"' in page
    assert "이미 가입된 이메일입니다" in page


def test_signup_invalid_and_duplicate_email_show_exact_reason(client, user):
    invalid = client.post("/accounts/signup/", {
        "first_name": "새 사용자", "email": "wrong-email",
        "password1": "Safe-password-123!", "password2": "Safe-password-123!",
    }).content.decode()
    assert "이메일 형식이 올바르지 않습니다" in invalid

    duplicate = client.post("/accounts/signup/", {
        "first_name": "새 사용자", "email": user.email.upper(),
        "password1": "Safe-password-123!", "password2": "Safe-password-123!",
    }).content.decode()
    assert "이미 가입된 이메일입니다" in duplicate


def test_signup_password_feedback_is_shown_in_popup(client):
    mismatch = client.post("/accounts/signup/", {
        "first_name": "새 사용자", "email": "new-c@example.com",
        "password1": "Safe-password-123!", "password2": "Different-password-123!",
    }).content.decode()
    assert "비밀번호와 비밀번호 확인이 일치하지 않습니다" in mismatch

    too_short = client.post("/accounts/signup/", {
        "first_name": "새 사용자", "email": "new-d@example.com",
        "password1": "a1!", "password2": "a1!",
    }).content.decode()
    assert 'id="signup-error-popup"' in too_short
    assert "비밀번호" in too_short


def test_signup_error_popup_assets_and_accessibility(client):
    response = client.post("/accounts/signup/", {})
    page = response.content.decode()
    assert "/static/accounts/signup.js?v=20260917-account" in page
    assert 'aria-modal="true"' in page
    assert 'aria-labelledby="signup-error-title"' in page


def test_signup_field_error_is_below_its_input_and_rules_are_compact(client, user):
    page = client.post("/accounts/signup/", {
        "first_name": "이름", "email": "wrong-email",
        "password1": "a1!", "password2": "different",
    }).content.decode()
    email_input = page.index('name="email"')
    email_error = page.index("이메일 형식이 올바르지 않습니다")
    assert email_input < email_error
    assert 'class="password-rules"' in page
    assert "8자 이상" in page


def test_signup_edit_clears_only_that_fields_error_state():
    script = (settings.BASE_DIR / "static" / "accounts" / "signup.js").read_text(encoding="utf-8")
    assert 'input.addEventListener("input"' in script
    assert 'classList.remove("has-error")' in script
    assert 'querySelectorAll(".field-error")' in script
    assert 'removeAttribute("aria-invalid")' in script
