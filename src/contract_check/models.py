"""계약서 점검 서비스와 Streamlit 화면이 공유하는 모델."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from src.document_check.extraction_models import ExtractionResult
from src.document_check.models import SourceReference


FieldStatus = Literal["confirmed", "review", "not_found"]
ClauseStatus = Literal["included", "recommended"]
ContractStatus = Literal["review_required", "check_required", "core_detected", "abstain"]


@dataclass(frozen=True)
class ContractFieldCheck:
    field_id: str
    title: str
    importance: Literal["core", "conditional", "visual"]
    status: FieldStatus
    guidance: str
    page_number: int | None = None
    evidence: str = ""
    sources: tuple[SourceReference, ...] = ()


@dataclass(frozen=True)
class ContractClauseCheck:
    clause_id: str
    title: str
    status: ClauseStatus
    reason: str
    recommendation: str
    page_number: int | None = None
    evidence: str = ""
    related_registry_signal: bool = False
    sources: tuple[SourceReference, ...] = ()


@dataclass(frozen=True)
class ContractAnalysis:
    filename: str
    status: ContractStatus
    headline: str
    summary: str
    extraction: ExtractionResult
    fields: tuple[ContractFieldCheck, ...]
    clauses: tuple[ContractClauseCheck, ...]
    disclaimer: str
    masked_text_preview: str = field(repr=False)

    def to_public_dict(self) -> dict:
        """파일명과 OCR 원문을 제외한 팀 공유용 결과를 반환한다."""

        payload = asdict(self)
        payload.pop("filename", None)
        payload.pop("masked_text_preview", None)
        for page in payload["extraction"]["pages"]:
            page.pop("text", None)
        for field in payload["fields"]:
            field["evidence"] = ""
        for clause in payload["clauses"]:
            clause["evidence"] = ""
        return payload
