from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from cases.models import ContractCase, Report
from chat.models import Conversation


pytestmark = pytest.mark.django_db


def test_signup_uses_email_as_login_id_and_allows_duplicate_names(client):
    payload = {
        "first_name": "같은 이름",
        "email": "first@example.com",
        "password1": "Safe-password-123!",
        "password2": "Safe-password-123!",
    }
    response = client.post("/accounts/signup/", payload)

    assert response.status_code == 302
    user = get_user_model().objects.get(email="first@example.com")
    assert user.username == "first@example.com"
    assert user.first_name == "같은 이름"

    client.logout()
    other = client.post("/accounts/signup/", {**payload, "email": "second@example.com"})
    assert other.status_code == 302
    assert get_user_model().objects.filter(first_name="같은 이름").count() == 2


def test_signup_page_and_login_use_email_id_labels(client):
    signup = client.get("/accounts/signup/").content.decode()
    login = client.get("/accounts/login/").content.decode()

    assert 'placeholder="name@example.com"' in signup
    assert "아이디(이메일)" in signup
    assert "아이디(이메일)" in login
    assert "data-password-match-error" in signup


def test_delete_room_removes_case_report_and_conversation(client):
    user = get_user_model().objects.create_user("owner@example.com", "owner@example.com", "Safe-password-123!")
    case = ContractCase.objects.create(user=user, title="삭제할 채팅방")
    report = Report.objects.create(case=case, version=1, content_json={}, source_snapshot={})
    conversation = Conversation.objects.create(
        user=user, case=case, state={}, expires_at=timezone.now() + timedelta(days=1),
    )
    client.force_login(user)
    session = client.session
    session["lens_case_id"] = str(case.pk)
    session["lens_conversation_id"] = str(conversation.pk)
    session.save()

    response = client.post(f"/cases/{case.pk}/delete/")

    assert response.status_code == 302
    assert not ContractCase.objects.filter(pk=case.pk).exists()
    assert not Report.objects.filter(pk=report.pk).exists()
    assert not Conversation.objects.filter(pk=conversation.pk).exists()
    assert "lens_case_id" not in client.session


def test_delete_room_from_chat_returns_to_chat(client):
    user = get_user_model().objects.create_user("owner@example.com", "owner@example.com", "Safe-password-123!")
    case = ContractCase.objects.create(user=user, title="삭제할 채팅방")
    client.force_login(user)

    response = client.post(f"/cases/{case.pk}/delete/", {"next": "chat"})

    assert response.status_code == 302
    assert response.url == "/"
    assert "lens_conversation_id" not in client.session


def test_mypage_hides_empty_report_room_and_shows_room_after_report(client):
    user = get_user_model().objects.create_user("owner@example.com", "owner@example.com", "Safe-password-123!")
    empty_case = ContractCase.objects.create(user=user, title="리포트 없는 채팅방")
    report_case = ContractCase.objects.create(user=user, title="리포트 있는 채팅방")
    client.force_login(user)

    before = client.get("/cases/").content.decode()
    assert 'class="report-folder"' not in before

    Report.objects.create(case=report_case, version=1, content_json={}, source_snapshot={})
    after = client.get("/cases/").content.decode()
    assert after.count('class="report-folder"') == 1
    assert "리포트 있는 채팅방" in after
    assert "리포트 없는 채팅방" not in after[after.index("채팅방별 리포트 파일"):]
    assert ContractCase.objects.filter(pk=empty_case.pk).exists()
