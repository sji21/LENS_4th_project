"""업로드 파일을 한 번만 OCR하고 문서 종류에 맞는 분석기로 연결한다."""

from __future__ import annotations

from dataclasses import dataclass

from src.contract_check import service as contract_service
from src.contract_check.models import ContractAnalysis

from . import service as registry_service
from .analysis_models import DocumentAnalysis
from .document_kind import DocumentKindClassification, classify_document_kind
from .extraction import extract_document_text
from .extraction_models import ExtractionResult


@dataclass(frozen=True)
class ClassifiedDocumentAnalysis:
    extraction: ExtractionResult
    classification: DocumentKindClassification
    analysis: DocumentAnalysis | ContractAnalysis | None


def analyze_uploaded_document(filename: str, data: bytes) -> ClassifiedDocumentAnalysis:
    extraction = extract_document_text(filename, data)
    classification = classify_document_kind(extraction)

    if classification.kind == "registry":
        analysis = registry_service.analyze_registry_extraction(filename, extraction)
    elif classification.kind == "contract":
        analysis = contract_service.analyze_contract_extraction(filename, extraction)
    else:
        analysis = None

    return ClassifiedDocumentAnalysis(
        extraction=extraction,
        classification=classification,
        analysis=analysis,
    )
