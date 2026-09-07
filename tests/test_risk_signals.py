"""PATCH-002 개인정보 마스킹과 위험 신호 규칙 테스트."""

from __future__ import annotations

from datetime import date

from src.document_check.extraction_models import PageExtraction
from src.document_check.privacy import mask_sensitive_text
from src.document_check.risk_rules import detect_risk_signals


def page(text: str, number: int = 1) -> PageExtraction:
    return PageExtraction(number, text, "embedded_text", len(text))


def test_masks_identifiers_without_masking_issue_date() -> None:
    text = "900101-1234567 010-1234-5678 123-456-789012 발급일 2026-08-20"

    masked = mask_sensitive_text(text)

    assert "900101-1******" in masked
    assert "010-****-5678" in masked
    assert "123-456-789012" not in masked
    assert "2026-08-20" in masked


def test_detects_signals_across_gabgu_and_eulgu() -> None:
    pages = (
        page("갑구 소유권이전 신탁 수탁자 주식회사 예시"),
        page("을구 근저당권 설정 채권최고액 864,000,000원 임차권 설정", 2),
    )

    signals = detect_risk_signals(pages, today=date(2026, 8, 26))
    by_id = {signal.rule_id: signal for signal in signals}

    assert by_id.keys() >= {"trust", "mortgage", "tenant_registration"}
    assert by_id["trust"].section == "갑구"
    assert by_id["mortgage"].section == "을구"
    assert by_id["tenant_registration"].severity == "high"
    assert "접수번호" not in by_id["tenant_registration"].checks


def test_reports_masked_evidence_and_source_page() -> None:
    signals = detect_risk_signals(
        (page("을구 임차권 설정 임차인 900101-1234567 보증금 100000000원", 4),),
        today=date(2026, 8, 26),
    )

    signal = next(item for item in signals if item.rule_id == "tenant_registration")

    assert signal.page_number == 4
    assert "900101-1******" in signal.evidence
    assert "900101-1234567" not in signal.evidence
    assert signal.sources


def test_only_uses_issue_date_near_issue_label() -> None:
    signals = detect_risk_signals(
        (page("등기원인 2020년 1월 1일\n발급일 2026년 8월 20일"),),
        today=date(2026, 8, 26),
    )

    assert "stale_document" not in {signal.rule_id for signal in signals}


def test_old_issue_date_requests_a_fresh_document() -> None:
    signals = detect_risk_signals(
        (page("발급일 2025년 3월 12일"),),
        today=date(2026, 8, 26),
    )

    stale = next(signal for signal in signals if signal.rule_id == "stale_document")
    assert stale.severity == "caution"
    assert any("재발급" in check for check in stale.checks)


def test_detects_spaced_ocr_issue_date() -> None:
    signals = detect_risk_signals(
        (page("발 급 일 2 0 2 5 년 0 3 월 1 2 일"),),
        today=date(2026, 8, 26),
    )

    assert "stale_document" in {signal.rule_id for signal in signals}


def test_rules_do_not_make_contract_safety_decision() -> None:
    signals = detect_risk_signals((page("갑구 가압류 을구 근저당권 설정"),))

    parts = []
    for signal in signals:
        parts.extend([signal.title, signal.guidance, *signal.checks])
    rendered = " ".join(parts)

    assert "계약해도 안전합니다" not in rendered
    assert "계약 가능합니다" not in rendered
