"""Streamlit과 향후 LangChain 체인이 함께 사용할 문서 점검 서비스."""

from __future__ import annotations

from .analysis_models import DocumentAnalysis
from .extraction import extract_document_text, extract_pdf_text
from .extraction_models import ExtractionResult
from .models import RiskSignal
from .privacy import mask_sensitive_text
from .rag_handoff import build_rag_queries
from .risk_rules import detect_risk_signals


DISCLAIMER = (
    "이 결과는 업로드한 문서에서 주의 신호를 찾는 정보 제공용 점검이며 법률 자문이나 계약 안전성 판정이 아닙니다. "
    "OCR 누락과 등기 이후의 변동 가능성이 있으므로 계약·잔금 직전에 최신 원문을 확인하고 필요하면 공인중개사, HUG 또는 법률 전문가와 상담하세요."
)

COMMON_CHECKS = (
    "등기상 소유자와 실제 계약 상대방의 신분·권한이 일치하는지 확인하세요.",
    "계약 직전과 잔금 지급 직전에 최신 등기사항증명서를 다시 발급해 권리 변동을 확인하세요.",
    "근저당 등 선순위채권, 기존 임차보증금과 계약할 보증금을 주택가격과 함께 비교하세요.",
    "등기에 드러나지 않을 수 있는 미납국세·지방세와 선순위 임차인 정보도 별도로 확인하세요.",
    "보증금반환보증 가입 가능 여부와 요구 서류를 보증기관에서 확인하세요.",
)


def _status_for(extraction: ExtractionResult, signals: tuple[RiskSignal, ...]):
    if not extraction.text.strip() or extraction.unreadable_page_count == extraction.page_count:
        return (
            "abstain",
            "문서를 충분히 판독하지 못했습니다",
            "결과를 추측하지 않습니다. 한국어 OCR 설치 상태를 확인하거나 더 선명한 PDF를 업로드하세요.",
        )
    if any(signal.severity == "high" for signal in signals):
        return (
            "review_required",
            "계약 전에 우선 확인할 권리 신호가 있습니다",
            "발견된 문구만으로 계약 가능 여부를 판단할 수 없습니다. 아래 항목의 현재 효력과 말소 여부를 확인하세요.",
        )
    if signals:
        return (
            "check_required",
            "추가 확인이 필요한 항목이 있습니다",
            "등기에서 확인된 권리와 문서 상태를 계약 조건·주택가격·선순위 권리와 함께 검토하세요.",
        )
    return (
        "no_signal",
        "설정된 주요 위험 키워드는 탐지되지 않았습니다",
        "위험 신호가 없다는 사실이 계약의 안전을 의미하지는 않습니다. 공통 확인사항을 반드시 점검하세요.",
    )


def analyze_registry_extraction(
    filename: str,
    extraction: ExtractionResult,
) -> DocumentAnalysis:
    """이미 추출한 OCR 결과를 재사용해 등기 위험 신호를 점검한다."""

    signals = detect_risk_signals(extraction.pages)
    status, headline, summary = _status_for(extraction, signals)
    return DocumentAnalysis(
        filename=filename,
        status=status,
        headline=headline,
        summary=summary,
        extraction=extraction,
        signals=signals,
        common_checks=COMMON_CHECKS,
        rag_queries=build_rag_queries(signals),
        disclaimer=DISCLAIMER,
        masked_text_preview=mask_sensitive_text(extraction.text[:12000]),
    )


def analyze_registry_document(filename: str, data: bytes) -> DocumentAnalysis:
    """PDF 또는 촬영 이미지에서 등기 문서를 점검한다."""

    return analyze_registry_extraction(filename, extract_document_text(filename, data))


def analyze_registry_pdf(filename: str, data: bytes) -> DocumentAnalysis:
    """기존 PDF 전용 호출부와의 호환성을 유지한다."""

    return analyze_registry_extraction(filename, extract_pdf_text(filename, data))
