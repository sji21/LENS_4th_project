"""검색기(Retriever).

검색 방식이 무엇이든 아래 인터페이스만 지키면 평가 하네스가 그대로 돌아간다.

    search(query, k) -> [(chunk_id, score), ...]   점수 내림차순

어휘 기반으로 BM25 와 TF-IDF 가 있다. 임베딩 기반은 `src/retrieval/dense.py` 의
DenseRetriever(메모리)와 ChromaRetriever(영속 인덱스)가 같은 인터페이스를 지키고,
`src/retrieval/hybrid.py` 가 둘의 순위를 RRF 로 합친다.

서비스에서 쓰는 진입점은 `src/retrieval/service.py` 의 RetrievalService 다.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable, Protocol

from src.retrieval.terms import expand

# 한글 음절 / 한자 / 영숫자 덩어리를 하나의 낱말로 끊는다.
_WORD_RE = re.compile(r"[가-힣]+|[一-龥]+|[A-Za-z]+|[0-9]+")
# "제3조의2", "제88조" 같은 조문 지시어. 법령 검색에서 가장 중요한 정확일치 신호.
_ARTICLE_RE = re.compile(r"제\d+조(?:의\d+)?(?:제\d+항)?")


def tokenize(text: str, char_ngram: int = 2) -> list[str]:
    """한국어 검색용 토큰화.

    형태소 분석기 없이도 쓸 만하게 만들기 위해 세 종류를 섞는다.
      1. 낱말 그대로        — "확정일자", "임차권등기명령"
      2. 낱말 내부 문자 n-gram — 조사가 붙어 표기가 흔들려도 매칭되게
      3. 조문 지시어         — "제3조의2" 를 통째로 한 토큰으로 보존

    3번이 없으면 "제3조"와 "제3조의2"가 문자 n-gram 상에서 거의 구분되지 않는다.
    """
    tokens: list[str] = []

    for article in _ARTICLE_RE.findall(text):
        tokens.append(f"§{article}")

    for word in _WORD_RE.findall(text):
        tokens.append(word)
        if len(word) > char_ngram:
            for i in range(len(word) - char_ngram + 1):
                tokens.append(word[i : i + char_ngram])

    return tokens


def matches(metadata: dict, where: dict | None) -> bool:
    """Chroma 의 where 절과 같은 형태의 메타데이터 필터.

    나중에 Chroma 로 갈아탈 때 필터 정의를 그대로 옮길 수 있도록 문법을 맞춘다.

        {"status": "current"}                      같음
        {"doc_type": {"$in": ["law", "decree"]}}  포함
        {"title": {"$nin": ["상가건물 임대차보호법"]}}  제외
        {"$and": [ ... ]}                          모두 만족

    Chroma 는 조건이 둘 이상이면 키를 나란히 두는 것을 거부하고 $and 를 요구한다
    (`Expected where to have exactly one operator`). 여기서도 같은 문법을 받아야
    같은 필터를 두 검색기에 그대로 넘길 수 있다.
    """
    if not where:
        return True
    if "$and" in where:
        return all(matches(metadata, cond) for cond in where["$and"])
    if "$or" in where:
        return any(matches(metadata, cond) for cond in where["$or"])
    for field, cond in where.items():
        value = metadata.get(field)
        if isinstance(cond, dict):
            if "$in" in cond and value not in cond["$in"]:
                return False
            if "$nin" in cond and value in cond["$nin"]:
                return False
            if "$ne" in cond and value == cond["$ne"]:
                return False
        elif value != cond:
            return False
    return True


class Retriever(Protocol):
    """모든 검색기가 지켜야 할 최소 약속."""

    def search(
        self, query: str, k: int, where: dict | None = None
    ) -> list[tuple[str, float]]:
        ...


class BM25Retriever:
    """문서 안에 질의어가 얼마나 자주, 얼마나 드물게 등장하는지로 점수를 매기는 고전 검색기.

    k1: 같은 단어가 반복 등장할 때 점수가 얼마나 더 오를지 (클수록 반복을 더 인정)
    b : 긴 문서에 얼마나 불이익을 줄지 (0이면 길이 무시, 1이면 길이로 완전 정규화)
    """

    def __init__(
        self,
        chunks: list[dict],
        k1: float = 1.5,
        b: float = 0.75,
        char_ngram: int = 2,
        query_expander: Callable[[str], list[str]] = expand,
    ) -> None:
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.char_ngram = char_ngram
        self.query_expander = query_expander

        self.chunk_ids = [c["chunk_id"] for c in chunks]
        self.doc_tokens = [tokenize(c["text"], char_ngram) for c in chunks]
        self.doc_freqs = [Counter(toks) for toks in self.doc_tokens]
        self.doc_lens = [len(toks) for toks in self.doc_tokens]
        self.avg_len = sum(self.doc_lens) / len(self.doc_lens) if self.doc_lens else 0.0

        n_docs = len(chunks)
        df: Counter[str] = Counter()
        for freqs in self.doc_freqs:
            df.update(freqs.keys())
        # 표준 BM25 IDF. 흔한 토큰일수록 0에 가까워진다.
        self.idf = {
            term: math.log(1 + (n_docs - n + 0.5) / (n + 0.5)) for term, n in df.items()
        }

    def search(
        self,
        query: str,
        k: int,
        where: dict | None = None,
        expand_weight: float = 0.0,
    ) -> list[tuple[str, float]]:
        """expand_weight > 0 이면 용어 사전으로 질의를 넓힌다.

        덧붙인 법률 용어는 사용자가 실제로 친 낱말보다 낮은 가중치를 준다.
        사전이 틀렸을 때 원래 질문을 덮어쓰지 않게 하려는 것이다.
        """
        q_terms: list[tuple[str, float]] = [
            (t, 1.0) for t in tokenize(query, self.char_ngram)
        ]
        if expand_weight > 0:
            for added in self.query_expander(query):
                q_terms += [
                    (t, expand_weight) for t in tokenize(added, self.char_ngram)
                ]

        scores: list[tuple[str, float]] = []

        for i, freqs in enumerate(self.doc_freqs):
            # Chroma 와 마찬가지로 점수 계산 전에 후보를 걸러낸다.
            # IDF 는 전체 코퍼스 기준을 유지한다(필터마다 재계산하지 않음).
            if where and not matches(self.chunks[i]["metadata"], where):
                continue
            score = 0.0
            norm = self.k1 * (1 - self.b + self.b * self.doc_lens[i] / self.avg_len)
            for term, weight in q_terms:
                tf = freqs.get(term)
                if not tf:
                    continue
                score += weight * self.idf.get(term, 0.0) * tf * (self.k1 + 1) / (tf + norm)
            if score > 0:
                scores.append((self.chunk_ids[i], score))

        # 동점일 때 순서가 흔들리면 같은 설정을 두 번 돌려도 점수가 달라진다.
        # chunk_id 를 2차 정렬 기준으로 두어 결정론적으로 만든다.
        scores.sort(key=lambda x: (-x[1], x[0]))
        return scores[:k]


class TfidfRetriever:
    """TF-IDF 코사인 유사도로 순위를 매기는 가벼운 어휘 검색기.

    외부 패키지나 API 없이 BM25와 같은 토큰을 사용한다. 따라서 이번 비교는
    토큰화 차이가 아니라 순위 산정 방식(BM25 vs TF-IDF)의 차이를 측정한다.
    """

    def __init__(self, chunks: list[dict], char_ngram: int = 2) -> None:
        self.chunks = chunks
        self.char_ngram = char_ngram
        self.chunk_ids = [c["chunk_id"] for c in chunks]
        self.doc_freqs = [Counter(tokenize(c["text"], char_ngram)) for c in chunks]

        n_docs = len(chunks)
        df: Counter[str] = Counter()
        for freqs in self.doc_freqs:
            df.update(freqs.keys())
        self.idf = {
            term: math.log((1 + n_docs) / (1 + frequency)) + 1
            for term, frequency in df.items()
        }

        self.doc_vectors = [self._vector(freqs) for freqs in self.doc_freqs]
        self.doc_norms = [self._norm(vector) for vector in self.doc_vectors]

    def _vector(self, freqs: Counter[str]) -> dict[str, float]:
        return {
            term: (1 + math.log(count)) * self.idf[term]
            for term, count in freqs.items()
            if term in self.idf and count > 0
        }

    @staticmethod
    def _norm(vector: dict[str, float]) -> float:
        return math.sqrt(sum(weight * weight for weight in vector.values()))

    def search(
        self, query: str, k: int, where: dict | None = None
    ) -> list[tuple[str, float]]:
        query_vector = self._vector(Counter(tokenize(query, self.char_ngram)))
        query_norm = self._norm(query_vector)
        if query_norm == 0:
            return []

        scores: list[tuple[str, float]] = []
        for chunk, chunk_id, doc_vector, doc_norm in zip(
            self.chunks, self.chunk_ids, self.doc_vectors, self.doc_norms
        ):
            if doc_norm == 0:
                continue
            if where and not matches(chunk["metadata"], where):
                continue
            dot = sum(
                weight * doc_vector.get(term, 0.0)
                for term, weight in query_vector.items()
            )
            score = dot / (query_norm * doc_norm)
            if score > 0:
                scores.append((chunk_id, score))

        scores.sort(key=lambda x: (-x[1], x[0]))
        return scores[:k]


def load_chunks(path: str | Path) -> list[dict]:
    """chunks jsonl 을 읽어 리스트로 돌려준다."""
    chunks = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def chunk_to_article(chunks: Iterable[dict]) -> dict[str, str]:
    """chunk_id -> article_id 매핑.

    평가 정답은 조문 단위로 잡고, 검색 결과(청크)를 이 표로 조문으로 환산해서 채점한다.
    청킹 방식이 바뀌어 chunk_id 가 전부 달라져도 평가셋은 그대로 쓸 수 있다.
    """
    return {c["chunk_id"]: c["metadata"]["article_id"] for c in chunks}
