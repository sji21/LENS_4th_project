"""PATCH-003 문서 분석 서비스 상태 판정 테스트."""

from __future__ import annotations

from src.document_check.extraction_models import ExtractionResult, PageExtraction
from src.document_check.service import analyze_registry_pdf


def extraction(text: str, method: str = "embedded_text") -> ExtractionResult:
    return ExtractionResult(
        pages=(PageExtraction(1, text, method, len(text)),),  # type: ignore[arg-type]
        elapsed_seconds=0.1,
    )


def test_service_requests_review_for_high_priority_signal(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.document_check.service.extract_pdf_text",
        lambda *_: extraction("갑구 가압류 을구 근저당권 설정"),
    )

    result = analyze_registry_pdf("registry.pdf", b"%PDF-1.7")

    assert result.status == "review_required"
    assert {signal.rule_id for signal in result.signals} >= {"seizure", "mortgage"}
    assert "안전" not in result.headline


def test_service_abstains_when_all_pages_are_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.document_check.service.extract_pdf_text",
        lambda *_: extraction("", "unreadable"),
    )

    result = analyze_registry_pdf("registry.pdf", b"%PDF-1.7")

    assert result.status == "abstain"
    assert result.signals == ()


def test_public_result_excludes_extracted_text_preview(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.document_check.service.extract_pdf_text",
        lambda *_: extraction("임대인 900101-1234567 기록사항 없음"),
    )

    result = analyze_registry_pdf("registry.pdf", b"%PDF-1.7")
    public = result.to_public_dict()

    assert "masked_text_preview" not in public
    assert "900101-1234567" not in str(public)
