"""Conversation routing through Django with isolated model and retrieval doubles."""
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock
import json
import uuid

import pytest
from django.test import Client

from chat import dialogue_planner, services
from chat.dialogue_contract import Decision
from chat.dialogue_planner import PlanningError, PlanningResult
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


def post(client, message, *, request_id=None, **extra):
    payload = {
        "conversation_id": current(client)["conversation_id"],
        "request_id": request_id or str(uuid.uuid4()), "message": message, **extra,
    }
    return client.post(
        "/api/chat/", data=json.dumps(payload), content_type="application/json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )


def proposal(**changes):
    decision = Decision(
        intent="question", action="rag", topic="보증금반환", topic_changed=False,
        updates={}, clarify_field=None, question=None,
        search_query="보증금 반환 절차를 알려주세요.", document_id=None, style="standard",
    )
    return PlanningResult(
        decision=replace(decision, **changes), elapsed_seconds=0.125,
        model_invocations=1, output_tokens=40,
    )


@pytest.fixture
def calls(monkeypatch):
    answer = Answer(question="검사된 질문", status="answered", text="검증된 공개 답변", raw_text="PRIVATE_GRAPH_RAW")
    official = Mock(return_value=answer)
    document = Mock(return_value=answer)
    loader = Mock()
    loader.result.return_value = object()
    loader_factory = Mock(return_value=loader)
    planner = Mock(side_effect=AssertionError("unexpected planner invocation"))
    resolver = Mock(side_effect=lambda question, messages: SimpleNamespace(standalone=question, used_history=False))
    legacy = Mock(wraps=services._respond_legacy)
    monkeypatch.setattr(dialogue_planner, "plan_turn", planner)
    monkeypatch.setattr(services.graph, "answer_question", official)
    monkeypatch.setattr(services.graph, "answer_document_question", document)
    monkeypatch.setattr(services, "retrieval_loader", loader_factory)
    monkeypatch.setattr(services, "resolve_question", resolver)
    monkeypatch.setattr(services, "_respond_legacy", legacy)
    return SimpleNamespace(
        official=official, document=document, loader=loader, loader_factory=loader_factory,
        planner=planner, resolver=resolver, legacy=legacy,
    )


def plan(calls, **changes):
    result = proposal(**changes)
    calls.planner.side_effect = None
    calls.planner.return_value = result
    return result


def test_guidance_followup_round_trip(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    plan(calls, clarify_field="end_date")
    response = post(browser, "보증금 반환 상담을 하고 싶어요.")
    assert response.status_code == 200
    state = current(browser)
    pending = state["conversation"]["pending"]
    assert pending["mode"] == "after_answer"
    assert state["messages"][-1]["followup_question"] == pending["question"]
    assert "request" not in pending
    plan(calls, intent="clarification_answer", updates={
        "end_date": {"value": "다음 달 말", "evidence": "다음 달 말"},
    })
    response = post(browser, "다음 달 말이에요.", reply_to=pending["message_id"])
    assert response.status_code == 200
    assert "계약 종료일: 다음 달 말" in calls.official.call_args.args[0]
    assert current(browser)["conversation"]["pending"] is None


def owned_document(client):
    conversation = Conversation.objects.get(pk=current(client)["conversation_id"])
    extraction = ExtractionResult(
        pages=(PageExtraction(1, "임대차계약서 보증금 삼천만원 특약", "embedded_text", 30),),
        elapsed_seconds=0.1,
    )
    context = build_session_document_context(
        "contract.pdf", extraction, str(conversation.pk),
        document_id="doc-owned", document_kind="임대차계약서",
    )
    conversation.state["documents"].append({
        "document_id": "doc-owned", "kind": "contract", "label": "임대차계약서",
        "filename": "contract.pdf", "context": asdict(context), "checksum": "private-checksum",
    })
    conversation.save(update_fields=["state"])
    return "doc-owned"


def test_disabled_feature_uses_the_existing_graph_and_message_contract(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = False
    before_dialogue = deepcopy(Conversation.objects.get().state["dialogue"])

    response = post(browser, "보증금 반환 절차를 알려주세요.")

    assert response.status_code == 200
    message = response.json()["messages"][-1]
    assert message["status"] == "answered"
    assert message["content"] == "검증된 공개 답변"
    assert "action" not in message and "intent" not in message
    assert "context_content" not in message
    assert "PRIVATE_GRAPH_RAW" not in response.content.decode()
    assert Conversation.objects.get().state["dialogue"] == before_dialogue
    calls.legacy.assert_called_once()
    calls.official.assert_called_once_with("보증금 반환 절차를 알려주세요.", service=calls.loader.result.return_value)
    calls.document.assert_not_called()
    calls.planner.assert_not_called()


def test_absent_feature_setting_also_defaults_to_legacy(browser, calls, settings, monkeypatch):
    monkeypatch.delattr(settings, "CHAT_CONVERSATION_ENABLED", raising=False)

    response = post(browser, "보증금 반환 절차를 알려주세요.")

    assert response.status_code == 200
    assert "action" not in response.json()["messages"][-1]
    calls.legacy.assert_called_once()
    calls.official.assert_called_once()
    calls.planner.assert_not_called()


@pytest.mark.parametrize("action,intent,status,field", [
    ("social", "greeting", "social", None),
    ("clarify", "question", "clarify", "contract_ended"),
    ("refuse", "question", "refused", None),
])
def test_enabled_nonrag_routes_publish_status_and_never_load_retrieval(browser, calls, settings, action, intent, status, field):
    settings.CHAT_CONVERSATION_ENABLED = True
    plan(calls, action=action, intent=intent, clarify_field=field, search_query="")

    response = post(browser, "안녕하세요." if action == "social" else "제 상황을 설명할게요.")

    assert response.status_code == 200
    message = response.json()["messages"][-1]
    assert message["action"] == action
    assert message["intent"] == intent
    assert message["status"] == status
    assert message["content"].strip()
    assert message["sources"] == []
    assert "dialogue" not in response.json()
    assert "normalizations" not in message
    calls.planner.assert_called_once()
    calls.legacy.assert_not_called()
    calls.official.assert_not_called()
    calls.document.assert_not_called()
    calls.loader_factory.assert_not_called()


def test_enabled_rag_uses_validated_query_and_saves_user_facts_once(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    planned = plan(calls, updates={"contract_type": {"value": "월세", "evidence": "월세"}},
                   search_query="계약 유형: 월세\n사용자 입력: 월세 보증금 반환을 문의해요.")

    response = post(browser, "월세 보증금 반환을 문의해요.")

    assert response.status_code == 200
    message = response.json()["messages"][-1]
    assert (message["status"], message["action"], message["intent"]) == ("answered", "rag", "question")
    assert message["content"] == "검증된 공개 답변"
    assert "PRIVATE_GRAPH_RAW" not in response.content.decode()
    saved = Conversation.objects.get().state
    assert saved["dialogue"]["turn"] == 1
    assert saved["dialogue"]["facts"]["contract_type"]["value"] == "월세"
    assert saved["dialogue"]["facts"]["contract_type"]["source_turn"] == 1
    assert len(saved["messages"]) == 2
    assert saved["dialogue"]["last_answer"]["content"] == "검증된 공개 답변"
    assert "PRIVATE_GRAPH_RAW" not in json.dumps(saved["dialogue"], ensure_ascii=False)
    calls.planner.assert_called_once()
    calls.official.assert_called_once()
    actual_query = calls.official.call_args.args[0]
    assert actual_query.endswith("월세 보증금 반환을 문의해요.")
    assert "계약 유형: 월세" in actual_query and "보증금반환" in actual_query
    assert calls.official.call_args.kwargs == {"service": calls.loader.result.return_value, "response_style": "standard"}
    calls.document.assert_not_called()
    calls.legacy.assert_not_called()
    calls.resolver.assert_not_called()


@pytest.mark.parametrize("status", ["abstained", "refused"])
def test_rag_keeps_existing_answer_status_and_does_not_remember_rejected_text(browser, calls, settings, status):
    settings.CHAT_CONVERSATION_ENABLED = True
    plan(calls, updates={"contract_type": {"value": "월세", "evidence": "월세"}})
    calls.official.return_value = Answer(question="q", status=status, text="확인할 근거가 필요합니다.", raw_text="PRIVATE_REJECTED")

    response = post(browser, "월세 보증금 반환을 문의해요.")

    assert response.status_code == 200
    message = response.json()["messages"][-1]
    assert message["status"] == status
    assert message["action"] == ("refuse" if status == "refused" else "rag")
    assert "PRIVATE_REJECTED" not in response.content.decode()
    dialogue = Conversation.objects.get().state["dialogue"]
    if status == "refused":
        assert dialogue["facts"] == {}
        assert dialogue["turn"] == 0
        assert dialogue["history"] == []
    else:
        assert dialogue["facts"]["contract_type"]["value"] == "월세"
        assert dialogue["turn"] == 1
    assert dialogue["last_answer"] is None
    assert "PRIVATE_REJECTED" not in json.dumps(dialogue, ensure_ascii=False)
    calls.official.assert_called_once()
    calls.legacy.assert_not_called()


def test_enabled_duplicate_request_has_one_planner_and_one_answer(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    plan(calls)
    request_id = str(uuid.uuid4())

    first = post(browser, "보증금 반환 절차를 알려주세요.", request_id=request_id)
    second = post(browser, "보증금 반환 절차를 알려주세요.", request_id=request_id)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    saved = Conversation.objects.get().state
    assert saved["dialogue"]["turn"] == 1
    assert len(saved["messages"]) == 2
    assert saved["completed_requests"] == [request_id]
    calls.planner.assert_called_once()
    calls.official.assert_called_once()
    calls.legacy.assert_not_called()


@pytest.mark.parametrize("code", ["invalid_decision:json", "truncated"])
def test_planning_failure_returns_confirmation_without_rag_or_internal_details(browser, calls, settings, code):
    settings.CHAT_CONVERSATION_ENABLED = True
    calls.planner.side_effect = PlanningError(code)
    before = deepcopy(Conversation.objects.get().state["dialogue"])
    response = post(browser, "보증금 반환 절차를 알려주세요.")
    assert response.status_code == 200
    message = response.json()["messages"][-1]
    assert (message["status"], message["action"], message["intent"]) == ("clarify", "clarify", None)
    assert code not in response.content.decode()
    saved = Conversation.objects.get().state
    assert saved["dialogue"]["facts"] == before["facts"]
    assert saved["dialogue"]["history"] == before["history"]
    assert saved["dialogue"]["last_answer"] == before["last_answer"]
    assert len(saved["messages"]) == 2
    assert saved["dialogue_runtime"]["path"] == "clarification_recovery"
    assert "dialogue_runtime" not in response.json()
    calls.planner.assert_called_once()
    calls.legacy.assert_not_called()
    calls.official.assert_not_called()


def test_unavailable_planner_is_retryable_without_changing_conversation(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    calls.planner.side_effect = PlanningError("model_unavailable")
    before = deepcopy(Conversation.objects.get().state)
    response = post(browser, "보증금 반환 절차를 알려주세요.")
    assert response.status_code == 503
    assert Conversation.objects.get().state == before
    calls.official.assert_not_called()
    calls.legacy.assert_not_called()


def test_duplicate_failed_plan_does_not_repeat_the_confirmation(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    calls.planner.side_effect = PlanningError("invalid_decision:keys")
    request_id = str(uuid.uuid4())
    first = post(browser, "보증금 반환 절차를 알려주세요.", request_id=request_id)
    second = post(browser, "보증금 반환 절차를 알려주세요.", request_id=request_id)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    calls.planner.assert_called_once()
    calls.legacy.assert_not_called()
    calls.official.assert_not_called()


def test_graph_failure_does_not_repeat_generation_or_commit_pending_facts(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    plan(calls, updates={"contract_type": {"value": "월세", "evidence": "월세"}})
    before = deepcopy(Conversation.objects.get().state)
    calls.official.side_effect = RuntimeError("PRIVATE_GRAPH_FAILURE")
    request_id = str(uuid.uuid4())

    response = post(browser, "월세 보증금 반환을 문의해요.", request_id=request_id)

    assert response.status_code == 503
    assert "PRIVATE_GRAPH_FAILURE" not in response.content.decode()
    saved = Conversation.objects.get()
    assert saved.state == before
    assert saved.lease_token is None
    calls.planner.assert_called_once()
    calls.official.assert_called_once()
    calls.legacy.assert_not_called()


def test_csrf_rejection_happens_before_enabled_planner(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True

    response = browser.post("/api/chat/", data=json.dumps({
        "conversation_id": current(browser)["conversation_id"],
        "request_id": str(uuid.uuid4()), "message": "보증금 반환 절차",
    }), content_type="application/json")

    assert response.status_code == 403
    calls.planner.assert_not_called()
    calls.official.assert_not_called()


def test_another_browser_cannot_plan_against_the_owners_session(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    other = Client(enforce_csrf_checks=True)
    assert other.get("/").status_code == 200

    response = post(other, "보증금 반환 절차", conversation_id=current(browser)["conversation_id"])

    assert response.status_code == 409
    assert current(browser)["messages"] == []
    assert current(other)["messages"] == []
    calls.planner.assert_not_called()
    calls.official.assert_not_called()


def test_foreign_document_is_rejected_before_planning(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    document_id = owned_document(browser)
    other = Client(enforce_csrf_checks=True)
    assert other.get("/").status_code == 200

    response = post(other, "이 문서 보증금을 알려주세요.", document_id=document_id)

    assert response.status_code == 404
    assert current(other)["messages"] == []
    calls.planner.assert_not_called()
    calls.document.assert_not_called()


def test_owned_document_uses_existing_document_graph_and_keeps_context_private(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    document_id = owned_document(browser)
    plan(calls, intent="document_question", topic="문서확인", document_id=document_id,
         search_query="임대차계약서에 적힌 보증금 금액")

    response = post(browser, "이 임대차계약서 보증금이 얼마인가요?", document_id=document_id)

    assert response.status_code == 200
    message = response.json()["messages"][-1]
    assert (message["status"], message["action"], message["intent"]) == ("answered", "rag", "document_question")
    assert "삼천만원" not in response.content.decode()
    assert "private-checksum" not in response.content.decode()
    calls.planner.assert_called_once()
    calls.document.assert_called_once()
    assert calls.document.call_args.args[1][0].document_id == document_id
    calls.official.assert_not_called()
    calls.legacy.assert_not_called()
    assert Conversation.objects.get().state["dialogue"]["active_document_id"] == document_id


def test_next_http_request_reads_saved_facts_without_reattributing_them(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    snapshots = []
    decisions = [
        proposal(updates={"contract_type": {"value": "월세", "evidence": "월세"}}),
        proposal(intent="followup", search_query="월세 보증금 반환에 필요한 서류"),
    ]

    def capture(state, question, document_id=None):
        snapshots.append(deepcopy(state["dialogue"]))
        return decisions[len(snapshots) - 1]

    calls.planner.side_effect = capture

    first = post(browser, "월세 보증금 반환을 문의해요.")
    second = post(browser, "그럼 서류는 무엇을 준비하나요?")

    assert first.status_code == second.status_code == 200
    assert snapshots[0]["facts"] == {}
    assert snapshots[1]["facts"]["contract_type"]["value"] == "월세"
    assert snapshots[1]["history"][-1]["content"] == "월세 보증금 반환을 문의해요."
    saved = Conversation.objects.get().state["dialogue"]
    assert saved["turn"] == 2
    assert saved["facts"]["contract_type"] == snapshots[1]["facts"]["contract_type"]
    assert saved["facts"]["contract_type"]["source_turn"] == 1
    assert second.json()["messages"][-1]["intent"] == "followup"
    assert second.json()["messages"][-1]["used_history"] is True
    assert calls.planner.call_count == calls.official.call_count == 2
    calls.legacy.assert_not_called()


def test_pending_question_resumes_after_reload_without_reclassifying_offered_answer(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    plan(calls, action="clarify", clarify_field="contract_ended", search_query="")
    first = post(browser, "보증금 반환을 문의해요.")
    assert first.json()["messages"][-1]["reason"] == "needs_information"
    assert browser.get("/").status_code == 200
    calls.planner.side_effect = AssertionError("Offered answer must not need another planner")
    second = post(browser, "모르겠어요")
    assert second.status_code == 200
    saved = Conversation.objects.get().state
    assert saved["dialogue"]["facts"]["contract_ended"]["value"] == "모름"
    assert saved["dialogue"]["pending"] is None
    assert len(saved["messages"]) == 4
    calls.planner.assert_called_once()
    calls.official.assert_called_once()


def test_unrelated_reply_cannot_repeat_the_same_confirmation_forever(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    plan(calls, action="clarify", clarify_field="contract_ended", search_query="")
    for text in ["보증금 상담할게요", "질문을 이해하지 못했어요"]:
        assert post(browser, text).json()["messages"][-1]["status"] == "clarify"
    third = post(browser, "아직 답하기 어려워요")
    assert third.json()["messages"][-1]["action"] == "rag"
    assert Conversation.objects.get().state["dialogue"]["pending"] is None
    assert "contract_ended" not in Conversation.objects.get().state["dialogue"]["facts"]


def test_ambiguous_document_asks_before_model_or_retrieval_and_choice_resumes(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    original = owned_document(browser)
    conversation = Conversation.objects.get()
    second = deepcopy(conversation.state["documents"][0])
    second.update(document_id="doc-second", filename="second.pdf")
    conversation.state["documents"].append(second)
    conversation.save(update_fields=["state"])
    result = post(browser, "이 계약서의 보증금을 알려주세요")
    assert result.status_code == 200
    assert result.json()["messages"][-1]["status"] == "clarify"
    assert {c["document_id"] for c in result.json()["messages"][-1]["choices"]} == {original, "doc-second"}
    calls.planner.assert_not_called()
    calls.document.assert_not_called()
    plan(calls, intent="document_question", document_id=original)
    result = post(browser, "선택한 문서를 설명해주세요", document_id=original)
    assert result.status_code == 200
    assert Conversation.objects.get().state["dialogue"]["active_document_id"] == original
    calls.document.assert_called_once()


def test_document_recovery_offers_selected_owned_document_without_answering(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    selected = owned_document(browser)
    calls.planner.side_effect = PlanningError("invalid_decision:changed_literal_value")
    result = post(browser, "올린 계약서의 보증금이 얼마인가요?")
    assert result.status_code == 200
    pending = Conversation.objects.get().state["dialogue"]["pending"]
    assert all(c["document_id"] == selected for c in pending["choices"])
    assert not Conversation.objects.get().state["dialogue"]["facts"]
    calls.document.assert_not_called()


def test_different_document_amounts_are_not_combined_as_one_fact(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    owned_document(browser)
    conversation = Conversation.objects.get()
    extraction = ExtractionResult(pages=(PageExtraction(1, "임대차계약서 보증금 사백만원 특약", "embedded_text", 30),), elapsed_seconds=0.1)
    context = build_session_document_context("second.pdf", extraction, str(conversation.pk), document_id="doc-b", document_kind="임대차계약서")
    conversation.state["documents"].append({"document_id": "doc-b", "kind": "contract", "label": "임대차계약서", "filename": "second.pdf", "context": asdict(context), "checksum": "second"})
    conversation.save(update_fields=["state"])
    plan(calls, intent="document_question", document_id="doc-b")
    result = post(browser, "2번 문서에 적힌 보증금은 얼마인가요?")
    assert result.status_code == 200
    evidence = calls.document.call_args.args[1]
    assert evidence and all(item.document_id == "doc-b" for item in evidence)
    assert "사백만원" in " ".join(item.text for item in evidence)
    assert "삼천만원" not in " ".join(item.text for item in evidence)
    assert "deposit" not in Conversation.objects.get().state["dialogue"]["facts"]


def test_general_question_keeps_document_available_without_using_it(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    selected = owned_document(browser)
    conversation = Conversation.objects.get()
    conversation.state["dialogue"]["active_document_id"] = selected
    conversation.save(update_fields=["state"])
    plan(calls, intent="question", topic="대항력")
    result = post(browser, "대항력의 의미를 알려주세요.")
    assert result.status_code == 200
    calls.official.assert_called_once()
    calls.document.assert_not_called()
    assert "선택 문서:" not in calls.official.call_args.args[0]
    assert Conversation.objects.get().state["dialogue"]["active_document_id"] == selected


@pytest.mark.parametrize("style", ["simple", "brief"])
def test_style_reaches_generation_and_public_body_is_not_rewritten(browser, calls, settings, style):
    settings.CHAT_CONVERSATION_ENABLED = True
    plan(calls, intent="explain", style=style)
    result = post(browser, "보증금 반환 설명을 더 쉽게 알려주세요")
    assert result.status_code == 200
    assert calls.official.call_args.kwargs["response_style"] == style
    assert result.json()["messages"][-1]["content"] == "검증된 공개 답변"


def test_pending_view_has_only_public_choices_and_stale_choice_is_rejected(browser, calls, settings):
    settings.CHAT_CONVERSATION_ENABLED = True
    plan(calls, action="clarify", clarify_field="contract_ended", search_query="")
    first = post(browser, "보증금 반환을 문의해요")
    pending = first.json()["conversation"]["pending"]
    assert set(pending) == {"message_id", "question", "choices"}
    assert pending["message_id"] == first.json()["messages"][-1]["id"]
    second = post(browser, "예", reply_to=pending["message_id"])
    assert second.status_code == 200
    before = deepcopy(Conversation.objects.get().state)
    stale = post(browser, "아니요", reply_to=pending["message_id"])
    assert stale.status_code == 409
    assert Conversation.objects.get().state == before
    calls.planner.assert_called_once()
    calls.official.assert_called_once()


def test_member_room_preserves_followup_and_reset_clears_dialogue(client, calls, settings, monkeypatch):
    from django.contrib.auth import get_user_model
    from chat.models import Message
    settings.CHAT_CONVERSATION_ENABLED = True
    user = get_user_model().objects.create_user('dialogue-member', password='test-only-password')
    client.force_login(user)
    assert client.get('/').status_code == 200
    monkeypatch.setattr('cases.services.conversation_guidance.refresh_conversation_guidance', lambda *a, **k: {})
    plan(calls, topic='계약갱신', purpose='procedure', clarify_field='end_date')
    first = post(client, '월세집 계약 갱신은 어떻게 해?')
    assert first.status_code == 200 and first.json()['room_created']
    conversation = Conversation.objects.get(pk=current(client)['conversation_id'])
    assert conversation.case_id and conversation.user_id == user.pk
    assert Message.objects.get(conversation=conversation, role='assistant').metadata['followup_question']
    plan(calls, intent='clarification_answer', topic='계약갱신', clarify_field='landlord_notified',
         updates={'end_date': {'value': '2026년 12월 31일', 'evidence': '2026년 12월 31일'}})
    second = post(client, '2026년 12월 31일이야')
    assert second.status_code == 200
    assert second.json()['messages'][-1]['action'] == 'clarify'
    conversation.refresh_from_db()
    assert conversation.state['dialogue']['facts']['end_date']['value'] == '2026년 12월 31일'
    result = client.post('/api/reset/', data=json.dumps({'conversation_id': str(conversation.pk), 'request_id': str(uuid.uuid4())}), content_type='application/json')
    assert result.status_code == 200
    conversation.refresh_from_db()
    assert conversation.case_id
    assert conversation.state['dialogue'] == services.empty_dialogue()
    assert not Message.objects.filter(conversation=conversation).exists()
