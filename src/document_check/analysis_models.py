"""문서 점검 서비스와 화면이 공유하는 종합 결과 모델."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from .extraction_models import ExtractionResult
from .models import RiskSignal


AnalysisStatus = Literal["review_required", "check_required", "no_signal", "abstain"]


@dataclass(frozen=True)
class DocumentAnalysis:
    filename: str
    status: AnalysisStatus
    headline: str
    summary: str
    extraction: ExtractionResult
    signals: tuple[RiskSignal, ...]
    common_checks: tuple[str, ...]
    rag_queries: tuple[str, ...]
    disclaimer: str
    masked_text_preview: str = field(repr=False)

    def to_public_dict(self) -> dict:
        """민감할 수 있는 전체 추출문을 제외한 공유용 결과를 반환한다."""

        payload = asdict(self)
        payload.pop("filename", None)
        payload.pop("masked_text_preview", None)
        for page in payload["extraction"]["pages"]:
            page.pop("text", None)
        return payload
