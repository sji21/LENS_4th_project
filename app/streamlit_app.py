"""LENS 챗봇 UI.

공식 법령·판례·기관 안내 RAG와 현재 브라우저 세션의 업로드 문서 OCR을
서로 다른 근거 저장소로 유지하면서 하나의 대화 화면에서 연결한다.
"""

from __future__ import annotations

import logging
import sys
import time
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.document_check.session_retrieval import (  # noqa: E402
    SessionDocumentRetriever,
    build_session_document_context,
    normalize_document_review_question,
    question_references_uploaded_document,
    referenced_document_kind,
)
from src.document_check.upload_analysis import analyze_uploaded_document  # noqa: E402
from src.generation.chain import get_default_service  # noqa: E402
from src.generation.graph import answer_document_question, answer_question  # noqa: E402
from src.generation.conversation import resolve_question  # noqa: E402
from src.generation.models import Answer  # noqa: E402
from src.retrieval.readiness import BackgroundServiceLoader  # noqa: E402


logger = logging.getLogger(__name__)


STATUS_LABELS = {
    "answered": ("근거를 확인해 답변드렸습니다.", "✅"),
    "abstained": ("답변을 바로 제공하기 어렵습니다.", "⚠️"),
    "refused": ("이 질문은 LENS의 답변 범위에 포함되지 않습니다.", "🚫"),
}

STATUS_CLASSES = {
    "answered": "status-answered",
    "abstained": "status-abstained",
    "refused": "status-refused",
}

WELCOME_MESSAGE = (
    "안녕하세요. 부동산 계약 과정에서 궁금한 권리나 절차를 질문해 주세요.\n\n"
    "확인 가능한 근거와 함께 안내해 드릴게요."
)


@st.cache_resource(show_spinner=False)
def load_retrieval_service_loader():
    """KURE-v1을 한 번만 백그라운드에서 준비해 모든 rerun에서 재사용한다."""

    return BackgroundServiceLoader(get_default_service).start()


@st.fragment(run_every="1s")
def render_retrieval_readiness(loader: BackgroundServiceLoader) -> None:
    """검색 모델이 준비되는 동안에만 상태를 주기적으로 갱신한다."""

    snapshot = loader.snapshot()
    if snapshot.state in {"idle", "loading"}:
        with st.status(
            "검색 모델을 준비하고 있습니다.",
            state="running",
            expanded=False,
        ):
            st.caption(
                "채팅 화면은 먼저 사용할 수 있습니다. "
                f"첫 준비 작업 경과 {snapshot.elapsed_seconds:.0f}초"
            )
        return

    # 준비 완료 후에도 run_every fragment를 계속 두면 긴 채팅 화면의
    # 스크롤을 방해할 수 있다. 전체 앱을 한 번만 다시 실행해 polling을 끝낸다.
    st.rerun()


def render_retrieval_status(loader: BackgroundServiceLoader) -> None:
    """로딩 중에만 polling fragment를 사용하고 종료 상태는 정적으로 표시한다."""

    snapshot = loader.snapshot()
    if snapshot.state in {"idle", "loading"}:
        render_retrieval_readiness(loader)
        return

    if snapshot.state == "ready":
        st.caption(":material/check_circle: 검색 모델 준비 완료")
        return

    st.error(
        "검색 모델을 준비하지 못했습니다. 검색 인덱스와 로컬 환경을 확인해 주세요."
    )
    if st.button(
        "검색 모델 다시 준비",
        icon=":material/refresh:",
        key="retry_retrieval_service",
    ):
        loader.retry()
        st.rerun()


def configure_page() -> None:
    st.set_page_config(
        page_title="LENS | 주택임대차 근거 기반 챗봇",
        page_icon="🏠",
        layout="centered",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        :root {
            --jeonse-navy: #153556;
            --jeonse-blue: #2176B8;
            --jeonse-sky: #EAF5FD;
            --jeonse-ink: #172B3A;
            --jeonse-muted: #647587;
            --jeonse-line: #DCE5EC;
            --jeonse-surface: #FFFFFF;
        }

        .stApp {
            background:
                radial-gradient(circle at 92% 4%, rgba(80, 164, 224, .10), transparent 23rem),
                #F5F8FB;
        }

        [data-testid="stHeader"] {
            background: transparent;
            pointer-events: none;
        }

        [data-testid="stToolbar"] {
            pointer-events: none;
        }

        [data-testid="stToolbar"] button,
        [data-testid="stToolbar"] [role="button"] {
            pointer-events: auto;
        }

        #MainMenu,
        footer {
            visibility: hidden;
        }

        .block-container {
            max-width: 920px;
            padding-top: 2rem;
            padding-bottom: 7.5rem;
        }

        /* Sidebar */
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #112E4C 0%, #173E65 100%);
            border-right: 0;
        }

        [data-testid="stSidebarContent"] {
            padding-top: 1.1rem;
        }

        [data-testid="stSidebar"] * {
            color: #F7FAFC;
        }

        .sidebar-brand {
            display: flex;
            align-items: center;
            gap: .75rem;
            padding: .25rem 0 1rem;
        }

        .sidebar-logo {
            display: grid;
            width: 44px;
            height: 44px;
            place-items: center;
            border-radius: 14px;
            background: linear-gradient(145deg, #4AA7E6, #2080C4);
            box-shadow: 0 8px 22px rgba(1, 18, 36, .28);
            font-size: .82rem;
            font-weight: 900;
            letter-spacing: -.02em;
        }

        .sidebar-brand-name {
            font-size: 1.25rem;
            font-weight: 850;
            letter-spacing: -.03em;
            line-height: 1.2;
        }

        .sidebar-brand-copy {
            color: #BFD2E4 !important;
            font-size: .8rem;
            margin-top: .15rem;
        }

        .sidebar-status {
            display: flex;
            align-items: center;
            gap: .5rem;
            color: #DCEAF5 !important;
            font-size: .8rem;
            border-top: 1px solid rgba(255,255,255,.10);
            border-bottom: 1px solid rgba(255,255,255,.10);
            padding: .75rem 0;
            margin-bottom: 1.15rem;
        }

        .sidebar-status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #66D7A6;
            box-shadow: 0 0 0 4px rgba(102, 215, 166, .12);
            flex: 0 0 auto;
        }

        .sidebar-section {
            background: rgba(255,255,255,.07);
            border: 1px solid rgba(255,255,255,.11);
            border-radius: 16px;
            padding: 1rem;
            margin: 0 0 .8rem;
        }

        .sidebar-section-title {
            font-size: .85rem;
            font-weight: 800;
            margin-bottom: .7rem;
        }

        .sidebar-tags {
            display: flex;
            flex-wrap: wrap;
            gap: .4rem;
        }

        .sidebar-tags span {
            display: inline-block;
            padding: .28rem .55rem;
            border-radius: 999px;
            background: rgba(255,255,255,.09);
            border: 1px solid rgba(255,255,255,.10);
            color: #DCEAF5 !important;
            font-size: .76rem;
            line-height: 1.2;
        }

        .sidebar-copy {
            color: #C6D7E5 !important;
            font-size: .8rem;
            line-height: 1.58;
        }

        [data-testid="stSidebar"] .stButton > button {
            background: rgba(255,255,255,.96) !important;
            color: #17365D !important;
            border: 0 !important;
            border-radius: 12px !important;
            min-height: 44px;
            font-weight: 800 !important;
            box-shadow: 0 5px 16px rgba(3, 22, 42, .16);
        }

        [data-testid="stSidebar"] .stButton > button * {
            color: #17365D !important;
        }

        [data-testid="stSidebar"] .stButton > button:hover {
            background: #EAF5FD !important;
            transform: translateY(-1px);
        }

        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
            margin-top: .85rem;
        }

        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
            color: #AFC5D7 !important;
            font-size: .74rem;
            line-height: 1.5;
        }

        /* Header */
        .st-key-retrieval_readiness_area {
            margin-bottom: .45rem;
        }

        .st-key-retrieval_readiness_area [data-testid="stStatusWidget"] {
            border-radius: 12px;
        }

        .st-key-intro_card {
            position: relative;
            overflow: hidden;
            padding: 1.15rem 1.35rem 1.2rem;
            border: 1px solid #D7E7F3;
            border-radius: 22px;
            background:
                linear-gradient(120deg, rgba(255,255,255,.98) 0%, rgba(238,248,255,.98) 100%);
            box-shadow: 0 14px 34px rgba(31, 76, 110, .08);
            margin-bottom: .75rem;
        }

        .st-key-intro_card::after {
            content: "";
            position: absolute;
            width: 170px;
            height: 170px;
            border-radius: 50%;
            right: -65px;
            top: -105px;
            background: rgba(45, 139, 202, .10);
            pointer-events: none;
        }

        .st-key-intro_card_header {
            position: relative;
            z-index: 2;
        }

        .st-key-intro_card:has(.hero-copy) .st-key-intro_card_header {
            margin-bottom: 1.1rem;
        }

        .hero-title {
            color: var(--jeonse-ink);
            font-size: 1.55rem;
            line-height: 1.3;
            letter-spacing: -.035em;
            font-weight: 850;
            white-space: nowrap;
            word-break: keep-all;
        }

        .hero-title span {
            color: var(--jeonse-blue);
        }

        .st-key-intro_toggle button {
            min-height: 36px;
            padding: .35rem .55rem;
            color: #466274;
            font-weight: 700;
        }

        .hero-copy {
            position: relative;
            z-index: 1;
            color: #586C7E;
            margin: 0;
            line-height: 1.65;
            font-size: .88rem;
            max-width: 46rem;
        }

        .hero-copy-line {
            display: block;
        }

        .hero-copy-line + .hero-copy-line {
            margin-top: .22rem;
        }

        .hero-meta {
            position: relative;
            z-index: 1;
            display: flex;
            flex-wrap: wrap;
            gap: .45rem;
            margin-top: .9rem;
        }

        .hero-meta span {
            border-radius: 8px;
            background: rgba(255,255,255,.82);
            border: 1px solid #DCE9F2;
            color: #466274;
            padding: .3rem .55rem;
            font-size: .75rem;
            font-weight: 650;
        }

        /* Chat */
        [data-testid="stChatMessage"] {
            background: rgba(255,255,255,.92);
            border: 1px solid var(--jeonse-line);
            border-radius: 18px;
            padding: 1rem 1.05rem;
            margin-bottom: .7rem;
            box-shadow: 0 5px 18px rgba(34, 67, 90, .045);
        }

        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
            background: #DDEFFD;
            border-color: #9FCDEB;
            color: #123D5A;
            margin-left: 3.5rem;
            box-shadow: 0 7px 20px rgba(33, 118, 184, .08);
        }

        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
            background: #FFFFFF;
            border-color: #D8E2EA;
            margin-right: 3.5rem;
        }

        [data-testid="stChatMessage"] p {
            line-height: 1.7;
        }

        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) p,
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) li,
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) strong {
            color: #172B3A !important;
        }

        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) p,
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) li,
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) strong {
            color: #123D5A !important;
        }

        [data-testid="stChatMessageAvatarAssistant"] {
            background: #DDEFFA;
        }

        [data-testid="stChatMessageAvatarUser"] {
            background: #C7E3F7;
        }

        .st-key-chat_area {
            min-height: calc(100vh - 370px);
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            padding: .5rem 0 1rem;
        }

        [data-testid="stChatInput"] {
            border: 1px solid #BFD2E1;
            border-radius: 16px;
            background: #FFFFFF;
            box-shadow: 0 8px 28px rgba(21, 53, 86, .12);
        }

        [data-testid="stChatInput"]:focus-within {
            border-color: #4B9CD3;
            box-shadow: 0 0 0 3px rgba(33, 118, 184, .11),
                        0 8px 28px rgba(21, 53, 86, .12);
        }

        .answer-meta-row {
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: .45rem;
            margin: .4rem 0 .15rem;
        }

        .status-pill {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: .28rem .62rem;
            margin: 0;
            font-size: .75rem;
            font-weight: 700;
        }

        .elapsed-time {
            display: inline-flex;
            align-items: center;
            color: #647587;
            font-size: .75rem;
            font-weight: 650;
        }

        .status-answered {
            border: 1px solid #BFE6D3;
            background: #EFFAF5;
            color: #24754F;
        }

        .status-abstained {
            border: 1px solid #F0D89B;
            background: #FFF9E8;
            color: #8A6512;
        }

        .status-refused {
            border: 1px solid #E8C5C5;
            background: #FFF3F3;
            color: #9A4444;
        }

        [data-testid="stExpander"] {
            border: 1px solid #DCE5EC;
            border-radius: 13px;
            background: #FAFCFE;
            margin-top: .6rem;
        }

        @media (max-width: 640px) {
            .block-container {
                padding: 1rem .85rem 7rem;
            }

            .st-key-intro_card {
                padding: .9rem 1rem 1rem;
                border-radius: 18px;
            }

            .hero-title {
                font-size: 1.15rem;
                white-space: normal;
            }

            .hero-copy br {
                display: none;
            }

            .hero-meta {
                gap: .35rem;
            }

            [data-testid="stChatMessage"] {
                padding: .85rem .8rem;
            }

            [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
                margin-left: .75rem;
            }

            [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
                margin-right: .75rem;
            }

            .st-key-chat_area {
                min-height: calc(100vh - 330px);
            }

        }

        @media (max-width: 900px) {
            [data-testid="stAppDeployButton"] {
                display: none;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def init_chat_state() -> None:
    st.session_state.setdefault("intro_expanded", True)

    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = [
            {
                "role": "assistant",
                "content": WELCOME_MESSAGE,
                "status": None,
                "sources": [],
                "context_content": "",
            }
        ]

    if "session_document_id" not in st.session_state:
        st.session_state["session_document_id"] = uuid4().hex
    if "session_documents" not in st.session_state:
        st.session_state["session_documents"] = {}
    if "upload_jobs" not in st.session_state:
        st.session_state["upload_jobs"] = {}
    if "active_upload_job_id" not in st.session_state:
        st.session_state["active_upload_job_id"] = None


def clear_chat() -> None:
    st.session_state["chat_messages"] = [
        {
            "role": "assistant",
            "content": WELCOME_MESSAGE,
            "status": None,
            "sources": [],
            "context_content": "",
        }
    ]
    st.session_state["upload_jobs"] = {}
    st.session_state["active_upload_job_id"] = None


_TERMINAL_UPLOAD_JOB_STATUSES = frozenset({"completed", "failed"})


def _finish_upload_job(job_id: str | None) -> None:
    """작업 종료 경로에서 활성 ID와 임시 작업을 한 번에 정리한다."""

    if not job_id:
        st.session_state["active_upload_job_id"] = None
        return
    st.session_state["upload_jobs"].pop(job_id, None)
    if st.session_state.get("active_upload_job_id") == job_id:
        st.session_state["active_upload_job_id"] = None


def reconcile_upload_job_state() -> bool:
    """입력창을 그리기 전에 고아·종료 업로드 작업을 제거한다."""

    job_id = st.session_state.get("active_upload_job_id")
    if not job_id:
        return False

    upload_job = st.session_state["upload_jobs"].get(job_id)
    if upload_job is None or upload_job.get("status") in _TERMINAL_UPLOAD_JOB_STATUSES:
        _finish_upload_job(job_id)
        return False
    return True


def _document_id(kind: str, data: bytes) -> str:
    """같은 파일을 반복 선택해도 중복 적재하지 않을 세션 내 식별자."""

    return sha256(f"{kind}:".encode("utf-8") + data).hexdigest()[:20]


def _document_label(kind: str) -> str:
    labels = {
        "registry": "등기사항증명서",
        "contract": "임대차계약서",
        "unknown": "종류 확인 필요",
    }
    return labels.get(kind, "첨부 문서")


def _add_uploaded_document(filename: str, data: bytes) -> tuple[str | None, dict]:
    """OCR을 한 번 수행한 뒤 본문 판별 결과와 같은 추출물을 분석에 재사용한다."""

    documents = st.session_state["session_documents"]
    content_checksum = sha256(data).hexdigest()
    duplicate = next(
        (
            document
            for document in documents.values()
            if document.get("content_checksum") == content_checksum
        ),
        None,
    )
    if duplicate is not None:
        return None, {
            "kind": duplicate["kind"],
            "label": duplicate["label"],
            "confidence": duplicate.get("classification_confidence", ""),
            "reason": "이미 현재 세션에 추가된 문서입니다.",
        }

    classified = analyze_uploaded_document(filename, data)
    classification = classified.classification
    details = {
        "kind": classification.kind,
        "label": _document_label(classification.kind),
        "confidence": classification.confidence,
        "reason": classification.reason,
    }
    if classification.kind == "unknown" or classified.analysis is None:
        return (
            "OCR 내용만으로 등기사항증명서인지 임대차계약서인지 구분하지 못했습니다. "
            "문서 종류를 확인하고 제목과 주요 항목이 선명하게 보이도록 다시 첨부해 주세요.",
            details,
        )

    kind = classification.kind
    analysis = classified.analysis
    document_id = _document_id(kind, data)

    context = build_session_document_context(
        filename,
        classified.extraction,
        st.session_state["session_document_id"],
        document_id=document_id,
        document_kind=_document_label(kind),
    )
    documents[document_id] = {
        "document_id": document_id,
        "kind": kind,
        "label": _document_label(kind),
        "filename": filename,
        "page_count": classified.extraction.page_count,
        "analysis": analysis,
        "context": context,
        "content_checksum": content_checksum,
        "classification_confidence": classification.confidence,
        "classification_reason": classification.reason,
    }
    return None, details


def _available_document_kinds() -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            document["kind"]
            for document in st.session_state["session_documents"].values()
        )
    )


def find_document_evidences(question: str):
    """현재 세션의 문서들에서 양의 BM25 점수를 가진 페이지만 모은다."""

    documents = st.session_state["session_documents"]
    if not documents:
        return ()

    normalized = question.replace(" ", "")
    selected_kind = referenced_document_kind(question, _available_document_kinds())
    document_items = list(documents.values())
    if selected_kind:
        selected_documents = [
            document for document in document_items
            if document["kind"] == selected_kind
        ]
    else:
        # "이 문서"처럼 종류를 말하지 않은 표현은 가장 최근 첨부를 가리킨다.
        selected_documents = document_items[-1:]

    broad_analysis = any(
        cue in normalized
        for cue in ("주의", "위험", "분석", "확인할점", "검토", "요약")
    )

    found = []
    for document in selected_documents:
        retriever = SessionDocumentRetriever(document["context"])
        search_question = question
        if broad_analysis and document["kind"] == "registry":
            search_question += " 갑구 을구 소유권 근저당권 가압류 압류 임차권 경매"
        elif broad_analysis and document["kind"] == "contract":
            search_question += " 임대인 임차인 보증금 차임 기간 특약"
        matches = retriever.search(search_question, k=3)
        found.extend(matches or retriever.first_pages(k=2))

    found.sort(key=lambda evidence: (-evidence.score, evidence.chunk_id))
    return tuple(found[:4])


def render_document_manager() -> None:
    """채팅으로 추가한 문서를 본문에서 확인한다."""

    documents = st.session_state["session_documents"]
    if not documents:
        return

    with st.expander(f"첨부 문서 {len(documents)}개", expanded=False):
        st.caption("첨부 문서는 현재 브라우저 세션에서만 검색에 사용됩니다.")
        for document in documents.values():
            st.caption(
                f"{document['filename']} · {document['label']} · "
                f"{document['page_count']}쪽 · 자동 판별 {document.get('classification_confidence', '-')}"
            )
            if document.get("classification_reason"):
                st.caption(f"판별 근거: {document['classification_reason']}")


def toggle_intro_card() -> None:
    st.session_state["intro_expanded"] = not st.session_state["intro_expanded"]


@st.fragment
def render_header() -> None:
    expanded = st.session_state["intro_expanded"]
    toggle_label = "접기" if expanded else "펼치기"
    toggle_icon = ":material/keyboard_arrow_up:" if expanded else ":material/keyboard_arrow_down:"

    # 소개 카드 높이에 맞춰 첫 화면의 채팅 영역을 조정한다. 입력창은 Streamlit이
    # 화면 하단에 고정하므로 카드와 첫 메시지가 같은 뷰포트 안에 남는다.
    desktop_offset = 500 if expanded else 340
    mobile_offset = 630 if expanded else 405
    st.markdown(
        f"""
        <style>
        .st-key-chat_area {{
            min-height: max(9rem, calc(100vh - {desktop_offset}px));
        }}
        @media (max-width: 640px) {{
            .st-key-chat_area {{
                min-height: max(8rem, calc(100vh - {mobile_offset}px));
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.container(key="intro_card", gap=None):
        with st.container(
            key="intro_card_header",
            horizontal=True,
            wrap=False,
            horizontal_alignment="distribute",
            vertical_alignment="center",
            gap="small",
        ):
            st.markdown(
                '<div class="hero-title">안전한 부동산 계약을 위한 '
                '<span>챗봇 서비스</span></div>',
                unsafe_allow_html=True,
            )
            st.button(
                toggle_label,
                key="intro_toggle",
                type="tertiary",
                icon=toggle_icon,
                icon_position="right",
                help=f"서비스 소개 {toggle_label}",
                on_click=toggle_intro_card,
            )

        if expanded:
            st.markdown(
                """
                <p class="hero-copy">
                  <span class="hero-copy-line">전세계약과 주택임대차에 관한 질문을 입력하면</span>
                  <span class="hero-copy-line">관련 법령·판례·공식 기관 안내를 찾아, 확인 가능한 근거와 함께 이해하기 쉽게 정리해 드립니다.</span>
                </p>
                <div class="hero-meta">
                  <span>출처와 함께 답변</span>
                  <span>후속 질문 맥락 반영</span>
                  <span>근거 부족 시 답변 보류</span>
                </div>
                """,
                unsafe_allow_html=True,
            )


def visible_answer_text(answer: Answer) -> str:
    """검증을 거친 사용자용 답변만 화면에 표시한다."""

    text = (answer.text or "").strip()
    if text:
        return text

    # raw_text는 검증 전 생성 원문일 수 있으므로 사용자 화면의 fallback으로 쓰지 않는다.
    return "답변 본문을 표시하지 못했습니다. 다시 질문해 주세요."


def answer_to_message(
    answer: Answer,
    *,
    used_history: bool = False,
    elapsed_seconds: float | None = None,
) -> dict:
    document_sources = (
        answer.document_sources()
        if hasattr(answer, "document_sources")
        else []
    )
    return {
        "role": "assistant",
        "content": visible_answer_text(answer),
        "status": answer.status,
        "sources": document_sources + answer.sources(),
        # 후속 질문에는 화면용 면책문구가 아니라 실제 생성 본문을 사용한다.
        "context_content": answer.raw_text if answer.status == "answered" else "",
        "used_history": used_history,
        "elapsed_seconds": elapsed_seconds,
        "document_ids": tuple(
            source["document_id"]
            for source in document_sources
            if source.get("document_id")
        ),
    }


def render_source_group(title: str, sources: list[dict]) -> None:
    if not sources:
        return

    st.markdown(f"**{title}**")
    for source in sources:
        label = source.get("label") or source.get("chunk_id") or "출처"
        url = source.get("url")

        if url:
            st.markdown(f"- [{label}]({url})")
        else:
            st.markdown(f"- {label}")


def render_sources(sources: list[dict]) -> None:
    if not sources:
        return

    document_sources = [
        source for source in sources
        if source.get("doc_type") == "uploaded_document"
    ]
    law_sources = [
        source for source in sources
        if source.get("doc_type") in {"law", "decree", "rule"}
    ]
    case_sources = [
        source for source in sources
        if source.get("doc_type") == "case"
    ]
    guide_sources = [
        source for source in sources
        if source.get("doc_type") == "guide"
    ]
    other_sources = [
        source for source in sources
        if source not in document_sources + law_sources + case_sources + guide_sources
    ]

    with st.expander(f"답변에 사용한 출처 {len(sources)}건"):
        render_source_group("업로드 문서 근거", document_sources)
        render_source_group("관련 법령", law_sources)
        render_source_group("관련 판례", case_sources)
        render_source_group("관련 기관 안내", guide_sources)
        render_source_group("기타 출처", other_sources)


def render_assistant_meta(message: dict) -> None:
    status = message.get("status")
    elapsed_seconds = message.get("elapsed_seconds")

    meta_parts = []
    if status:
        label, icon = STATUS_LABELS.get(status, (status, "ℹ️"))
        status_class = STATUS_CLASSES.get(status, "")
        meta_parts.append(
            f'<span class="status-pill {status_class}">{icon} {label}</span>'
        )

    if isinstance(elapsed_seconds, (int, float)):
        meta_parts.append(
            f'<span class="elapsed-time">⏱ {elapsed_seconds:.1f}초</span>'
        )

    if meta_parts:
        st.markdown(
            '<div class="answer-meta-row">' + "".join(meta_parts) + "</div>",
            unsafe_allow_html=True,
        )

    if message.get("used_history"):
        st.caption("↪ 이전 대화 맥락을 반영해 질문을 해석했습니다.")

    render_sources(message.get("sources", []))


def render_user_attachments(message: dict) -> None:
    """업로드 파일별 OCR 상태와 오류를 사용자 메시지 아래에 표시한다."""

    status_labels = {
        "queued": "대기",
        "processing": "OCR 처리 중",
        "completed": "완료",
        "needs_confirmation": "문서 종류 확인 필요",
        "failed": "실패",
    }
    for attachment in message.get("attachments", ()):
        status = status_labels.get(attachment.get("status"), "대기")
        kind_label = attachment.get("label")
        confidence = attachment.get("confidence")
        metadata = ""
        if kind_label and attachment.get("status") == "completed":
            metadata = f" · {kind_label} ({confidence})" if confidence else f" · {kind_label}"
        st.caption(f"첨부 · {attachment['name']} · {status}{metadata}")
        if attachment.get("error"):
            st.warning(attachment["error"])


def render_assistant_content(message: dict) -> None:
    """최종 답변 본문과 메타데이터를 하나의 안정된 슬롯에 그린다."""

    st.markdown(message["content"])
    render_assistant_meta(message)


def render_history(active_upload_message_id: str | None = None):
    active_attachment_slot = None
    for message in st.session_state["chat_messages"]:
        avatar = "🏠" if message["role"] == "assistant" else "👤"
        with st.chat_message(message["role"], avatar=avatar):
            if message["role"] == "assistant":
                render_assistant_content(message)
            else:
                st.markdown(message["content"])
                if message.get("message_id") == active_upload_message_id:
                    active_attachment_slot = st.empty()
                    with active_attachment_slot.container():
                        render_user_attachments(message)
                else:
                    render_user_attachments(message)
    return active_attachment_slot


def render_live_elapsed_timer() -> None:
    """질문 처리 중 브라우저에서 경과 시간을 계속 갱신한다.

    answer_question()은 서버 쪽에서 동기적으로 실행되므로 그동안 Streamlit rerun은
    일어나지 않는다. 대신 iframe 안의 JavaScript가 0.1초마다 독립적으로 시간을
    갱신해 사용자가 처리 진행 시간을 계속 볼 수 있게 한다.
    """

    st.iframe(
        """
        <style>
          html, body { margin: 0; overflow: hidden; }
        </style>
        <div style="
            display:flex;
            align-items:center;
            gap:8px;
            font-family:Arial, sans-serif;
            font-size:14px;
            color:#647587;
            padding:2px 0 8px 2px;
        ">
          <span>이전 대화와 관련 근거를 확인하고 있어요...</span>
          <strong id="jeonse-elapsed" style="color:#2176B8;">⏱ 0.0초</strong>
        </div>
        <script>
          const startedAt = performance.now();
          const timer = document.getElementById("jeonse-elapsed");

          function updateElapsed() {
            const seconds = (performance.now() - startedAt) / 1000;
            timer.textContent = `⏱ ${seconds.toFixed(1)}초`;
          }

          updateElapsed();
          setInterval(updateElapsed, 100);
        </script>
        """,
        height=42,
    )


def _upload_message(message_id: str) -> dict | None:
    return next(
        (
            message
            for message in st.session_state["chat_messages"]
            if message.get("message_id") == message_id
        ),
        None,
    )


def _sync_upload_message(upload_job: dict) -> None:
    message = _upload_message(upload_job["message_id"])
    if message is None:
        return
    message["attachments"] = [
        {
            "name": item["name"],
            "status": item["status"],
            "error": item.get("error"),
            "label": item.get("label"),
            "confidence": item.get("confidence"),
            "reason": item.get("reason"),
        }
        for item in upload_job["files"]
    ]


def _queue_upload_job(question: str, uploaded_files) -> str:
    """질문과 파일명을 먼저 저장하고 OCR은 다음 rerun에서 수행한다."""

    job_id = uuid4().hex
    message_id = f"upload-{job_id}"
    files = []
    for uploaded_file in uploaded_files:
        filename = uploaded_file.name
        try:
            data = uploaded_file.getvalue()
            status = "queued"
            error = None
        except Exception:
            logger.exception("채팅 첨부 파일을 읽는 중 오류가 발생했습니다.")
            data = b""
            status = "failed"
            error = "파일을 읽지 못했습니다. 다시 첨부해 주세요."
        files.append(
            {
                "name": filename,
                "data": data,
                "status": status,
                "error": error,
                "label": None,
                "confidence": None,
                "reason": None,
            }
        )

    previous_messages = list(st.session_state["chat_messages"])
    visible_question = question or "문서를 첨부했습니다."
    st.session_state["chat_messages"].append(
        {
            "message_id": message_id,
            "role": "user",
            "content": visible_question,
            "status": None,
            "sources": [],
            "context_content": question,
            "attachments": [
                {
                    "name": item["name"],
                    "status": item["status"],
                    "error": item.get("error"),
                    "label": item.get("label"),
                    "confidence": item.get("confidence"),
                    "reason": item.get("reason"),
                }
                for item in files
            ],
        }
    )
    st.session_state["upload_jobs"][job_id] = {
        "job_id": job_id,
        "message_id": message_id,
        "question": question,
        "files": files,
        "previous_messages": previous_messages,
        "status": "queued",
    }
    st.session_state["active_upload_job_id"] = job_id
    return job_id


def _store_uploaded_documents(
    upload_job: dict,
    upload_status,
) -> tuple[list[str], list[str], list[str]]:
    """파일별 상태를 저장하면서 세션 문서 OCR을 한 번씩 수행한다."""

    added_names: list[str] = []
    errors: list[str] = []
    confirmations: list[str] = []
    total = len(upload_job["files"])
    for index, item in enumerate(upload_job["files"], start=1):
        if item["status"] in {"completed", "needs_confirmation", "failed"}:
            if item["status"] == "completed":
                added_names.append(item["name"])
            elif item["status"] == "needs_confirmation" and item.get("error"):
                confirmations.append(f"{item['name']}: {item['error']}")
            elif item.get("error"):
                errors.append(f"{item['name']}: {item['error']}")
            continue

        item["status"] = "processing"
        upload_job["status"] = "processing"
        _sync_upload_message(upload_job)
        upload_status.update(
            label=f"첨부 문서 OCR 처리 중 ({index}/{total}) · {item['name']}",
            state="running",
            expanded=True,
        )
        st.write(f"{item['name']} · OCR 처리 중")

        try:
            error, details = _add_uploaded_document(item["name"], item["data"])
            item.update(details)
        except Exception:
            logger.exception("채팅 첨부 문서 분석 중 오류가 발생했습니다.")
            error = "문서를 분석하지 못했습니다. 파일 형식과 OCR 상태를 확인해 주세요."
            details = {}

        if error:
            needs_confirmation = item.get("kind") == "unknown"
            item["status"] = "needs_confirmation" if needs_confirmation else "failed"
            item["error"] = error
            target = confirmations if needs_confirmation else errors
            target.append(f"{item['name']}: {error}")
            st.warning(f"{item['name']} · {error}")
        else:
            item["status"] = "completed"
            item["error"] = None
            added_names.append(item["name"])
            st.success(f"{item['name']} · 완료")
        _sync_upload_message(upload_job)

    return added_names, errors, confirmations


def _append_assistant_notice(notice: str) -> None:
    """업로드 안내를 저장하고 현재 실행의 채팅 영역에 바로 표시한다."""

    message = {
        "role": "assistant",
        "content": notice,
        "status": None,
        "sources": [],
        "context_content": "",
    }
    st.session_state["chat_messages"].append(message)
    with st.chat_message("assistant", avatar="🏠"):
        render_assistant_content(message)


def _answer_visible_question(
    question: str,
    previous_messages: list[dict],
    retrieval_loader=None,
) -> None:
    with st.chat_message("assistant", avatar="🏠"):
        started_at = time.perf_counter()
        response_slot = st.empty()
        with response_slot.container():
            render_live_elapsed_timer()

        try:
            # 화면과 사용자 질문은 먼저 표시한다. 모델 준비가 아직 끝나지 않았다면
            # 여기서 최초 백그라운드 작업 하나만 기다린다.
            retrieval_service = (
                retrieval_loader.result() if retrieval_loader is not None else None
            )
            resolved = resolve_question(question, previous_messages)
            # 사전 로딩한 RetrievalService 를 그대로 넘긴다. 문서 전용 질문이면
            # answer_document_question 이 공식 검색 상한을 0으로 낮춘다.
            use_uploaded_document = (
                bool(st.session_state["session_documents"])
                and question_references_uploaded_document(
                    question,
                    _available_document_kinds(),
                )
            )
            answer_question_text = resolved.standalone
            used_history = resolved.used_history
            if use_uploaded_document:
                document_kind = referenced_document_kind(
                    question,
                    _available_document_kinds(),
                )
                if document_kind is None:
                    latest_document = next(
                        reversed(st.session_state["session_documents"].values()),
                        None,
                    )
                    document_kind = (
                        latest_document["kind"] if latest_document is not None else None
                    )
                answer_question_text = normalize_document_review_question(
                    question,
                    document_kind,
                )
                # 첨부 문서를 직접 가리키는 질문은 이전 채팅 재작성 대신 원문을 쓴다.
                used_history = False
            document_evidences = (
                find_document_evidences(answer_question_text)
                if use_uploaded_document
                else ()
            )
            if use_uploaded_document:
                answer = answer_document_question(
                    answer_question_text,
                    document_evidences,
                    service=retrieval_service,
                )
            else:
                answer = answer_question(
                    answer_question_text,
                    service=retrieval_service,
                )
        except Exception:
            # 사용자 화면에는 내부 예외를 숨기되 서버 터미널에는 traceback을 남긴다.
            logger.exception("Streamlit 질문 처리 중 예외가 발생했습니다.")
            answer = None
        finally:
            elapsed_seconds = time.perf_counter() - started_at

        if answer is None:
            error_message = (
                "답변을 불러오지 못했습니다. "
                "검색 인덱스와 Ollama 상태를 확인한 뒤 다시 시도해 주세요."
            )
            assistant_message = {
                "role": "assistant",
                "content": error_message,
                "status": None,
                "sources": [],
                "context_content": "",
            }
        else:
            assistant_message = answer_to_message(
                answer,
                used_history=used_history,
                elapsed_seconds=elapsed_seconds,
            )
        st.session_state["chat_messages"].append(assistant_message)
        response_slot.empty()
        with response_slot.container():
            render_assistant_content(assistant_message)


def process_question(question: str, retrieval_loader=None) -> None:
    """첨부 파일이 없는 일반 질문을 기존 RAG 흐름으로 처리한다."""

    previous_messages = list(st.session_state["chat_messages"])
    st.session_state["chat_messages"].append(
        {
            "role": "user",
            "content": question,
            "status": None,
            "sources": [],
            "context_content": question,
        }
    )
    with st.chat_message("user", avatar="👤"):
        st.markdown(question)
    _answer_visible_question(question, previous_messages, retrieval_loader)


def process_active_upload_job(retrieval_loader=None, attachment_slot=None) -> None:
    """화면에 먼저 표시된 활성 업로드 작업을 이어서 처리한다."""

    job_id = st.session_state.get("active_upload_job_id")
    upload_job = st.session_state["upload_jobs"].get(job_id)
    if upload_job is None:
        _finish_upload_job(job_id)
        return

    try:
        with st.status("첨부 문서 OCR 준비 중...", expanded=True) as upload_status:
            added_names, upload_errors, confirmation_requests = _store_uploaded_documents(
                upload_job,
                upload_status,
            )
            if upload_errors or confirmation_requests:
                upload_status.update(
                    label=(
                        "첨부 문서 OCR 완료 · 일부 파일을 확인해 주세요."
                        if added_names
                        else "첨부 문서 확인 필요"
                    ),
                    state="error",
                    expanded=True,
                )
            else:
                upload_status.update(
                    label="첨부 문서 OCR 완료",
                    state="complete",
                    expanded=False,
                )

        question = upload_job["question"]
        if not added_names:
            details = "\n\n".join(confirmation_requests + upload_errors)
            notice = details or "문서를 추가하지 못했습니다. 파일 상태를 확인한 뒤 다시 첨부해 주세요."
            _append_assistant_notice(notice)
        elif not question:
            notice = (
                "문서를 현재 세션에 추가했습니다. 이어서 문서에서 확인할 내용을 질문해 주세요."
            )
            _append_assistant_notice(notice)
        else:
            _answer_visible_question(
                question,
                upload_job["previous_messages"],
                retrieval_loader,
            )

        upload_job["status"] = "completed"
    except Exception:
        logger.exception("활성 업로드 작업 처리 중 예외가 발생했습니다.")
        upload_job["status"] = "failed"
        try:
            _append_assistant_notice(
                "첨부 문서 처리를 완료하지 못했습니다. 파일을 다시 첨부해 주세요."
            )
        except Exception:
            logger.exception("업로드 실패 안내를 화면에 표시하지 못했습니다.")
    finally:
        try:
            _sync_upload_message(upload_job)
            upload_message = _upload_message(upload_job["message_id"])
            if attachment_slot is not None and upload_message is not None:
                attachment_slot.empty()
                with attachment_slot.container():
                    render_user_attachments(upload_message)
        except Exception:
            logger.exception("업로드 메시지의 최종 상태를 동기화하지 못했습니다.")
        _finish_upload_job(job_id)


def main() -> None:
    configure_page()
    init_chat_state()
    reconcile_upload_job_state()
    active_job_id = st.session_state.get("active_upload_job_id")
    active_upload_job = st.session_state["upload_jobs"].get(active_job_id)
    active_upload_message_id = (
        active_upload_job.get("message_id") if active_upload_job is not None else None
    )
    readiness_area = st.container(key="retrieval_readiness_area")
    render_header()
    render_document_manager()

    chat_area = st.container(key="chat_area")
    with chat_area:
        active_attachment_slot = render_history(active_upload_message_id)

    submission = st.chat_input(
        "예: 전입신고 효력은? · 파일을 함께 첨부해 문서 내용을 질문할 수 있어요.",
        accept_file="multiple",
        file_type=("pdf", "jpg", "jpeg", "png"),
        submit_mode="disable",
    )

    # Streamlit은 위에서부터 화면 요소를 전송한다. 채팅 이력과 입력창을 먼저
    # 만든 뒤 무거운 KURE-v1 초기화를 시작해 첫 화면의 체감 대기를 줄인다.
    retrieval_loader = load_retrieval_service_loader()
    with readiness_area:
        render_retrieval_status(retrieval_loader)

    if st.session_state.get("active_upload_job_id"):
        with chat_area:
            process_active_upload_job(retrieval_loader, active_attachment_slot)
        return

    if submission:
        question = (getattr(submission, "text", submission) or "").strip()
        uploaded_files = tuple(getattr(submission, "files", ()) or ())
        if not question and not uploaded_files:
            return
        if uploaded_files:
            _queue_upload_job(question, uploaded_files)
            st.rerun()
        else:
            with chat_area:
                process_question(question, retrieval_loader)


if __name__ == "__main__":
    main()
