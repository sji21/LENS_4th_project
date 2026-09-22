"""임대차계약서 문서의 작성 항목과 특약을 점검하는 서비스."""

from __future__ import annotations

from dataclasses import replace

from src.document_check.extraction import extract_document_text
from src.document_check.extraction_models import ExtractionResult
from src.document_check.privacy import mask_sensitive_text

from .models import ContractAnalysis, ContractFieldCheck
from .rules import check_contract_clauses, check_contract_fields, looks_like_contract
from .dates import extract_clause_dates, reconcile_dates


DISCLAIMER = (
    "이 결과는 OCR로 탐지한 문구를 바탕으로 한 작성 보조 정보이며 계약의 유효성이나 법적 안전성을 판정하지 않습니다. "
    "미탐지는 실제 누락이 아니라 OCR 실패일 수 있고, 추천 문구는 그대로 사용하기보다 계약 상황에 맞게 당사자·공인중개사 또는 법률 전문가와 검토하세요."
)


def analyze_contract_document(
    filename: str,
    data: bytes,
    *,
    registry_signal_ids: tuple[str, ...] = (),
) -> ContractAnalysis:
    extraction = extract_document_text(filename, data)
    return analyze_contract_extraction(
        filename,
        extraction,
        registry_signal_ids=registry_signal_ids,
    )


def analyze_contract_extraction(
    filename: str,
    extraction: ExtractionResult,
    *,
    registry_signal_ids: tuple[str, ...] = (),
) -> ContractAnalysis:
    """이미 추출한 OCR 결과를 재사용해 계약서 항목과 특약을 점검한다."""

    readable = bool(extraction.text.strip()) and extraction.unreadable_page_count < extraction.page_count
    contract_like = readable and looks_like_contract(extraction.pages)

    if not contract_like:
        return ContractAnalysis(
            filename=filename,
            status="abstain",
            headline="주택 임대차계약서로 충분히 판독하지 못했습니다",
            summary="결과를 추측하지 않습니다. 문서 종류와 OCR 상태를 확인하고 더 선명한 계약서 PDF 또는 사진을 업로드하세요.",
            extraction=extraction,
            fields=(),
            clauses=(),
            disclaimer=DISCLAIMER,
            masked_text_preview=mask_sensitive_text(extraction.text[:12000]),
        )

    fields = tuple(replace(item, guidance=(
        "현재 업로드한 페이지에서 확인되지 않았습니다. 실제 누락으로 판단하지 않습니다. 해당 항목이 있는 페이지나 선명한 확대 사진을 추가해 주세요. " + item.guidance
        if item.status == "not_found" else
        "항목 표시는 있지만 작성값을 확정하지 못했습니다. 질문에 필요한 부분이면 원본을 대조하거나 해당 부분을 선명하게 다시 촬영해 주세요. " + item.guidance
        if item.status == "review" and item.importance != "visual" else item.guidance
    )) for item in check_contract_fields(extraction.pages))
    uncertain_ocr = any("판독 신뢰도가 낮" in w or "판독 결과" in w for w in extraction.warnings)
    if uncertain_ocr:
        fields = tuple(replace(item, status="review", guidance="OCR 결과를 확정하기 어렵습니다. 해당 항목을 선명하게 확대 촬영하거나 원본과 대조해 주세요. " + item.guidance)
                       if item.status == "confirmed" else item for item in fields)
    clauses = check_contract_clauses(
        extraction.pages,
        registry_signal_ids=registry_signal_ids,
    )
    readings = list(extraction.date_readings)
    for page in extraction.pages:
        for item in extract_clause_dates(page.text, page.page_number):
            if any(previous.field_id == item.field_id and previous.value == item.value for previous in extraction.date_readings):
                continue
            # Whole-page uncertainty does not override independent focused OCR.
            status = "review" if any("판독 신뢰도가 낮" in w for w in extraction.warnings) else "confirmed"
            readings.append(replace(item, status=status))
    dates = reconcile_dates(readings)
    date_fields = []
    for field_id, title in (("handover_date", "인도기한(입주 관련 날짜)"),
                            ("lease_start_date", "임대차 시작일"),
                            ("lease_end_date", "임대차 종료일"),
                            ("balance_date", "잔금 지급일")):
        values = [item for item in dates if item.field_id == field_id]
        confirmed = len(values) == 1 and values[0].status == "confirmed"
        status = "confirmed" if confirmed else "review" if values else "not_found"
        guidance = (f"문구에서 읽은 날짜: {values[0].value}. 실제 이행일을 확정한 결과는 아니므로 원문과 대조해 주세요."
                    if confirmed else "현재 첨부 범위에서 날짜와 해당 조항을 명확히 연결하지 못했습니다. 실제 누락으로 판단하지 않습니다. 해당 조항과 날짜가 함께 보이는 확대 사진을 추가해 주세요.")
        date_fields.append(ContractFieldCheck(field_id, title, "conditional", status, guidance,
                                              page_number=values[0].page_number if values else None,
                                              evidence=values[0].evidence if confirmed else ""))
    fields += tuple(date_fields)
    for field_id, title in (("landlord_name", "임대인 성명"), ("tenant_name", "임차인 성명")):
        values = [item for item in extraction.table_values if item.field_id == field_id]
        confirmed = len(values) == 1 and values[0].status == "confirmed"
        fields += (ContractFieldCheck(
            field_id, title, "conditional", "confirmed" if confirmed else "review" if values else "not_found",
            "당사자 표시·성명 칸·값 칸을 같은 표에서 연결해 판독했습니다. 원문과 대조해 주세요."
            if confirmed else "현재 첨부 범위에서 당사자와 성명 칸을 정확히 연결하지 못했습니다. 실제 누락으로 판단하지 않습니다. 당사자 표시와 성명 칸이 함께 보이는 확대 사진을 추가해 주세요.",
            page_number=values[0].page_number if values else None),)
    missing_core = [
        field for field in fields if field.importance == "core" and field.status == "not_found"
    ]
    unclear_core = [
        field for field in fields if field.importance == "core" and field.status == "review"
    ]
    recommended = [clause for clause in clauses if clause.status == "recommended"]

    if missing_core:
        status = "review_required"
        headline = "계약서에서 찾지 못한 핵심 항목이 있습니다"
        summary = "OCR 미탐지일 수 있으므로 누락으로 단정하지 말고 아래 항목을 계약서 원본에서 우선 확인하세요."
    elif unclear_core:
        status = "check_required"
        headline = "값을 명확히 확인하기 어려운 핵심 항목이 있습니다"
        summary = "항목 표시는 찾았지만 작성값을 확정하지 못했습니다. 금액·날짜·당사자 정보를 원본과 대조하세요."
    elif recommended:
        status = "check_required"
        headline = "핵심 작성값은 탐지됐고 검토할 특약이 있습니다"
        summary = "추천 특약은 자동 삽입하지 않습니다. 해당 상황에 필요한지 확인하고 구체적인 조건을 협의하세요."
    else:
        status = "core_detected"
        headline = "등록된 핵심 항목과 특약 문구가 탐지됐습니다"
        summary = "탐지 결과가 계약서의 완전성이나 법적 효력을 보장하지 않으므로 원본과 공적 장부를 함께 확인하세요."

    return ContractAnalysis(
        filename=filename,
        status=status,
        headline=headline,
        summary=f"현재 첨부된 {extraction.page_count}쪽만 점검했습니다. 전체 계약서 제출 여부는 알 수 없습니다. " + summary,
        extraction=extraction,
        fields=fields,
        clauses=clauses,
        disclaimer=DISCLAIMER,
        masked_text_preview=mask_sensitive_text(extraction.text[:12000]),
        dates=dates,
    )


# 기존 호출부와 팀원 브랜치의 호환성을 유지한다.
analyze_contract_pdf = analyze_contract_document
