"""Django-to-RAG adapter. No Streamlit imports or changes to legal validation."""
from dataclasses import asdict
from functools import lru_cache
from hashlib import sha256
import time
import uuid

from src.document_check.privacy import mask_sensitive_text
from src.security.secret_filter import redact_secrets
from src.document_check.upload_analysis import analyze_uploaded_document
from src.document_check.session_retrieval import (
    SessionDocumentChunk, SessionDocumentContext, SessionDocumentRetriever,
    build_session_document_context, normalize_document_review_question,
    question_references_uploaded_document, referenced_document_kind,
)
from src.generation import graph
from src.generation.chain import get_default_service
from src.generation.conversation import resolve_question
from src.retrieval.readiness import BackgroundServiceLoader

LABELS = {"registry": "등기사항증명서", "contract": "임대차계약서", "unknown": "종류 확인 필요"}


def safe_text(value):
    return mask_sensitive_text(redact_secrets(value).text)


@lru_cache(maxsize=1)
def retrieval_loader():
    return BackgroundServiceLoader(get_default_service)


def initial_state():
    return {"messages": [], "documents": [], "completed_requests": []}


def public_state(conversation):
    state = conversation.state
    return {
        "conversation_id": str(conversation.id),
        "messages": [{k: v for k, v in m.items() if k != "context_content"} for m in state.get("messages", [])],
        "documents": [{k: v for k, v in d.items() if k not in {"context", "checksum"}} for d in state.get("documents", [])],
    }


def add_document(state, filename, data, session_id):
    checksum = sha256(data).hexdigest()
    if any(d["checksum"] == checksum for d in state["documents"]):
        return "이미 추가된 문서입니다."
    classified = analyze_uploaded_document(filename, data)
    classification = classified.classification
    if classification.kind == "unknown" or classified.analysis is None:
        raise ValueError("문서 종류를 확인하지 못했습니다. 계약서·등기의 제목과 주요 항목이 선명한 파일을 첨부해 주세요.")
    document_id = uuid.uuid4().hex
    context = build_session_document_context(
        filename, classified.extraction, session_id,
        document_id=document_id, document_kind=LABELS[classification.kind],
    )
    if context.is_empty:
        raise ValueError("읽을 수 있는 문구가 없습니다. 더 선명한 파일을 첨부해 주세요.")
    state["documents"].append({
        "document_id": document_id, "filename": filename,
        "kind": classification.kind, "label": LABELS[classification.kind],
        "confidence": classification.confidence, "page_count": classified.extraction.page_count,
        "checksum": checksum, "context": asdict(context),
        "analysis": classified.analysis.to_public_dict(),
    })
    return "문서 분석을 완료했습니다."


def find_evidences(question, documents, selected_id=None):
    kinds = tuple(dict.fromkeys(d["kind"] for d in documents))
    selected_kind = referenced_document_kind(question, kinds)
    if selected_id:
        selected = [d for d in documents if d["document_id"] == selected_id]
    elif selected_kind:
        selected = [d for d in documents if d["kind"] == selected_kind]
    else:
        selected = documents[-1:]
    found = []
    for document in selected:
        payload = document["context"]
        context = SessionDocumentContext(
            **{k: v for k, v in payload.items() if k != "chunks"},
            chunks=tuple(SessionDocumentChunk(**c) for c in payload["chunks"]),
        )
        retriever = SessionDocumentRetriever(context)
        query = question
        if any(cue in question.replace(" ", "") for cue in ("주의", "위험", "분석", "확인할점", "검토", "요약")):
            query += " 갑구 을구 소유권 근저당권 압류 임차권 경매" if document["kind"] == "registry" else " 임대인 임차인 보증금 차임 기간 특약"
        matches = retriever.search(query, k=3)
        found.extend(matches or retriever.first_pages(k=2))
    return tuple(sorted(found, key=lambda e: (-e.score, e.chunk_id))[:4])


def respond(state, question, document_id=None):
    started = time.perf_counter()
    documents = state["documents"]
    kinds = tuple(dict.fromkeys(d["kind"] for d in documents))
    use_document = bool(documents) and (
        bool(document_id) or question_references_uploaded_document(question, kinds)
    )
    if use_document:
        selected = next((d for d in documents if d["document_id"] == document_id), None)
        kind = selected["kind"] if selected else referenced_document_kind(question, kinds) or documents[-1]["kind"]
        query = normalize_document_review_question(question, kind)
        evidences = find_evidences(query, documents, document_id)
        answer = graph.answer_document_question(query, evidences, service=retrieval_loader().result())
        used_history = False
    else:
        resolved = resolve_question(question, state["messages"])
        answer = graph.answer_question(resolved.standalone, service=retrieval_loader().result())
        used_history = resolved.used_history
    message = {
        "id": uuid.uuid4().hex, "role": "assistant", "status": answer.status,
        # raw_text is never a fallback for an empty or rejected answer.
        "content": safe_text((answer.text or "").strip()) or "답변 본문을 표시하지 못했습니다. 다시 질문해 주세요.",
        "sources": answer.document_sources() + answer.sources(),
        "context_content": safe_text(answer.raw_text) if answer.status == "answered" else "",
        "used_history": used_history, "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    state["messages"].extend([
        {"id": uuid.uuid4().hex, "role": "user", "content": question}, message,
    ])
    return message
