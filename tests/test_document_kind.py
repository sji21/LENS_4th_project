from __future__ import annotations

from types import SimpleNamespace

from src.document_check.document_kind import classify_document_kind
from src.document_check.extraction_models import ExtractionResult, PageExtraction
from src.document_check import upload_analysis


def extraction(text: str) -> ExtractionResult:
    return ExtractionResult(
        pages=(PageExtraction(1, text, "tesseract", len(text)),),
        elapsed_seconds=0.1,
    )


def test_classifies_registry_from_ocr_structure_not_filename():
    result = classify_document_kind(
        extraction("등 기 사 항 전 부 증 명 서 갑 구 소유권에 관한 사항 을 구 근저당권")
    )

    assert result.kind == "registry"
    assert result.confidence == "high"
    assert "갑구" in result.matched_signals


def test_classifies_contract_from_ocr_structure_not_filename():
    result = classify_document_kind(
        extraction("주택 임대차 계약서 임대인 임차인 보증금 차임 임대차 기간 특약 사항")
    )

    assert result.kind == "contract"
    assert result.confidence == "high"
    assert "임대인" in result.matched_signals


def test_ambiguous_or_weak_ocr_does_not_default_to_contract():
    result = classify_document_kind(extraction("보증금 관련 참고 문서"))

    assert result.kind == "unknown"
    assert result.confidence == "low"
    assert "구분하지 못했습니다" in result.reason


def test_uploaded_document_is_extracted_once_and_reuses_same_result(monkeypatch):
    extracted = extraction("등기사항증명서 갑구 을구 소유권 근저당권")
    calls: list[tuple[str, object]] = []

    def fake_extract(filename: str, data: bytes):
        calls.append(("extract", (filename, data)))
        return extracted

    def fake_registry(filename: str, received: ExtractionResult):
        calls.append(("registry", received))
        return SimpleNamespace(extraction=received)

    monkeypatch.setattr(upload_analysis, "extract_document_text", fake_extract)
    monkeypatch.setattr(
        upload_analysis.registry_service,
        "analyze_registry_extraction",
        fake_registry,
    )

    result = upload_analysis.analyze_uploaded_document("camera.jpg", b"image")

    assert result.classification.kind == "registry"
    assert result.extraction is extracted
    assert calls == [
        ("extract", ("camera.jpg", b"image")),
        ("registry", extracted),
    ]


def test_unknown_document_stops_before_domain_analysis(monkeypatch):
    monkeypatch.setattr(
        upload_analysis,
        "extract_document_text",
        lambda *_args: extraction("내용을 식별하기 어려운 문서"),
    )
    monkeypatch.setattr(
        upload_analysis.registry_service,
        "analyze_registry_extraction",
        lambda *_args: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")),
    )
    monkeypatch.setattr(
        upload_analysis.contract_service,
        "analyze_contract_extraction",
        lambda *_args: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")),
    )

    result = upload_analysis.analyze_uploaded_document("unknown.png", b"image")

    assert result.classification.kind == "unknown"
    assert result.analysis is None
