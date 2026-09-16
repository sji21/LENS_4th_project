import json
import logging
import uuid
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.core.exceptions import RequestDataTooBig, TooManyFilesSent
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from src.document_check.extraction import DocumentValidationError, OcrUnavailableError
from src.generation.simplify import SimplificationError
from .models import Conversation, Message
from .leases import heartbeat
from . import services
from .dialogue_state import invalidate_document

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def api(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except ApiError as error:
            return JsonResponse({"error": error.message}, status=error.status)
        except (RequestDataTooBig, TooManyFilesSent):
            return JsonResponse({"error": "파일은 한 번에 하나씩, 20MB 이하로 첨부해 주세요."}, status=413)
        except SimplificationError as error:
            return JsonResponse({"error": str(error)}, status=422)
        except (DocumentValidationError, ValueError):
            return JsonResponse({"error": "입력 또는 문서를 처리할 수 없습니다. 형식과 내용을 확인해 주세요."}, status=400)
        except OcrUnavailableError:
            return JsonResponse({"error": "OCR을 사용할 수 없습니다. 서버의 Tesseract 설치를 확인해 주세요."}, status=503)
        except Exception as error:
            # Exception messages and local variables may contain document text or secrets.
            logger.error("Django request failed: %s", type(error).__name__)
            return JsonResponse({"error": "처리하지 못했습니다. 잠시 후 다시 시도해 주세요. 검색 준비 상태도 확인해 주세요."}, status=503)
    return wrapped


def csrf_failure(request, reason=""):
    return JsonResponse({"error": "세션 확인에 실패했습니다. 페이지를 새로고침해 주세요."}, status=403)


def current_conversation(request):
    chat_id = request.session.get("lens_conversation_id")
    if not chat_id:
        raise ApiError("세션이 없습니다. 페이지를 새로고침해 주세요.", 410)
    query = Conversation.objects.filter(pk=chat_id)
    if request.user.is_authenticated:
        query = query.filter(user=request.user).filter(
            Q(case__user=request.user) | Q(case__isnull=True, expires_at__gt=timezone.now())
        )
    else:
        query = query.filter(user__isnull=True, expires_at__gt=timezone.now())
    conversation = query.first()
    if conversation is None:
        raise ApiError("대화 세션이 만료되었습니다. 페이지를 새로고침해 주세요.", 410)
    return conversation


def selected_case(request):
    """Return only an explicitly selected room; never create one on page load."""
    from cases.models import ContractCase

    case_id = request.session.get("lens_case_id")
    return ContractCase.objects.filter(pk=case_id, user=request.user).first()


def new_conversation(request):
    kwargs = {"state": services.initial_state(), "expires_at": timezone.now() + timedelta(seconds=settings.CHAT_TTL_SECONDS)}
    if request.user.is_authenticated:
        case = selected_case(request)
        if case:
            existing = Conversation.objects.filter(case=case, user=request.user).order_by("created_at").first()
            if existing:
                request.session["lens_conversation_id"] = str(existing.pk)
                return existing
        kwargs.update(
            user=request.user, case=case,
            expires_at=timezone.now() + timedelta(days=3650) if case else timezone.now() + timedelta(days=1),
        )
    conversation = Conversation.objects.create(**kwargs)
    request.session["lens_conversation_id"] = str(conversation.pk)
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)
    return conversation


@require_GET
@never_cache
@ensure_csrf_cookie
def home(request):
    # Lazy model loading: migrations and the first HTML response never load KURE.
    Conversation.objects.filter(user__isnull=True, expires_at__lte=timezone.now()).delete()
    try:
        current_conversation(request)
    except ApiError:
        new_conversation(request)
    conversation = current_conversation(request)
    contract_cases = request.user.contract_cases.all() if request.user.is_authenticated else ()
    return render(request, "chat/index.html", {
        "contract_case": conversation.case,
        "contract_cases": contract_cases,
    })


@api
@require_GET
def state(request):
    conversation = current_conversation(request)
    payload = services.public_state(conversation)
    payload["busy"] = conversation.busy_until > timezone.now()
    return JsonResponse(payload)


@api
@require_GET
def readiness(request):
    current_conversation(request)
    snapshot = services.retrieval_loader().start().snapshot()
    return JsonResponse({"state": snapshot.state, "elapsed_seconds": round(snapshot.elapsed_seconds, 1)})


@api
@require_POST
def retry_readiness(request):
    current_conversation(request)
    return JsonResponse({"retried": services.retrieval_loader().retry()})


def json_body(request):
    if request.content_type != "application/json":
        raise ApiError("JSON 요청이 필요합니다.", 415)
    if len(request.body) > 20_000:
        raise ApiError("요청이 너무 큽니다.", 413)
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ApiError("요청 형식이 올바르지 않습니다.") from None
    if not isinstance(payload, dict):
        raise ApiError("요청 형식이 올바르지 않습니다.")
    return payload


@contextmanager
def exclusive_conversation(request, payload):
    conversation = current_conversation(request)
    if payload.get("conversation_id") != str(conversation.pk):
        raise ApiError("다른 탭에서 대화가 변경되었습니다. 새로고침해 주세요.", 409)
    try:
        request_id = str(uuid.UUID(payload.get("request_id", "")))
    except (ValueError, TypeError, AttributeError):
        raise ApiError("유효한 요청 ID가 필요합니다.") from None
    now, token = timezone.now(), uuid.uuid4()
    acquired = Conversation.objects.filter(pk=conversation.pk, busy_until__lte=now).update(
        busy_until=now + timedelta(seconds=settings.CHAT_LEASE_SECONDS), lease_token=token,
    )
    if not acquired:
        raise ApiError("이 대화의 다른 요청을 처리 중입니다. 완료 후 다시 시도해 주세요.", 409)
    conversation.refresh_from_db()
    duplicate = request_id in conversation.state.get("completed_requests", [])
    successor = None
    successor_busy_until = None
    try:
        from cases.services.attachments import storage_rollback_guard
        with heartbeat(conversation.pk, token), storage_rollback_guard(), transaction.atomic():
            yield conversation, duplicate
            if not duplicate:
                conversation.state["completed_requests"] = (conversation.state.get("completed_requests", []) + [request_id])[-32:]
            # A member draft has no persistent case yet, so retain the one-day
            # lifetime assigned at creation.  Only an actual room is long-lived.
            if conversation.case_id:
                expiry = timezone.now() + timedelta(days=3650)
            elif conversation.user_id:
                expiry = timezone.now() + timedelta(days=1)
            else:
                expiry = timezone.now() + timedelta(seconds=settings.CHAT_TTL_SECONDS)
            updated = Conversation.objects.filter(pk=conversation.pk, lease_token=token, busy_until__gt=timezone.now()).update(
                state=conversation.state, expires_at=expiry,
            )
            if not updated:
                owner = Conversation.objects.filter(pk=conversation.pk).values("lease_token", "busy_until").first()
                if owner and owner["lease_token"] not in {None, token}:
                    successor = owner["lease_token"]
                    successor_busy_until = owner["busy_until"]
                raise ApiError("요청 처리 시간이 초과되었습니다. 대화를 새로고침해 주세요.", 409)
        request.session.set_expiry(settings.SESSION_COOKIE_AGE)
    except ApiError:
        if successor:
            Conversation.objects.filter(pk=conversation.pk).update(
                lease_token=successor, busy_until=successor_busy_until,
            )
        raise
    finally:
        Conversation.objects.filter(pk=conversation.pk, lease_token=token).update(busy_until=timezone.now(), lease_token=None)


@api
@require_POST
@sensitive_post_parameters()
def send_message(request):
    payload = json_body(request)
    question = payload.get("message", "")
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ApiError("질문은 1~2,000자로 입력해 주세요.")
    question = services.safe_text(question.strip())
    room_created = False
    with exclusive_conversation(request, payload) as (conversation, duplicate):
        if not duplicate:
            if len(conversation.state["messages"]) >= settings.CHAT_MAX_MESSAGES:
                raise ApiError("대화가 길어졌습니다. 새 대화를 시작해 주세요.")
            reply_to = payload.get("reply_to")
            if reply_to is not None:
                pending = conversation.state.get("dialogue", {}).get("pending") or {}
                if not isinstance(reply_to, str) or reply_to != pending.get("message_id"):
                    raise ApiError("확인 질문이 변경되었습니다. 현재 질문에 답해 주세요.", 409)
            doc_id = payload.get("document_id")
            if doc_id and not any(d["document_id"] == doc_id for d in conversation.state["documents"]):
                raise ApiError("현재 대화의 문서를 선택해 주세요.", 404)
            services.respond(conversation.state, question, doc_id)
            if request.user.is_authenticated and conversation.case_id is None:
                from cases.models import ContractCase
                from cases.services.titles import title_from_question
                conversation.case = ContractCase.objects.create(
                    user=request.user,
                    title=title_from_question(question),
                )
                conversation.save(update_fields=("case", "updated_at"))
                from cases.services.attachments import promote_pending_documents
                promote_pending_documents(conversation, conversation.case)
                request.session["lens_case_id"] = str(conversation.case_id)
                room_created = True
            if conversation.case_id:
                from cases.models import CaseFact
                from cases.services.conversation_guidance import schedule_conversation_guidance
                from cases.services.facts import record_text_facts
                user_message = conversation.state["messages"][-2]
                record_text_facts(
                    conversation.case, question, source_type=CaseFact.SourceType.CHAT,
                    source_ref=user_message["id"], source_label="사용자 채팅",
                )
                schedule_conversation_guidance(
                    conversation.case,
                    messages=conversation.state["messages"],
                    conversation=conversation,
                )
                services.sync_persistent_messages(conversation)
                conversation.case.save(update_fields=("updated_at",))
    response = services.public_state(conversation)
    response["room_created"] = room_created
    return JsonResponse(response)


@api
@require_POST
@sensitive_post_parameters()
def upload_document(request):
    files = request.FILES.getlist("file")
    if getattr(request, "upload_too_large", False):
        raise ApiError("파일 크기는 20MB 이하여야 합니다.", 413)
    if len(files) != 1:
        raise ApiError("파일을 하나 선택해 주세요.")
    upload = files[0]
    if upload.size > 20 * 1024 * 1024:
        raise ApiError("파일 크기는 20MB 이하여야 합니다.", 413)
    filename = services.safe_text(Path(upload.name.replace("\\", "/")).name)[:180]
    if Path(filename).suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise ApiError("PDF, PNG, JPG 파일만 첨부할 수 있습니다.")
    with exclusive_conversation(request, request.POST) as (conversation, duplicate):
        notice = "이미 처리된 요청입니다."
        if not duplicate:
            if len(conversation.state["documents"]) >= settings.CHAT_MAX_DOCUMENTS:
                raise ApiError("문서는 최대 5개입니다. 기존 문서를 삭제한 뒤 첨부해 주세요.")
            try:
                data = upload.read()
                notice = services.add_document(
                    conversation.state, filename, data, str(conversation.pk),
                    case=conversation.case, conversation=conversation,
                    content_type=upload.content_type or "",
                )
            except ValueError as error:
                # Only adapter-owned fixed messages are returned; parser exceptions stay private.
                if type(error) is ValueError:
                    raise ApiError("문서 종류나 문구를 확인하지 못했습니다. 제목과 주요 항목이 선명한 계약서·등기를 첨부해 주세요.") from None
                raise
    return JsonResponse({**services.public_state(conversation), "notice": notice})


@api
@require_POST
def delete_document(request, document_id):
    with exclusive_conversation(request, json_body(request)) as (conversation, duplicate):
        if not duplicate:
            documents = conversation.state["documents"]
            if not any(d["document_id"] == document_id for d in documents):
                raise ApiError("문서를 찾을 수 없습니다.", 404)
            conversation.state["documents"] = [d for d in documents if d["document_id"] != document_id]
            invalidate_document(conversation.state, document_id)
            if conversation.case_id:
                from cases.services.attachments import delete_document_attachment
                from cases.services.conversation_guidance import clear_generated_guidance, schedule_conversation_guidance
                delete_document_attachment(conversation.case, document_id)
                messages = conversation.state["messages"]
                provenance_complete = conversation.state.get("document_provenance_version") == 1
                retained = ([
                    message for message in messages
                    if document_id not in message.get("document_ids", [])
                ] if provenance_complete else [])
                removed_message_ids = [
                    message.get("id") for message in messages
                    if message.get("id") and message not in retained
                ]
                conversation.state["document_provenance_version"] = 1
                conversation.state["messages"] = retained
                Message.objects.filter(conversation=conversation).exclude(
                    public_id__in=[m.get("id") for m in retained if m.get("id")]
                ).delete()
                from cases.services.facts import invalidate_source
                for message_id in removed_message_ids:
                    invalidate_source(conversation.case, "chat", message_id)
                clear_generated_guidance(conversation.case)
                schedule_conversation_guidance(
                    conversation.case,
                    messages=retained,
                    conversation=conversation,
                )
            else:
                # Guest sessions cannot preserve provenance separately.
                conversation.state["messages"] = []
                pending = conversation.pending_documents.filter(document_id=document_id).first()
                if pending:
                    pending.delete()
    return JsonResponse(services.public_state(conversation))


@api
@require_POST
def simplify_message(request):
    payload = json_body(request)
    message_id = payload.get("message_id")
    if not isinstance(message_id, str) or not message_id.strip():
        raise ApiError("다시 설명할 답변을 찾지 못했습니다.")
    with exclusive_conversation(request, payload) as (conversation, duplicate):
        if not duplicate:
            if services.simplify_message(conversation.state, message_id.strip()) is None:
                raise ApiError("현재 대화의 답변을 선택해 주세요.", 404)
            services.sync_persistent_messages(conversation)
    return JsonResponse(services.public_state(conversation))


@api
@require_POST
def reset(request):
    with exclusive_conversation(request, json_body(request)) as (conversation, duplicate):
        if not duplicate:
            if conversation.case_id:
                conversation.state["messages"] = []
                conversation.state["document_provenance_version"] = 1
                conversation.state["dialogue"] = services.empty_dialogue()
                Message.objects.filter(conversation=conversation).delete()
            else:
                conversation.state = services.initial_state()
    return JsonResponse(services.public_state(conversation))
