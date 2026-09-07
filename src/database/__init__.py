"""LENS 법령·판례 지식 DB 초기화 API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import DatabasePaths, resolve_database_paths
from .relational import DatabaseSummary, initialize_relational_database

if TYPE_CHECKING:
    from .vector import VectorStoreSummary, initialize_vector_store


def __getattr__(name: str):
    """Chroma가 필요한 벡터 API만 실제 사용 시 가져온다.

    원천 수집·파싱·SQLite 적재는 Chroma와 독립적인 단계다. 패키지 초기화에서
    벡터 모듈을 바로 불러오면 ``chromadb``가 없는 환경에서 JSONL 변환조차 실행할
    수 없으므로, 기존 ``from src.database import initialize_vector_store`` 공개 API는
    유지하면서 지연 로딩한다.
    """

    if name in {"VectorStoreSummary", "initialize_vector_store"}:
        from .vector import VectorStoreSummary, initialize_vector_store

        return {
            "VectorStoreSummary": VectorStoreSummary,
            "initialize_vector_store": initialize_vector_store,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "DatabasePaths",
    "DatabaseSummary",
    "VectorStoreSummary",
    "initialize_relational_database",
    "initialize_vector_store",
    "resolve_database_paths",
]
