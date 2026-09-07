"""PATCH-010 임대차계약서 항목·특약 규칙 테스트."""

from __future__ import annotations

from src.contract_check.rules import check_contract_clauses, check_contract_fields
from src.contract_check.service import analyze_contract_document
from src.document_check.extraction_models import ExtractionResult, PageExtraction


def page(text: str) -> PageExtraction:
    return PageExtraction(1, text, "embedded_text", len(text))


def extraction(text: str, method: str = "embedded_text") -> ExtractionResult:
    return ExtractionResult(
        pages=(PageExtraction(1, text, method, len(text)),),  # type: ignore[arg-type]
        elapsed_seconds=0.1,
    )


COMPLETE_CONTRACT = """
주택 임대차 계약서
임차주택의 표시 소재지 서울특별시 종로구 세종대로 1
임대할 부분 101동 202호 전용면적 84.5㎡
보증금 금 200,000,000원
계약금 금 20,000,000원 2026년 8월 27일 중도금 금 0원 잔금 금 180,000,000원 2026년 9월 30일
임대차기간 2026년 9월 30일에서 2028년 9월 29일까지
계약체결일 2026년 8월 27일
임대인 성명 홍길동 임차인 성명 김임차
공인중개사 사무소 전세ON부동산 등록번호 12345
임대인 (인) 임차인 (인)
특약사항 임대인은 잔금 전 새로운 근저당을 설정하지 않고 권리관계를 유지한다.
임차인의 귀책없이 반환보증 가입 불가 시 계약을 취소하고 보증금을 반환한다.
누수 수리 비용은 임대인이 부담한다.
"""


def test_detects_written_core_fields_without_claiming_visual_signature() -> None:
    checks = {check.field_id: check for check in check_contract_fields((page(COMPLETE_CONTRACT),))}

    assert checks["property_address"].status == "confirmed"
    assert checks["deposit"].status == "confirmed"
    assert checks["lease_period"].status == "confirmed"
    assert checks["contract_date"].status == "confirmed"
    assert checks["signatures"].status == "review"


def test_blank_labels_require_original_document_review() -> None:
    checks = {
        check.field_id: check
        for check in check_contract_fields((page("임대차계약서 소재지 보증금 계약기간 임대인 임차인"),))
    }

    assert checks["property_address"].status == "review"
    assert checks["deposit"].status == "review"
    assert checks["contract_date"].status == "not_found"


def test_distinguishes_existing_and_recommended_clauses() -> None:
    clauses = {
        check.clause_id: check
        for check in check_contract_clauses(
            (page(COMPLETE_CONTRACT),),
            registry_signal_ids=("mortgage",),
        )
    }

    assert clauses["rights_freeze"].status == "included"
    assert clauses["guarantee_eligibility"].status == "included"
    assert clauses["repair_and_options"].status == "included"
    assert clauses["lien_cancellation"].status == "recommended"
    assert clauses["lien_cancellation"].related_registry_signal is True


def test_service_reports_missing_core_fields_and_masks_private_text(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.contract_check.service.extract_document_text",
        lambda *_: extraction(
            "주택 임대차계약서 임대인 성명 홍길동 임차인 성명 김임차 "
            "보증금 금 1억원 주민번호 900101-1234567"
        ),
    )

    result = analyze_contract_document("contract.pdf", b"%PDF-1.7")

    assert result.status == "review_required"
    assert any(field.field_id == "lease_period" and field.status == "not_found" for field in result.fields)
    assert "900101-1******" in result.masked_text_preview
    public_result = str(result.to_public_dict())
    assert "900101-1234567" not in public_result
    assert "홍길동" not in public_result


def test_service_abstains_for_non_contract_document(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.contract_check.service.extract_document_text",
        lambda *_: extraction("등기사항전부증명서 갑구 근저당권"),
    )

    result = analyze_contract_document("registry.pdf", b"%PDF-1.7")

    assert result.status == "abstain"
    assert result.fields == ()
    assert result.clauses == ()
