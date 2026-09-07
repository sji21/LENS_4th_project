"""임베딩 기반 Dense 검색기.

BM25 와 같은 `search(query, k, where)` 인터페이스를 지키므로 평가 하네스·스윕·
감사 도구가 코드 수정 없이 그대로 돌아간다.

임베딩 계산은 EmbeddingBackend 뒤에 숨겨 둔다. OpenAI API 든 로컬 모델이든
`embed(texts) -> list[list[float]]` 만 지키면 교체된다. 모델을 바꾸면 문서
벡터도 다시 계산해야 하므로 캐시 파일 이름에 모델명을 넣는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Protocol, Sequence

from src.retrieval.retriever import matches
from src.retrieval.terms import expand

CACHE_DIR = Path("data/index/embeddings")
logger = logging.getLogger(__name__)


class EmbeddingBackend(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class OpenAIEmbedding:
    """OpenAI 임베딩 API. 계획서 기준 모델."""

    def __init__(self, model: str = "text-embedding-3-small", batch: int = 64) -> None:
        from dotenv import load_dotenv
        from openai import OpenAI

        load_dotenv()
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY 가 없습니다 (.env 확인)")

        self.model = model
        self.name = model
        self.batch = batch
        self._client = OpenAI(api_key=key)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = [t.replace("\n", " ") for t in texts[i : i + self.batch]]
            response = self._client.embeddings.create(model=self.model, input=chunk)
            out.extend(item.embedding for item in response.data)
        return out


class SentenceTransformerEmbedding:
    """로컬 임베딩 모델 (KURE-v1, bge-m3 등)."""

    def __init__(
        self,
        model_id: str,
        device: str | None = None,
        batch: int = 16,
        prefer_local_cache: bool = True,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        self.name = model_id
        self.batch = batch
        if prefer_local_cache:
            try:
                # 캐시가 있는데 네트워크가 막힌 환경에서 Hugging Face의 파일별
                # 갱신 확인과 재시도가 초기 구동 시간을 대부분 차지하지 않게 한다.
                self._model = SentenceTransformer(
                    model_id,
                    device=device,
                    local_files_only=True,
                )
                return
            except OSError:
                logger.info(
                    "로컬 임베딩 모델 캐시가 없어 Hugging Face 다운로드를 시도합니다: %s",
                    model_id,
                )

        self._model = SentenceTransformer(
            model_id,
            device=device,
            local_files_only=False,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts), batch_size=self.batch, show_progress_bar=False,
            normalize_embeddings=False, convert_to_numpy=True,
        )
        return [v.tolist() for v in vectors]


# ── 벡터 연산 ───────────────────────────────────────────────────────────

def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def cosine(a: Sequence[float], b: Sequence[float], a_norm: float, b_norm: float) -> float:
    if a_norm == 0 or b_norm == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (a_norm * b_norm)


def _cache_path(backend_name: str, chunks: list[dict]) -> Path:
    """모델과 코퍼스가 같으면 재사용, 하나라도 바뀌면 다시 계산한다.

    지문에 **본문까지** 넣는다. chunk_id 만 쓰면 청크에 쉬운 설명을 덧붙이는 식으로
    본문을 고쳐도 ID 가 그대로라 옛 벡터를 재사용하게 되고, 바뀐 내용이 반영되지
    않은 채로 평가가 돌아간다.
    """
    digest = hashlib.sha256()
    digest.update(backend_name.encode("utf-8"))
    for chunk in chunks:
        digest.update(chunk["chunk_id"].encode("utf-8"))
        digest.update(b"\x00")
        digest.update(chunk["text"].encode("utf-8"))
        digest.update(b"\x00")
    safe = backend_name.replace("/", "_")
    return CACHE_DIR / f"{safe}-{digest.hexdigest()[:16]}.json"


class DenseRetriever:
    """문서 벡터와 질의 벡터의 코사인 유사도로 순위를 매긴다."""

    def __init__(
        self,
        chunks: list[dict],
        backend: EmbeddingBackend,
        use_cache: bool = True,
    ) -> None:
        self.chunks = chunks
        self.backend = backend
        self.chunk_ids = [c["chunk_id"] for c in chunks]

        cache = _cache_path(backend.name, chunks)
        if use_cache and cache.exists():
            self.vectors = json.loads(cache.read_text(encoding="utf-8"))
        else:
            self.vectors = backend.embed([c["text"] for c in chunks])
            if use_cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(self.vectors), encoding="utf-8")

        self.norms = [_norm(v) for v in self.vectors]

    def search(
        self,
        query: str,
        k: int,
        where: dict | None = None,
        expand_weight: float = 0.0,
    ) -> list[tuple[str, float]]:
        """expand_weight 는 인터페이스 호환용이다.

        용어 사전은 낱말 겹침을 늘리는 장치라 어휘 기반 검색을 위한 것이다.
        Dense 는 의미로 매칭하므로 확장어를 질의 문자열에 덧붙이기만 하고
        가중치는 주지 않는다.
        """
        text = query
        if expand_weight > 0:
            added = expand(query)
            if added:
                text = f"{query} {' '.join(added)}"

        q_vector = self.backend.embed([text])[0]
        q_norm = _norm(q_vector)

        scores: list[tuple[str, float]] = []
        for i, vector in enumerate(self.vectors):
            if where and not matches(self.chunks[i]["metadata"], where):
                continue
            score = cosine(q_vector, vector, q_norm, self.norms[i])
            if score > 0:
                scores.append((self.chunk_ids[i], score))

        # BM25 와 같은 규칙으로 동점을 깨어 재실행 결과가 흔들리지 않게 한다.
        scores.sort(key=lambda x: (-x[1], x[0]))
        return scores[:k]


class ChromaRetriever:
    """Chroma 컬렉션에서 읽는 검색기.

    DenseRetriever 와 달리 문서 벡터를 메모리에 들고 있지 않고 DB 에 위임한다.
    인터페이스는 같으므로 평가 하네스·스윕·감사 도구가 그대로 돌아간다.
    """

    def __init__(
        self,
        backend: EmbeddingBackend,
        path: str | Path,
        collection_name: str = "knowledge_chunks",
    ) -> None:
        import chromadb

        self.backend = backend
        self.path = Path(path)
        client = chromadb.PersistentClient(path=str(self.path))
        self.collection = client.get_collection(name=collection_name)

    def count(self) -> int:
        return self.collection.count()

    def search(
        self,
        query: str,
        k: int,
        where: dict | None = None,
        expand_weight: float = 0.0,
    ) -> list[tuple[str, float]]:
        text = query
        if expand_weight > 0:
            added = expand(query)
            if added:
                text = f"{query} {' '.join(added)}"

        q_vector = self.backend.embed([text])[0]
        result = self.collection.query(
            query_embeddings=[q_vector],
            n_results=k,
            where=where or None,
        )

        ids = result.get("ids") or [[]]
        distances = result.get("distances") or [[]]
        if not ids[0]:
            return []

        # Chroma 는 거리를 준다. 코사인 거리는 작을수록 가까우므로 뒤집는다.
        # 이 변환을 빠뜨리면 순위가 정확히 반대가 된다.
        scores = [(cid, 1.0 - dist) for cid, dist in zip(ids[0], distances[0])]
        scores.sort(key=lambda x: (-x[1], x[0]))
        return scores
