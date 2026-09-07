"""OCR 텍스트만으로 업로드 문서의 종류를 보수적으로 판별한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .extraction_models import ExtractionResult


DocumentKind = Literal["registry", "contract", "unknown"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class DocumentKindClassification:
    kind: DocumentKind
    confidence: Confidence
    registry_score: int
    contract_score: int
    matched_signals: tuple[str, ...]
    reason: str


_REGISTRY_SIGNALS = (
    ("등기사항전부증명서", 6, "등기사항전부증명서"),
    ("등기사항증명서", 5, "등기사항증명서"),
    ("부동산등기부", 5, "부동산등기부"),
    ("표제부", 2, "표제부"),
    ("갑구", 2, "갑구"),
    ("을구", 2, "을구"),
    ("소유권에관한사항", 3, "소유권에 관한 사항"),
    ("소유권이외의권리에관한사항", 3, "소유권 이외의 권리에 관한 사항"),
    ("순위번호", 1, "순위번호"),
    ("근저당권", 2, "근저당권"),
    ("채권최고액", 2, "채권최고액"),
    ("등기목적", 2, "등기목적"),
)

_CONTRACT_SIGNALS = (
    ("주택임대차계약서", 6, "주택 임대차계약서"),
    ("부동산임대차계약서", 6, "부동산 임대차계약서"),
    ("임대차계약서", 5, "임대차계약서"),
    ("임대인", 2, "임대인"),
    ("임차인", 2, "임차인"),
    ("임대차기간", 2, "임대차 기간"),
    ("보증금", 1, "보증금"),
    ("차임", 1, "차임"),
    ("계약금", 1, "계약금"),
    ("잔금", 1, "잔금"),
    ("특약사항", 2, "특약사항"),
    ("중개대상물", 1, "중개대상물"),
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _score(text: str, signals) -> tuple[int, tuple[str, ...]]:
    matched = tuple(label for token, _weight, label in signals if token in text)
    score = sum(weight for token, weight, _label in signals if token in text)
    return score, matched


def classify_document_kind(extraction: ExtractionResult) -> DocumentKindClassification:
    """파일명이나 질문에 기대지 않고 OCR 본문의 구조 신호를 비교한다."""

    text = _compact(extraction.text)
    if not text:
        return DocumentKindClassification(
            kind="unknown",
            confidence="low",
            registry_score=0,
            contract_score=0,
            matched_signals=(),
            reason="읽을 수 있는 OCR 텍스트가 없어 문서 종류를 판단하지 못했습니다.",
        )

    registry_score, registry_matches = _score(text, _REGISTRY_SIGNALS)
    contract_score, contract_matches = _score(text, _CONTRACT_SIGNALS)
    difference = abs(registry_score - contract_score)
    top_score = max(registry_score, contract_score)

    if top_score < 5 or difference < 2:
        return DocumentKindClassification(
            kind="unknown",
            confidence="low",
            registry_score=registry_score,
            contract_score=contract_score,
            matched_signals=registry_matches + contract_matches,
            reason="등기사항증명서와 임대차계약서 신호를 충분히 구분하지 못했습니다.",
        )

    kind: DocumentKind = "registry" if registry_score > contract_score else "contract"
    matches = registry_matches if kind == "registry" else contract_matches
    confidence: Confidence = "high" if top_score >= 9 and difference >= 4 else "medium"
    label = "등기사항증명서" if kind == "registry" else "임대차계약서"
    return DocumentKindClassification(
        kind=kind,
        confidence=confidence,
        registry_score=registry_score,
        contract_score=contract_score,
        matched_signals=matches,
        reason=f"OCR에서 {', '.join(matches[:4])} 신호를 확인해 {label}로 분류했습니다.",
    )
