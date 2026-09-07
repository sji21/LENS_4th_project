"""PATCH-001 PDF 검증과 추출 전략 테스트."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from src.document_check import extraction
from src.document_check.extraction import DocumentValidationError, PdfInspection


@pytest.mark.parametrize(
    ("filename", "data", "message"),
    [
        ("registry.txt", b"%PDF-1.7", "PDF 파일만"),
        ("registry.pdf", b"", "비어"),
        ("registry.pdf", b"not a pdf", "PDF 형식"),
        ("registry.pdf", b"%PDF" + b"0" * (20 * 1024 * 1024), "20MB"),
    ],
)
def test_validate_pdf_rejects_invalid_uploads(filename: str, data: bytes, message: str) -> None:
    with pytest.raises(DocumentValidationError, match=message):
        extraction.validate_pdf(filename, data)


def image_bytes(format_name: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 240), "white").save(output, format=format_name)
    return output.getvalue()


@pytest.mark.parametrize("filename", ["contract.png", "contract.jpg", "contract.jpeg"])
def test_extract_document_accepts_contract_image_extensions(monkeypatch, filename: str) -> None:
    monkeypatch.setattr(extraction, "find_tesseract", lambda: "/test/tesseract")
    monkeypatch.setattr(extraction, "_ocr_language", lambda _: "kor+eng")

    def fake_ocr(data: bytes, *_: str) -> str:
        assert data.startswith(b"\x89PNG")
        return "주택 임대차계약서 보증금 1억원"

    monkeypatch.setattr(extraction, "_ocr_image", fake_ocr)
    source_format = "PNG" if filename.endswith("png") else "JPEG"

    result = extraction.extract_document_text(filename, image_bytes(source_format))

    assert result.pages[0].method == "tesseract"
    assert "임대차계약서" in result.text


def test_extract_document_rejects_broken_or_unsupported_image() -> None:
    with pytest.raises(extraction.DocumentValidationError, match="손상"):
        extraction.extract_document_text("contract.png", b"not an image")

    with pytest.raises(extraction.DocumentValidationError, match="PDF, JPG, JPEG, PNG"):
        extraction.extract_document_text("contract.webp", image_bytes())

    with pytest.raises(extraction.DocumentValidationError, match="확장자"):
        extraction.extract_document_text("contract.jpg", image_bytes("PNG"))


def test_uses_embedded_text_without_tesseract(monkeypatch: pytest.MonkeyPatch) -> None:
    embedded = "등기사항전부증명서 " * 10
    monkeypatch.setattr(
        extraction,
        "inspect_pdf",
        lambda _: PdfInspection(page_count=1, embedded_texts=(embedded,)),
    )
    monkeypatch.setattr(
        extraction,
        "find_tesseract",
        lambda: pytest.fail("충분한 텍스트 페이지에서는 Tesseract를 찾지 않아야 합니다."),
    )

    result = extraction.extract_pdf_text("registry.pdf", b"%PDF-1.7")

    assert result.page_count == 1
    assert result.pages[0].method == "embedded_text"
    assert result.text == embedded
    assert result.ocr_page_count == 0


def test_marks_empty_page_unreadable_when_tesseract_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        extraction,
        "inspect_pdf",
        lambda _: PdfInspection(page_count=1, embedded_texts=("",)),
    )
    monkeypatch.setattr(extraction, "find_tesseract", lambda: None)

    result = extraction.extract_pdf_text("registry.pdf", b"%PDF-1.7")

    assert result.pages[0].method == "unreadable"
    assert result.unreadable_page_count == 1
    assert "Tesseract" in result.warnings[0]


def test_prefers_longer_ocr_text_for_sparse_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        extraction,
        "inspect_pdf",
        lambda _: PdfInspection(page_count=1, embedded_texts=("짧은 글자",)),
    )
    monkeypatch.setattr(extraction, "find_tesseract", lambda: "/fake/tesseract")
    monkeypatch.setattr(extraction, "_ocr_language", lambda _: "kor+eng")
    monkeypatch.setattr(extraction, "_render_pages", lambda *_: {0: b"png"})
    monkeypatch.setattr(
        extraction,
        "_ocr_image",
        lambda *_: "을구 근저당권 설정 채권최고액 금 팔억육천사백만원",
    )

    result = extraction.extract_pdf_text("registry.pdf", b"%PDF-1.7")

    assert result.pages[0].method == "tesseract"
    assert "근저당권" in result.pages[0].text
    assert result.ocr_page_count == 1
