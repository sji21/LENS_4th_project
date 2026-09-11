"""Web contracts: session isolation, CSRF, failures, and unchanged RAG boundaries."""
import json
import uuid
from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from chat import services
from chat.models import Conversation
from src.document_check.extraction_models import ExtractionResult, PageExtraction
from src.document_check.session_retrieval import build_session_document_context
from src.generation.models import Answer

pytestmark = pytest.mark.django_db


@pytest.fixture
def browser():
    client = Client(enforce_csrf_checks=True)
    assert client.get("/").status_code == 200
    return client


def current(client):
    return client.get("/api/state/").json()


def post(client, url, data=None, request_id=None):
    payload = {"conversation_id": current(client)["conversation_id"], "request_id": request_id or str(uuid.uuid4()), **(data or {})}
    return client.post(url, data=json.dumps(payload), content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)


def attach(client, name="contract.pdf", content=b"%PDF-fake", request_id=None):
    return client.post("/api/documents/", {
        "conversation_id": current(client)["conversation_id"], "request_id": request_id or str(uuid.uuid4()),
        "file": SimpleUploadedFile(name, content, content_type="application/pdf"),
    }, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)


@pytest.fixture
def model_calls(monkeypatch):
    loader = Mock()
    loader.result.return_value = object()
    monkeypatch.setattr(services, "retrieval_loader", lambda: loader)
    answer = Answer(question="질문", status="answered", text="검증된 답변", raw_text="대화용 본문")
    official = Mock(return_value=answer)
    document = Mock(return_value=answer)
    monkeypatch.setattr(services.graph, "answer_question", official)
    monkeypatch.setattr(services.graph, "answer_document_question", document)
    monkeypatch.setattr(services, "resolve_question", lambda q, messages: SimpleNamespace(standalone=q, used_history=False))
    return official, document


@pytest.fixture
def extracted_document(monkeypatch):
    extraction = ExtractionResult(pages=(PageExtraction(1, "임대차계약서 보증금 금액 삼천만원 특약", "embedded_text", 30),), elapsed_seconds=0.1)
    analysis = SimpleNamespace(to_public_dict=lambda: {"headline": "항목 확인", "summary": "원문과 대조하세요", "fields": []})
    classified = SimpleNamespace(extraction=extraction, classification=SimpleNamespace(kind="contract", confidence="high"), analysis=analysis)
    monkeypatch.setattr(services, "analyze_uploaded_document", Mock(return_value=classified))
    return classified


def test_html_has_csrf_form_static_assets_and_no_streamlit(browser):
    html = browser.get("/").content.decode()
    assert 'name="csrfmiddlewaretoken"' in html
    assert '/static/chat/app.js' in html and '/static/chat/app.css' in html
    assert "streamlit" not in html.lower()


def test_csrf_required_for_every_mutation(browser):
    for url in ["/api/chat/", "/api/reset/", "/api/documents/", "/api/documents/abc/delete/", "/api/readiness/retry/"]:
        result = browser.post(url)
        assert result.status_code == 403
        assert "error" in result.json()


def test_chat_calls_existing_graph_and_does_not_expose_context(browser, model_calls):
    response = post(browser, "/api/chat/", {"message": "대항력은 언제 생기나요?"})
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert messages[-1]["content"] == "검증된 답변"
    assert "context_content" not in messages[-1]
    assert messages[-1]["status"] == "answered"
    model_calls[0].assert_called_once()
    model_calls[1].assert_not_called()


@pytest.mark.parametrize("status", ["abstained", "refused"])
def test_rejected_raw_output_is_never_returned(browser, model_calls, status):
    model_calls[0].return_value = Answer(question="q", status=status, text="보류 안내", raw_text="DO NOT EXPOSE")
    result = post(browser, "/api/chat/", {"message": "질문"})
    assert "DO NOT EXPOSE" not in result.content.decode()
    saved = Conversation.objects.get().state["messages"][-1]
    assert saved["context_content"] == ""
    assert saved["status"] == status


def test_empty_answer_does_not_fall_back_to_raw(browser, model_calls):
    model_calls[0].return_value = Answer(question="q", status="abstained", text="", raw_text="UNVERIFIED")
    result = post(browser, "/api/chat/", {"message": "질문"})
    assert "UNVERIFIED" not in result.content.decode()


def test_duplicate_request_does_not_generate_twice(browser, model_calls):
    request_id = str(uuid.uuid4())
    first = post(browser, "/api/chat/", {"message": "질문"}, request_id)
    second = post(browser, "/api/chat/", {"message": "질문"}, request_id)
    assert first.json() == second.json()
    model_calls[0].assert_called_once()


def test_failure_unlocks_session_and_does_not_leak_exception(browser, model_calls):
    model_calls[0].side_effect = RuntimeError("secret document raw")
    result = post(browser, "/api/chat/", {"message": "질문"})
    assert result.status_code == 503
    assert "secret document raw" not in result.content.decode()
    assert Conversation.objects.get().lease_token is None
    model_calls[0].side_effect = None
    assert post(browser, "/api/chat/", {"message": "다시 질문"}).status_code == 200


def test_busy_session_rejects_parallel_write(browser, model_calls):
    Conversation.objects.update(busy_until=timezone.now() + timedelta(minutes=2))
    assert post(browser, "/api/chat/", {"message": "질문"}).status_code == 409
    assert current(browser)["busy"] is True
    model_calls[0].assert_not_called()


def test_expired_lease_can_be_recovered(browser, model_calls):
    Conversation.objects.update(busy_until=timezone.now() - timedelta(seconds=1), lease_token=uuid.uuid4())
    assert post(browser, "/api/chat/", {"message": "질문"}).status_code == 200


def test_expired_worker_cannot_save_answer(browser, model_calls):
    answer = model_calls[0].return_value

    def expire(*args, **kwargs):
        Conversation.objects.update(busy_until=timezone.now() - timedelta(seconds=1))
        return answer

    model_calls[0].side_effect = expire
    result = post(browser, "/api/chat/", {"message": "질문"})
    assert result.status_code == 409
    assert Conversation.objects.get().state["messages"] == []


def test_lost_owner_does_not_unlock_successor(browser, model_calls):
    successor = uuid.uuid4()
    answer = model_calls[0].return_value

    def replace(*args, **kwargs):
        Conversation.objects.update(lease_token=successor)
        return answer

    model_calls[0].side_effect = replace
    assert post(browser, "/api/chat/", {"message": "질문"}).status_code == 409
    row = Conversation.objects.get()
    assert row.lease_token == successor
    assert row.busy_until > timezone.now()
    assert row.state["messages"] == []


def test_session_cannot_access_another_conversation(browser, model_calls):
    other = Client(enforce_csrf_checks=True)
    other.get("/")
    result = post(other, "/api/chat/", {"message": "침범", "conversation_id": current(browser)["conversation_id"]})
    assert result.status_code == 409
    assert current(other)["messages"] == []
    assert current(browser)["messages"] == []
    model_calls[0].assert_not_called()


def test_no_cookie_and_expired_cookie_cannot_read_history(browser):
    assert Client().get("/api/state/").status_code == 410
    old_id = current(browser)["conversation_id"]
    Conversation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert browser.get("/api/state/").status_code == 410
    browser.get("/")
    assert current(browser)["conversation_id"] != old_id
    assert not Conversation.objects.filter(pk=old_id).exists()


def test_document_upload_and_private_context(browser, extracted_document):
    response = attach(browser)
    assert response.status_code == 200
    document = response.json()["documents"][0]
    assert document["kind"] == "contract" and document["page_count"] == 1
    assert "context" not in document and "checksum" not in document
    assert "삼천만원" not in response.content.decode()
    assert len(Conversation.objects.get().state["documents"][0]["context"]["chunks"]) == 1
    assert len(attach(browser).json()["documents"]) == 1


def test_uploaded_document_routes_to_document_graph(browser, extracted_document, model_calls):
    doc = attach(browser).json()["documents"][0]
    response = post(browser, "/api/chat/", {"message": "이 계약서 보증금이 얼마야?", "document_id": doc["document_id"]})
    assert response.status_code == 200
    model_calls[1].assert_called_once()
    assert model_calls[1].call_args.args[1][0].document_id == doc["document_id"]
    model_calls[0].assert_not_called()


def test_foreign_document_id_is_rejected(browser, extracted_document, model_calls):
    doc_id = attach(browser).json()["documents"][0]["document_id"]
    other = Client(enforce_csrf_checks=True); other.get("/")
    assert post(other, "/api/chat/", {"message": "문서 확인", "document_id": doc_id}).status_code == 404
    assert post(other, f"/api/documents/{doc_id}/delete/").status_code == 404
    model_calls[1].assert_not_called()


def test_delete_document_clears_followup_context(browser, extracted_document, model_calls):
    doc_id = attach(browser).json()["documents"][0]["document_id"]
    post(browser, "/api/chat/", {"message": "질문"})
    response = post(browser, f"/api/documents/{doc_id}/delete/")
    assert response.status_code == 200
    assert response.json()["documents"] == [] and response.json()["messages"] == []


def test_reset_keeps_auth_session_and_erases_chat(browser, extracted_document, model_calls):
    session = browser.session; session["unrelated_auth_extension"] = "keep"; session.save()
    attach(browser); post(browser, "/api/chat/", {"message": "질문"})
    response = post(browser, "/api/reset/")
    assert response.json()["messages"] == [] and response.json()["documents"] == []
    assert browser.session["unrelated_auth_extension"] == "keep"


def test_upload_limits_and_unknown_documents(browser, extracted_document, settings):
    assert attach(browser, "bad.html", b"<script>").status_code == 400
    assert attach(browser, content=b"x" * (20 * 1024 * 1024 + 1)).status_code == 413
    settings.CHAT_MAX_DOCUMENTS = 1
    assert attach(browser).status_code == 200
    assert attach(browser, content=b"%PDF-another").status_code == 400


def test_real_parser_rejects_invalid_pdf(browser):
    response = attach(browser, content=b"not a PDF")
    assert response.status_code == 400
    assert current(browser)["documents"] == []


@pytest.mark.parametrize("message", ["", " ", 32, ["hello"], "x" * 2001])
def test_message_validation(browser, model_calls, message):
    assert post(browser, "/api/chat/", {"message": message}).status_code == 400
    model_calls[0].assert_not_called()


def test_followup_receives_previous_answered_context(browser, model_calls, monkeypatch):
    post(browser, "/api/chat/", {"message": "첫 질문"})
    resolver = Mock(return_value=SimpleNamespace(standalone="이전 문맥을 포함한 질문", used_history=True))
    monkeypatch.setattr(services, "resolve_question", resolver)
    response = post(browser, "/api/chat/", {"message": "그럼 언제?"})
    assert resolver.call_args.args[1][-1]["context_content"] == "대화용 본문"
    assert response.json()["messages"][-1]["used_history"] is True
    assert model_calls[0].call_args.args[0] == "이전 문맥을 포함한 질문"


def test_private_input_is_masked_before_storage_and_graph(browser, model_calls):
    response = post(browser, "/api/chat/", {"message": "전화 010-1234-5678 관련 질문"})
    assert "010-1234-5678" not in response.content.decode()
    assert "010-1234-5678" not in model_calls[0].call_args.args[0]


def test_purge_command_removes_only_expired(browser):
    from django.core.management import call_command
    expired = Conversation.objects.create(state={"documents": ["private"]}, expires_at=timezone.now() - timedelta(seconds=1))
    call_command("purge_chats")
    assert not Conversation.objects.filter(pk=expired.pk).exists()
    assert Conversation.objects.count() == 1


def test_answer_carries_display_extras_without_new_retrieval(browser, model_calls):
    """용어·인용·후속질문은 이미 확정된 answer에서만 나온다. 검색은 한 번뿐이다."""

    law = SimpleNamespace(rank=1, chunk_id="law-3", doc_type="law",
                          citation="주택임대차보호법 제3조", score=1.0,
                          source_url="https://law.go.kr/x",
                          text="① 임차인은 주택의 인도와 주민등록을 마친 때에는 그 다음 날부터 효력이 생긴다.")
    other = SimpleNamespace(rank=2, chunk_id="law-4", doc_type="law",
                            citation="주택임대차보호법 제3조의2(보증금의 회수)", score=0.8,
                            source_url="https://law.go.kr/y", text="우선변제권 본문")
    answer = Answer(question="q", status="answered",
                    text="주택임대차보호법 제3조에 따라 대항력이 생깁니다.",
                    raw_text="주택임대차보호법 제3조에 따라 대항력이 생깁니다.",
                    laws=(law, other))
    model_calls[0].return_value = answer

    message = post(browser, "/api/chat/", {"message": "대항력"}).json()["messages"][-1]
    assert [span["term"] for span in message["glossary"]] == ["대항력"]
    assert [span["chunk_id"] for span in message["citations"]] == ["law-3"]
    assert "다음 날" in message["citations"][0]["excerpt"]
    assert message["followups"] == ["주택임대차보호법 제3조의2"]
    assert message["simplified"] == ""
    model_calls[0].assert_called_once()


@pytest.mark.parametrize("status", ["abstained", "refused"])
def test_display_extras_stay_empty_when_answer_is_not_answered(browser, model_calls, status):
    model_calls[0].return_value = Answer(question="q", status=status, text="주택임대차보호법 제3조 안내", raw_text="")
    message = post(browser, "/api/chat/", {"message": "질문"}).json()["messages"][-1]
    assert message["citations"] == [] and message["followups"] == []


def test_uploaded_document_text_never_reaches_citations(browser, model_calls, extracted_document):
    """업로드 OCR 근거는 인용 발췌 대상이 아니다. 원문이 화면으로 나가면 안 된다."""

    attach(browser)
    model_calls[1].return_value = Answer(
        question="q", status="answered", text="첨부 문서를 확인했습니다.", raw_text="첨부 문서를 확인했습니다.",
        document_evidences=(SimpleNamespace(
            chunk_id="s1", document_id="d1", filename="contract.pdf", document_kind="임대차계약서",
            page_number=1, text="임대인 홍길동 주민등록번호 820101-1234567"),),
    )
    body = post(browser, "/api/chat/", {"message": "첨부한 계약서 확인해줘"}).content.decode()
    assert "820101" not in body and "홍길동" not in body


def test_simplify_rewrites_once_and_is_idempotent(browser, model_calls, monkeypatch):
    calls = Mock(return_value="쉬운 말 본문")
    monkeypatch.setattr(services, "simplify_answer", calls)
    post(browser, "/api/chat/", {"message": "대항력"})
    message_id = current(browser)["messages"][-1]["id"]

    first = post(browser, "/api/simplify/", {"message_id": message_id})
    assert first.status_code == 200
    assert first.json()["messages"][-1]["simplified"] == "쉬운 말 본문"

    post(browser, "/api/simplify/", {"message_id": message_id})
    calls.assert_called_once()


def test_simplify_uses_generated_body_not_the_disclaimer_text(browser, model_calls, monkeypatch):
    seen = {}
    monkeypatch.setattr(services, "simplify_answer", lambda text: seen.setdefault("text", text) or "쉬운 말")
    model_calls[0].return_value = Answer(question="q", status="answered",
                                         text="본문입니다.\n\n본 답변은 법률 자문이 아닙니다.",
                                         raw_text="본문입니다.")
    post(browser, "/api/chat/", {"message": "질문"})
    post(browser, "/api/simplify/", {"message_id": current(browser)["messages"][-1]["id"]})
    assert seen["text"] == "본문입니다."
    assert "법률 자문이 아닙니다" not in seen["text"]


def test_simplify_rejects_unknown_message_and_other_sessions(browser, model_calls):
    post(browser, "/api/chat/", {"message": "질문"})
    assert post(browser, "/api/simplify/", {"message_id": "does-not-exist"}).status_code == 404
    assert post(browser, "/api/simplify/", {"message_id": ""}).status_code == 400

    intruder = Client(enforce_csrf_checks=True)
    intruder.get("/")
    message_id = current(browser)["messages"][-1]["id"]
    assert post(intruder, "/api/simplify/", {"message_id": message_id}).status_code == 404
