import json
import logging
import uuid
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.core.exceptions import RequestDataTooBig, TooManyFilesSent
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from src.document_check.extraction import DocumentValidationError, OcrUnavailableError
from src.generation.simplify import SimplificationError
from .models import Conversation
from .leases import heartbeat
from . import services

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
    conversation = Conversation.objects.filter(pk=chat_id, expires_at__gt=timezone.now()).first()
    if conversation is None:
        raise ApiError("대화 세션이 만료되었습니다. 페이지를 새로고침해 주세요.", 410)
    return conversation


def new_conversation(request):
    conversation = Conversation.objects.create(
        state=services.initial_state(),
        expires_at=timezone.now() + timedelta(seconds=settings.CHAT_TTL_SECONDS),
    )
    request.session["lens_conversation_id"] = str(conversation.pk)
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)
    return conversation


@require_GET
@never_cache
@ensure_csrf_cookie
def home(request):
    # Lazy model loading: migrations and the first HTML response never load KURE.
    Conversation.objects.filter(expires_at__lte=timezone.now()).delete()
    try:
        current_conversation(request)
    except ApiError:
        new_conversation(request)
    return render(request, "chat/index.html")


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
    try:
        with heartbeat(conversation.pk, token):
            yield conversation, duplicate
        if not duplicate:
            conversation.state["completed_requests"] = (conversation.state.get("completed_requests", []) + [request_id])[-32:]
        updated = Conversation.objects.filter(pk=conversation.pk, lease_token=token, busy_until__gt=timezone.now()).update(
            state=conversation.state, expires_at=timezone.now() + timedelta(seconds=settings.CHAT_TTL_SECONDS),
        )
        if not updated:
            raise ApiError("요청 처리 시간이 초과되었습니다. 대화를 새로고침해 주세요.", 409)
        request.session.set_expiry(settings.SESSION_COOKIE_AGE)
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
    with exclusive_conversation(request, payload) as (conversation, duplicate):
        if not duplicate:
            if len(conversation.state["messages"]) >= settings.CHAT_MAX_MESSAGES:
                raise ApiError("대화가 길어졌습니다. 새 대화를 시작해 주세요.")
            doc_id = payload.get("document_id")
            if doc_id and not any(d["document_id"] == doc_id for d in conversation.state["documents"]):
                raise ApiError("현재 대화의 문서를 선택해 주세요.", 404)
            services.respond(conversation.state, question, doc_id)
    return JsonResponse(services.public_state(conversation))


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
                notice = services.add_document(conversation.state, filename, upload.read(), str(conversation.pk))
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
            # Drop history too: deleted document facts must not survive in follow-up prompts.
            conversation.state["messages"] = []
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
    return JsonResponse(services.public_state(conversation))


@api
@require_POST
def reset(request):
    with exclusive_conversation(request, json_body(request)) as (conversation, duplicate):
        if not duplicate:
            conversation.state = services.initial_state()
    return JsonResponse(services.public_state(conversation))
