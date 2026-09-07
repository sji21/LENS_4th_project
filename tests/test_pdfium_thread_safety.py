"""PATCH-006 PDFium 렌더링과 OCR 병렬 실행의 스레드 경계 테스트."""

from __future__ import annotations

import threading

import pytest

from src.document_check import extraction
from src.document_check.extraction import PdfInspection


def test_renders_before_running_parallel_tesseract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    render_threads: list[str] = []
    ocr_threads: list[str] = []

    monkeypatch.setattr(
        extraction,
        "inspect_pdf",
        lambda _: PdfInspection(page_count=2, embedded_texts=("", "")),
    )
    monkeypatch.setattr(extraction, "find_tesseract", lambda: "/fake/tesseract")
    monkeypatch.setattr(extraction, "_ocr_language", lambda _: "kor+eng")

    def fake_render_pages(*_: object) -> dict[int, bytes]:
        render_threads.append(threading.current_thread().name)
        return {0: b"page-1", 1: b"page-2"}

    def fake_ocr_image(image: bytes, *_: object) -> str:
        ocr_threads.append(threading.current_thread().name)
        return f"근저당권 설정 {image.decode()}"

    monkeypatch.setattr(extraction, "_render_pages", fake_render_pages)
    monkeypatch.setattr(extraction, "_ocr_image", fake_ocr_image)

    result = extraction.extract_pdf_text(
        "registry.pdf",
        b"%PDF-1.7",
        max_workers=2,
    )

    assert render_threads == [threading.main_thread().name]
    assert ocr_threads
    assert all(name.startswith("registry-ocr") for name in ocr_threads)
    assert result.ocr_page_count == 2
