"""PATCH-005 실제 등기 PDF를 사용하는 선택적 통합 테스트."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.document_check.service import analyze_registry_pdf
from tests.test_streamlit_app import install_ui_test_stubs


ROOT = Path(__file__).resolve().parents[1]
LOCAL_SAMPLE = ROOT / "test" / "data" / "부동산등기부등본.pdf"
APP_PATH = ROOT / "app" / "streamlit_app.py"


def sample_path() -> Path:
    configured = os.getenv("REGISTRY_SAMPLE_PDF", "").strip()
    return Path(configured).expanduser() if configured else LOCAL_SAMPLE


@pytest.mark.integration
def test_registry_pdf_end_to_end() -> None:
    path = sample_path()
    if not path.is_file():
        pytest.skip("REGISTRY_SAMPLE_PDF를 지정하면 로컬 등기 PDF 통합 테스트를 실행합니다.")

    result = analyze_registry_pdf(path.name, path.read_bytes())

    assert result.extraction.page_count > 0
    assert result.extraction.unreadable_page_count == 0
    assert result.status in {"review_required", "check_required", "no_signal"}
    assert result.extraction.elapsed_seconds > 0
    assert result.rag_queries
    assert "masked_text_preview" not in result.to_public_dict()


def test_streamlit_uses_chat_attachment_for_registry_documents(monkeypatch) -> None:
    # 화면 구조만 확인하는 테스트다. main 병합으로 앱이 시작할 때 KURE-v1을 미리
    # 로딩하게 되어(PATCH-034) 실제 모델을 올리면 20초 제한을 넘긴다. 다른
    # Streamlit UI 테스트와 같은 스텁을 쓴다.
    install_ui_test_stubs(monkeypatch)
    app = AppTest.from_file(APP_PATH).run(timeout=20)

    assert not app.exception
    assert len(app.chat_input) == 1
    assert not app.get("file_uploader")
