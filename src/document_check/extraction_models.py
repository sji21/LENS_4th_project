"""PDF 텍스트 추출 단계의 독립 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PageExtraction:
    page_number: int
    text: str
    method: Literal["embedded_text", "tesseract", "unreadable"]
    character_count: int


@dataclass(frozen=True)
class ExtractionResult:
    pages: tuple[PageExtraction, ...]
    elapsed_seconds: float
    warnings: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def ocr_page_count(self) -> int:
        return sum(page.method == "tesseract" for page in self.pages)

    @property
    def unreadable_page_count(self) -> int:
        return sum(page.method == "unreadable" for page in self.pages)
