"""관계형 DB 청크를 검색하기 위한 파생 Chroma 저장소."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import chromadb


DEFAULT_COLLECTION = "knowledge_chunks"


@dataclass(frozen=True)
class VectorStoreSummary:
    path: Path
    collection_name: str
    document_count: int


def initialize_vector_store(
    path: Path,
    *,
    collection_name: str = DEFAULT_COLLECTION,
) -> VectorStoreSummary:
    """임베딩 없이 빈 컬렉션까지 생성한다. 실제 청크 적재는 인덱싱 단계가 담당한다."""

    chroma_path = path.expanduser().resolve()
    chroma_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={
            "hnsw:space": "cosine",
            "description": "법령·판례·정부 가이드 RAG 청크",
        },
    )
    return VectorStoreSummary(
        path=chroma_path,
        collection_name=collection.name,
        document_count=collection.count(),
    )
