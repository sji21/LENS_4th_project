"""업로드 OCR 문서를 현재 세션 안에서만 검색하는 경계.

공식 법령·판례·기관 안내는 ``RetrievalService``가 계속 담당한다. 이 모듈은
OCR 결과를 SQLite·Chroma·파일에 저장하지 않고, 호출자가 보유한 세션 객체 안에서만
BM25 검색할 수 있게 한다.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from hashlib import sha256

from src.retrieval.retriever import BM25Retriever

from .extraction_models import ExtractionResult, PageExtraction


def _no_query_expansion(_: str) -> list[str]:
    """공식 법령 용어 확장을 OCR 원문 검색에 적용하지 않는다."""

    return []


_OCR_INTERNAL_SPACE_RE = re.compile(r"(?<=[가-힣0-9])\s+(?=[가-힣0-9])")


def _searchable_ocr_text(text: str) -> str:
    """원문을 보존하면서 OCR의 글자 사이 공백을 제거한 검색 사본을 덧붙인다."""

    compact = _OCR_INTERNAL_SPACE_RE.sub("", text)
    return text if compact == text else f"{text}\n{compact}"


_UPLOADED_DOCUMENT_CUES = (
    "업로드한",
    "업로드했던",
    "첨부한",
    "첨부했던",
    "올린문서",
    "올렸던",
    "이문서",
    "해당문서",
    "문서에서",
    "이계약서",
    "해당계약서",
    "계약서에서",
    "이등기부",
    "해당등기부",
    "등기부등본에서",
    "등기사항증명서에서",
    "특약사항",
    "발급일",
    "채권최고액",
    "갑구",
    "을구",
)


_REGISTRY_CUES = (
    "등기부등본",
    "등기부",
    "등기사항증명서",
    "갑구",
    "을구",
    "근저당권",
)

_CONTRACT_CUES = (
    "임대차계약서",
    "계약서",
    "특약사항",
)

_GENERIC_REGISTRY_COPY_CUES = (
    "등본",
    "이등본",
    "해당등본",
    "첨부한등본",
    "올린등본",
)

_NON_REGISTRY_COPY_CUES = (
    "주민등록등본",
    "가족관계등록부",
    "가족관계증명서",
    "법인등기부등본",
)

_REGISTRY_REVIEW_CUES = (
    "검토",
    "주의",
    "주의깊게",
    "주의할점",
    "위험요소",
    "확인할점",
    "봐야할부분",
)

_REGISTRY_REVIEW_QUESTION = "이 등본에서 주의깊게 봐야 할 부분 알려줘"


def referenced_document_kind(
    question: str,
    available_document_kinds: tuple[str, ...] = (),
) -> str | None:
    """질문의 문서 별칭을 세션에 실제 존재하는 종류에 한해 해석한다."""

    compact = "".join((question or "").split())
    available = set(available_document_kinds)

    if any(cue in compact for cue in _REGISTRY_CUES):
        return "registry" if not available or "registry" in available else None
    if any(cue in compact for cue in _CONTRACT_CUES):
        return "contract" if not available or "contract" in available else None
    if any(cue in compact for cue in _NON_REGISTRY_COPY_CUES):
        return None
    if "registry" in available and any(
        cue in compact for cue in _GENERIC_REGISTRY_COPY_CUES
    ):
        return "registry"
    return None


def question_references_uploaded_document(
    question: str,
    available_document_kinds: tuple[str, ...] = (),
) -> bool:
    """사용자가 현재 세션의 업로드 문서를 명시적으로 가리키는지 판별한다."""

    compact = "".join((question or "").split())
    if any(cue in compact for cue in _NON_REGISTRY_COPY_CUES) and not any(
        cue in compact for cue in _REGISTRY_CUES
    ):
        return False
    referenced_kind = referenced_document_kind(question, available_document_kinds)
    if referenced_kind is not None:
        return True
    if "등본" in compact:
        # 종류가 확인되지 않은 단독 별칭을 최근 계약서에 연결하지 않는다.
        return False
    if available_document_kinds and (
        any(cue in compact for cue in _REGISTRY_CUES)
        or any(cue in compact for cue in _CONTRACT_CUES)
    ):
        return False
    return any(cue in compact for cue in _UPLOADED_DOCUMENT_CUES)


def normalize_document_review_question(question: str, document_kind: str | None) -> str:
    """짧은 등기 검토 표현을 검증된 문서 점검 질문으로 정규화한다.

    화면에 표시할 사용자 원문은 변경하지 않고 OCR 검색과 생성에 전달하는 질문만
    안정화한다. 발급 방법·특정 값 조회처럼 검토 요청이 아닌 질문은 유지한다.
    """

    compact = "".join((question or "").split())
    if document_kind == "registry" and any(
        cue in compact for cue in _REGISTRY_REVIEW_CUES
    ):
        return _REGISTRY_REVIEW_QUESTION
    return question


@dataclass(frozen=True)
class SessionDocumentChunk:
    """한 세션에만 존재하는 OCR 페이지 검색 단위."""

    chunk_id: str
    filename: str
    page_number: int
    extraction_method: str
    session_id: str
    checksum: str
    text: str = field(repr=False)
    document_id: str = ""
    document_kind: str = ""

    def as_retriever_chunk(self) -> dict:
        """기존 BM25Retriever가 읽는 최소 청크 규격으로 변환한다."""

        return {
            "chunk_id": self.chunk_id,
            # OCR 원문은 Evidence에 그대로 남기고 BM25 입력만 보강한다.
            "text": _searchable_ocr_text(self.text),
            "metadata": {
                "filename": self.filename,
                "page_number": self.page_number,
                "extraction_method": self.extraction_method,
                "session_id": self.session_id,
                "document_id": self.document_id,
                "document_kind": self.document_kind,
                "checksum": self.checksum,
            },
        }


@dataclass(frozen=True)
class SessionDocumentContext:
    """브라우저 세션 하나가 보유하는 업로드 문서 OCR 청크 묶음."""

    session_id: str
    filename: str
    chunks: tuple[SessionDocumentChunk, ...]
    document_id: str = ""
    document_kind: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.chunks


@dataclass(frozen=True)
class SessionDocumentEvidence:
    """OCR 문서에서 검색된 사실과 페이지 위치.

    공식 ``Evidence``와 타입을 분리해, OCR 원문을 법령·판례·기관 안내 근거로
    오인하지 않도록 한다.
    """

    chunk_id: str
    filename: str
    page_number: int
    extraction_method: str
    checksum: str
    text: str = field(repr=False)
    document_id: str = ""
    document_kind: str = ""
    score: float = 0.0


def _validate_identity(filename: str, session_id: str) -> tuple[str, str]:
    normalized_filename = (filename or "").strip()
    normalized_session_id = (session_id or "").strip()
    if not normalized_filename:
        raise ValueError("filename은 비어 있을 수 없습니다.")
    if not normalized_session_id:
        raise ValueError("session_id는 비어 있을 수 없습니다.")
    return normalized_filename, normalized_session_id


def _chunk_from_page(
    page: PageExtraction,
    *,
    filename: str,
    session_id: str,
    chunk_index: int,
    document_id: str = "",
    document_kind: str = "",
) -> SessionDocumentChunk | None:
    text = (page.text or "").strip()
    if page.method == "unreadable" or not text:
        return None

    checksum = sha256(text.encode("utf-8")).hexdigest()
    chunk_id = f"session:{session_id}:page:{page.page_number}:{chunk_index}"
    if document_id:
        chunk_id = (
            f"session:{session_id}:document:{document_id}:"
            f"page:{page.page_number}:{chunk_index}"
        )
    return SessionDocumentChunk(
        chunk_id=chunk_id,
        filename=filename,
        page_number=page.page_number,
        extraction_method=page.method,
        session_id=session_id,
        document_id=document_id,
        document_kind=document_kind,
        checksum=checksum,
        text=text,
    )


def build_session_document_context(
    filename: str,
    extraction: ExtractionResult,
    session_id: str,
    *,
    document_id: str = "",
    document_kind: str = "",
) -> SessionDocumentContext:
    """OCR 추출 결과를 영속화하지 않고 페이지별 세션 청크로 만든다.

    MVP는 페이지 하나를 청크 하나로 사용한다. 긴 페이지의 문단 분할은 실제 검색
    품질 측정이 필요할 때 별도 변경으로 추가한다.
    """

    filename, session_id = _validate_identity(filename, session_id)
    chunks = tuple(
        chunk
        for page in extraction.pages
        if (
            chunk := _chunk_from_page(
                page,
                filename=filename,
                session_id=session_id,
                chunk_index=0,
                document_id=document_id,
                document_kind=document_kind,
            )
        )
        is not None
    )
    return SessionDocumentContext(
        session_id=session_id,
        filename=filename,
        chunks=chunks,
        document_id=document_id,
        document_kind=document_kind,
    )


class SessionDocumentRetriever:
    """한 ``SessionDocumentContext`` 안에서만 OCR 문구를 BM25로 검색한다."""

    def __init__(self, context: SessionDocumentContext) -> None:
        self.context = context
        self._chunks = {chunk.chunk_id: chunk for chunk in context.chunks}
        self._retriever = (
            BM25Retriever(
                [chunk.as_retriever_chunk() for chunk in context.chunks],
                query_expander=_no_query_expansion,
            )
            if context.chunks
            else None
        )

    def search(self, question: str, k: int = 3) -> list[SessionDocumentEvidence]:
        """질문과 맞는 OCR 페이지를 점수 내림차순으로 반환한다."""

        if self._retriever is None or not question or not question.strip() or k <= 0:
            return []

        return [
            SessionDocumentEvidence(
                chunk_id=chunk.chunk_id,
                filename=chunk.filename,
                page_number=chunk.page_number,
                extraction_method=chunk.extraction_method,
                checksum=chunk.checksum,
                document_id=chunk.document_id,
                document_kind=chunk.document_kind,
                text=chunk.text,
                score=round(score, 4),
            )
            for chunk_id, score in self._retriever.search(question, k)
            if (chunk := self._chunks.get(chunk_id)) is not None
        ]

    def first_pages(self, k: int = 3) -> list[SessionDocumentEvidence]:
        """요약·위험 점검처럼 검색어가 원문에 없을 때 읽을 수 있는 앞쪽 페이지를 반환한다."""

        if k <= 0:
            return []
        return [
            SessionDocumentEvidence(
                chunk_id=chunk.chunk_id,
                filename=chunk.filename,
                page_number=chunk.page_number,
                extraction_method=chunk.extraction_method,
                checksum=chunk.checksum,
                document_id=chunk.document_id,
                document_kind=chunk.document_kind,
                text=chunk.text,
                score=0.0,
            )
            for chunk in sorted(
                self.context.chunks,
                key=lambda item: (item.page_number, item.chunk_id),
            )[:k]
        ]
