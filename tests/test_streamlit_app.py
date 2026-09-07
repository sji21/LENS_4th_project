"""LENS Streamlit 챗봇 초기 화면과 질문 처리 테스트."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import ModuleType

from streamlit.testing.v1 import AppTest

from src.document_check.extraction_models import ExtractionResult, PageExtraction


APP_PATH = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


class FakeAnswer:
    status = "answered"
    text = "테스트 답변입니다."
    raw_text = text

    @staticmethod
    def sources() -> list[dict]:
        return []


def install_ui_test_stubs(monkeypatch) -> None:
    """UI 테스트가 실제 검색 인덱스와 LLM을 준비하지 않게 한다."""

    dotenv_module = ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda *_args, **_kwargs: True

    chain_module = ModuleType("src.generation.chain")
    chain_module.answer_question = lambda *_args, **_kwargs: FakeAnswer()
    chain_module.get_default_service = lambda: object()
    chain_module.answer_document_question = lambda *_args, **_kwargs: FakeAnswer()

    # Streamlit의 Generation 진입점이 LangGraph로 바뀌었으므로
    # UI 테스트에서도 해당 import 경계만 같은 FakeAnswer로 대체한다.
    graph_module = ModuleType("src.generation.graph")
    graph_module.answer_question = lambda *_args, **_kwargs: FakeAnswer()
    graph_module.answer_document_question = lambda *_args, **_kwargs: FakeAnswer()

    conversation_module = ModuleType("src.generation.conversation")

    class ResolvedQuestion:
        standalone = "독립 질문"
        used_history = False

    conversation_module.resolve_question = (
        lambda _question, _messages: ResolvedQuestion()
    )

    models_module = ModuleType("src.generation.models")
    models_module.Answer = FakeAnswer

    monkeypatch.setitem(sys.modules, "dotenv", dotenv_module)
    monkeypatch.setitem(sys.modules, "src.generation.chain", chain_module)
    monkeypatch.setitem(sys.modules, "src.generation.graph", graph_module)
    monkeypatch.setitem(
        sys.modules,
        "src.generation.conversation",
        conversation_module,
    )
    monkeypatch.setitem(sys.modules, "src.generation.models", models_module)


def load_app(monkeypatch) -> AppTest:
    install_ui_test_stubs(monkeypatch)
    app = AppTest.from_file(APP_PATH)
    return app.run(timeout=20)


def test_initial_screen_is_chat_first_without_quick_questions(monkeypatch) -> None:
    app = load_app(monkeypatch)

    assert not app.exception
    assert len(app.chat_input) == 1
    assert not app.chat_input[0].disabled
    assert app.chat_input[0].placeholder.startswith("예: 전입신고")
    assert len(app.sidebar.button) == 0
    assert "🗑️ 대화 내용 지우기" not in [button.label for button in app.button]


def test_intro_card_starts_expanded_and_keeps_collapsed_state(monkeypatch) -> None:
    app = load_app(monkeypatch)

    assert any(button.label == "접기" for button in app.button)
    assert any(
        "전세계약과 주택임대차에 관한 질문" in markdown.value
        for markdown in app.markdown
    )

    next(button for button in app.button if button.label == "접기").click()
    app.run(timeout=20)

    assert not app.exception
    assert any(button.label == "펼치기" for button in app.button)
    assert not any(
        "전세계약과 주택임대차에 관한 질문" in markdown.value
        for markdown in app.markdown
    )

    app.run(timeout=20)
    assert any(button.label == "펼치기" for button in app.button)


def test_chat_input_runs_the_existing_answer_chain(monkeypatch) -> None:
    app = load_app(monkeypatch)
    app.chat_input[0].set_value(
        "전입신고와 확정일자를 받으면 어떤 효력이 있나요?"
    )
    app.run(timeout=20)

    assert not app.exception
    assert any("테스트 답변입니다." in markdown.value for markdown in app.markdown)
    answer_meta = [
        markdown.value
        for markdown in app.markdown
        if '<div class="answer-meta-row">' in markdown.value
    ]
    assert len(answer_meta) == 1
    assert "status-answered" in answer_meta[0]
    assert not app.chat_input[0].disabled

    # 일반 rerun에서도 같은 답변의 상태·시간 메타데이터는 한 번만 렌더링한다.
    app.run(timeout=20)
    answer_meta = [
        markdown.value
        for markdown in app.markdown
        if '<div class="answer-meta-row">' in markdown.value
    ]
    assert len(answer_meta) == 1


def _seed_active_upload_job(app: AppTest, *, question: str) -> None:
    previous_messages = list(app.session_state["chat_messages"])
    message_id = "upload-test-job"
    app.session_state["chat_messages"].append(
        {
            "message_id": message_id,
            "role": "user",
            "content": question,
            "status": None,
            "sources": [],
            "context_content": question,
            "attachments": [
                {
                    "name": "lease.png",
                    "status": "queued",
                    "error": None,
                    "label": None,
                    "confidence": None,
                    "reason": None,
                }
            ],
        }
    )
    app.session_state["upload_jobs"] = {
        "test-job": {
            "job_id": "test-job",
            "message_id": message_id,
            "question": question,
            "files": [
                {
                    "name": "lease.png",
                    "data": b"image-data",
                    "status": "queued",
                    "error": None,
                    "label": None,
                    "confidence": None,
                    "reason": None,
                }
            ],
            "previous_messages": previous_messages,
            "status": "queued",
        }
    }
    app.session_state["active_upload_job_id"] = "test-job"


def test_slow_ocr_keeps_one_visible_user_message_and_runs_once(monkeypatch) -> None:
    from src.document_check import upload_analysis

    calls: list[str] = []

    def slow_extraction(filename: str, _data: bytes):
        calls.append(filename)
        time.sleep(0.05)
        return ExtractionResult(
            pages=(
                PageExtraction(
                    page_number=1,
                    text="주택 임대차계약서 임대인 임차인 보증금 차임 임대차기간 특약사항",
                    method="tesseract",
                    character_count=38,
                ),
            ),
            elapsed_seconds=0.05,
        )

    monkeypatch.setattr(
        upload_analysis,
        "extract_document_text",
        slow_extraction,
    )
    app = load_app(monkeypatch)
    question = "첨부한 계약서에서 확인할 점을 알려줘"
    _seed_active_upload_job(app, question=question)

    # 느린 OCR을 시작하기 전 질문과 파일명이 이미 대화 상태에 저장돼 있다.
    assert sum(
        message.get("message_id") == "upload-test-job"
        for message in app.session_state["chat_messages"]
    ) == 1
    assert app.session_state["chat_messages"][-1]["attachments"][0]["status"] == "queued"

    app.run(timeout=20)

    assert not app.exception
    assert calls == ["lease.png"]
    user_messages = [
        message
        for message in app.session_state["chat_messages"]
        if message.get("message_id") == "upload-test-job"
    ]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == question
    assert user_messages[0]["attachments"] == [
        {
            "name": "lease.png",
            "status": "completed",
            "error": None,
            "label": "임대차계약서",
            "confidence": "high",
            "reason": (
                "OCR에서 주택 임대차계약서, 임대차계약서, 임대인, 임차인 신호를 "
                "확인해 임대차계약서로 분류했습니다."
            ),
        }
    ]
    assert app.session_state["active_upload_job_id"] is None
    assert app.session_state["upload_jobs"] == {}
    assert not app.chat_input[0].disabled
    assert any(
        "lease.png · 완료" in caption.value
        for caption in app.caption
    )

    # 완료 후 일반 rerun에서도 같은 파일의 OCR과 사용자 메시지가 중복되지 않는다.
    app.run(timeout=20)
    assert calls == ["lease.png"]
    assert sum(
        message.get("message_id") == "upload-test-job"
        for message in app.session_state["chat_messages"]
    ) == 1


def test_ocr_failure_keeps_question_filename_and_error(monkeypatch) -> None:
    from src.document_check import upload_analysis

    def failed_extraction(_filename: str, _data: bytes):
        raise RuntimeError("test OCR failure")

    monkeypatch.setattr(
        upload_analysis,
        "extract_document_text",
        failed_extraction,
    )
    app = load_app(monkeypatch)
    question = "이 계약서가 잘 보이는지 확인해줘"
    _seed_active_upload_job(app, question=question)

    app.run(timeout=20)

    assert not app.exception
    user_message = next(
        message
        for message in app.session_state["chat_messages"]
        if message.get("message_id") == "upload-test-job"
    )
    assert user_message["content"] == question
    assert user_message["attachments"][0]["name"] == "lease.png"
    assert user_message["attachments"][0]["status"] == "failed"
    assert "문서를 분석하지 못했습니다" in user_message["attachments"][0]["error"]
    assert app.session_state["active_upload_job_id"] is None
    assert app.session_state["upload_jobs"] == {}
    assert not app.chat_input[0].disabled


def test_orphaned_active_upload_id_is_cleared_before_chat_input(monkeypatch) -> None:
    app = load_app(monkeypatch)
    app.session_state["upload_jobs"] = {}
    app.session_state["active_upload_job_id"] = "missing-job"

    app.run(timeout=20)

    assert not app.exception
    assert app.session_state["active_upload_job_id"] is None
    assert app.session_state["upload_jobs"] == {}
    assert not app.chat_input[0].disabled


def test_terminal_upload_job_is_cleared_before_chat_input(monkeypatch) -> None:
    app = load_app(monkeypatch)
    app.session_state["upload_jobs"] = {
        "finished-job": {"job_id": "finished-job", "status": "completed"}
    }
    app.session_state["active_upload_job_id"] = "finished-job"

    app.run(timeout=20)

    assert not app.exception
    assert app.session_state["active_upload_job_id"] is None
    assert app.session_state["upload_jobs"] == {}
    assert not app.chat_input[0].disabled


def test_unexpected_upload_job_error_reactivates_chat_input(monkeypatch) -> None:
    app = load_app(monkeypatch)
    previous_messages = list(app.session_state["chat_messages"])
    message_id = "upload-broken-job"
    app.session_state["chat_messages"].append(
        {
            "message_id": message_id,
            "role": "user",
            "content": "문서를 확인해줘",
            "status": None,
            "sources": [],
            "context_content": "문서를 확인해줘",
            "attachments": [],
        }
    )
    app.session_state["upload_jobs"] = {
        "broken-job": {
            "job_id": "broken-job",
            "message_id": message_id,
            "question": "문서를 확인해줘",
            # len() 단계에서 예외가 발생하는 비정상 작업 상태를 재현한다.
            "files": None,
            "previous_messages": previous_messages,
            "status": "processing",
        }
    }
    app.session_state["active_upload_job_id"] = "broken-job"

    app.run(timeout=20)

    assert not app.exception
    assert app.session_state["active_upload_job_id"] is None
    assert app.session_state["upload_jobs"] == {}
    assert not app.chat_input[0].disabled
    assert any(
        "첨부 문서 처리를 완료하지 못했습니다" in message.get("content", "")
        for message in app.session_state["chat_messages"]
    )


def test_ambiguous_ocr_requests_confirmation_without_generic_answer(monkeypatch) -> None:
    from src.document_check import upload_analysis

    monkeypatch.setattr(
        upload_analysis,
        "extract_document_text",
        lambda *_args: ExtractionResult(
            pages=(
                PageExtraction(
                    page_number=1,
                    text="촬영 상태가 흐려 내용을 구분하기 어렵습니다",
                    method="tesseract",
                    character_count=21,
                ),
            ),
            elapsed_seconds=0.1,
        ),
    )
    app = load_app(monkeypatch)
    _seed_active_upload_job(app, question="이 문서에서 주의할 점을 알려줘")

    app.run(timeout=20)

    assert not app.exception
    user_message = next(
        message
        for message in app.session_state["chat_messages"]
        if message.get("message_id") == "upload-test-job"
    )
    assert user_message["attachments"][0]["status"] == "needs_confirmation"
    assert user_message["attachments"][0]["label"] == "종류 확인 필요"
    assert app.session_state["session_documents"] == {}
    assert any(
        "문서 종류를 확인" in message.get("content", "")
        for message in app.session_state["chat_messages"]
        if message.get("role") == "assistant"
    )


def test_registry_photo_is_classified_from_ocr_and_added_to_session(monkeypatch) -> None:
    from src.document_check import upload_analysis

    monkeypatch.setattr(
        upload_analysis,
        "extract_document_text",
        lambda *_args: ExtractionResult(
            pages=(
                PageExtraction(
                    page_number=1,
                    text="등기사항전부증명서 갑구 소유권에 관한 사항 을구 근저당권 채권최고액",
                    method="tesseract",
                    character_count=38,
                ),
            ),
            elapsed_seconds=0.1,
        ),
    )
    app = load_app(monkeypatch)
    _seed_active_upload_job(app, question="첨부한 등본에서 주의할 점을 알려줘")

    app.run(timeout=20)

    assert not app.exception
    assert len(app.session_state["session_documents"]) == 1
    document = next(iter(app.session_state["session_documents"].values()))
    assert document["kind"] == "registry"
    assert document["label"] == "등기사항증명서"
    assert document["classification_confidence"] == "high"
