"""문서 점검 파이프라인이 공유하는 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Severity = Literal["high", "caution", "info"]


@dataclass(frozen=True)
class SourceReference:
    title: str
    url: str


@dataclass(frozen=True)
class RiskSignal:
    rule_id: str
    title: str
    severity: Severity
    section: str
    matched_keyword: str
    page_number: int
    evidence: str
    guidance: str
    checks: tuple[str, ...]
    sources: tuple[SourceReference, ...]

