"""PATCH-014 Chroma 적재와 검색 테스트.

실제 임베딩 모델을 내려받지 않도록 결정론적인 가짜 백엔드를 쓴다.
확인하려는 것은 임베딩 품질이 아니라 적재·조회·점수 변환의 정확성이다.
"""

from __future__ import annotations

import gc
import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence

from src.retrieval.dense import ChromaRetriever, _cache_path
from src.retrieval.index import build_index, clean_metadata, index_dir_for


class FakeEmbedding:
    """글자 위치로 벡터를 만드는 결정론적 백엔드.

    같은 문자열은 항상 같은 벡터를 내고, 겹치는 글자가 많을수록 가까워진다.
    모델 다운로드 없이 순위 동작을 확인하기에 충분하다.
    """

    name = "fake/test-model"

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            v = [0.0] * self.dim
            for ch in text:
                v[ord(ch) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v])
        return vectors


def chunk(chunk_id: str, text: str, **meta) -> dict:
    base = {
        "article_id": f"테스트법-{chunk_id}",
        "title": "테스트법",
        "doc_type": "law",
        "status": "current",
    }
    base.update(meta)
    return {"chunk_id": chunk_id, "text": text, "metadata": base}


class MetadataTests(unittest.TestCase):
    def test_none_becomes_empty_string(self):
        """Chroma 는 None 을 거부한다."""
        self.assertEqual(clean_metadata({"expiry_date": None})["expiry_date"], "")

    def test_list_is_serialized_with_pipe(self):
        cleaned = clean_metadata({"refs": ["민법-제618조", "민사집행법-제88조"]})
        self.assertEqual(cleaned["refs"], "민법-제618조|민사집행법-제88조")

    def test_scalars_pass_through_unchanged(self):
        cleaned = clean_metadata({"s": "가", "i": 3, "f": 1.5, "b": True})
        self.assertEqual(cleaned, {"s": "가", "i": 3, "f": 1.5, "b": True})

    def test_result_is_chroma_safe(self):
        cleaned = clean_metadata({"a": None, "b": ["x"], "c": {"nested": 1}})
        for value in cleaned.values():
            self.assertIsInstance(value, (str, int, float, bool))


class EmbeddingCacheTests(unittest.TestCase):
    def test_changing_chunk_text_invalidates_cache(self):
        """본문을 고쳐도 chunk_id 가 같으면 옛 벡터로 평가된다."""
        before = [chunk("c1", "원래 본문")]
        after = [chunk("c1", "쉬운 설명을 덧붙인 본문")]
        self.assertNotEqual(_cache_path("m", before), _cache_path("m", after))

    def test_same_corpus_and_model_reuses_cache(self):
        chunks = [chunk("c1", "본문")]
        self.assertEqual(_cache_path("m", chunks), _cache_path("m", list(chunks)))

    def test_changing_model_invalidates_cache(self):
        chunks = [chunk("c1", "본문")]
        self.assertNotEqual(_cache_path("a", chunks), _cache_path("b", chunks))


class IndexPathTests(unittest.TestCase):
    def test_directory_name_carries_model_and_dimension(self):
        """차원은 컬렉션마다 하나뿐이므로 이름으로 구분해야 한다."""
        path = index_dir_for("nlpai-lab/KURE-v1", 1024, Path("data/index"))
        self.assertEqual(path.name, "chroma_kurev1_1024")

    def test_different_models_get_different_directories(self):
        a = index_dir_for("nlpai-lab/KURE-v1", 1024)
        b = index_dir_for("BAAI/bge-m3", 1024)
        self.assertNotEqual(a, b)


class SyncScopeTests(unittest.TestCase):
    """색인 한 번이 어디까지 지울 책임을 지는지.

    기존 테스트는 청크가 전부 doc_type="law" 라, 종류가 섞인 컬렉션을 한 번도
    넣어보지 않았다. 그래서 "판례만 재색인하면 법령이 전부 지워진다"를 놓쳤다.
    여기서는 입력의 doc_type 을 바꿔가며 넣는다.
    """

    LAWS = [
        chunk("law1", "대항력은 주택의 인도와 주민등록으로 생긴다"),
        chunk("law2", "보증금 증액청구는 20분의 1을 초과하지 못한다"),
    ]
    CASES = [
        chunk("case1", "임차권등기 후 이사해도 대항력이 유지된다", doc_type="case",
              title="대법원 2005다33039"),
    ]

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "chroma"
        self.backend = FakeEmbedding()

    def tearDown(self):
        gc.collect()
        try:
            self._tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def index(self, chunks, **kwargs):
        return build_index(chunks, self.backend, path=self.path, **kwargs)

    def test_indexing_cases_keeps_laws(self):
        """법령·판례를 같은 컬렉션에 각각 따로 넣는 운영을 지킨다."""
        self.index(self.LAWS)
        summary = self.index(self.CASES)
        self.assertEqual(summary.indexed, len(self.LAWS) + len(self.CASES))
        self.assertEqual(summary.removed, 0)

    def test_indexing_laws_keeps_cases(self):
        """반대 순서도 같아야 한다."""
        self.index(self.CASES)
        summary = self.index(self.LAWS)
        self.assertEqual(summary.indexed, len(self.LAWS) + len(self.CASES))
        self.assertEqual(summary.removed, 0)

    def test_stale_still_removed_within_the_same_doc_type(self):
        """범위를 좁혔다고 해서 원래 목적(빠진 조문 정리)을 잃으면 안 된다."""
        self.index(self.LAWS + self.CASES)
        summary = self.index(self.LAWS[:1])          # 법령 하나를 뺀 채 재색인
        self.assertEqual(summary.removed, 1)         # law2 만 정리
        self.assertEqual(summary.indexed, 1 + len(self.CASES))

    def test_summary_reports_the_scope_it_synced(self):
        summary = self.index(self.CASES)
        self.assertEqual(summary.scope, ("case",))

    def test_prune_all_crosses_doc_types(self):
        """어떤 종류를 코퍼스에서 통째로 뺄 때 쓰는 명시적 탈출구."""
        self.index(self.LAWS + self.CASES)
        summary = self.index(self.LAWS, prune_all=True)
        self.assertEqual(summary.removed, len(self.CASES))
        self.assertEqual(summary.indexed, len(self.LAWS))

    def test_empty_input_does_not_empty_the_collection(self):
        """청크 경로를 잘못 준 실행 한 번이 컬렉션을 비우면 안 된다."""
        self.index(self.LAWS)
        summary = self.index([])
        self.assertEqual(summary.removed, 0)
        self.assertEqual(summary.indexed, len(self.LAWS))

    def test_empty_input_does_not_empty_the_collection_with_prune_all(self):
        self.index(self.LAWS)
        summary = self.index([], prune_all=True)
        self.assertEqual(summary.indexed, len(self.LAWS))


class IndexAndSearchTests(unittest.TestCase):
    CHUNKS = [
        chunk("c1", "대항력은 주택의 인도와 주민등록으로 생긴다"),
        chunk("c2", "보증금 증액청구는 20분의 1을 초과하지 못한다"),
        chunk("c3", "상가 임대차의 갱신요구권", title="상가건물 임대차보호법"),
    ]

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "chroma"
        self.backend = FakeEmbedding()
        self.summary = build_index(self.CHUNKS, self.backend, path=self.path)

    def tearDown(self):
        # Chroma 의 PersistentClient 는 내부 SQLite 파일을 열어 둔 채 유지하고
        # 닫는 공개 API 가 없다. Windows 에서는 그 탓에 임시 폴더 삭제가 실패하는데,
        # 검사 대상과 무관한 정리 단계이므로 넘긴다. 폴더는 OS 가 회수한다.
        gc.collect()
        try:
            self._tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_indexes_every_chunk(self):
        self.assertEqual(self.summary.indexed, len(self.CHUNKS))
        self.assertEqual(self.summary.dimension, self.backend.dim)

    def test_reindexing_upserts_instead_of_duplicating(self):
        again = build_index(self.CHUNKS, self.backend, path=self.path)
        self.assertEqual(again.indexed, len(self.CHUNKS))

    def test_scores_are_descending_not_distances(self):
        """Chroma 는 거리를 주므로 뒤집어야 한다. 빠뜨리면 순위가 정확히 반대가 된다."""
        r = ChromaRetriever(self.backend, self.path)
        hits = r.search("대항력은 주택의 인도와 주민등록으로 생긴다", 3)
        self.assertGreater(len(hits), 1)
        self.assertEqual(hits[0][0], "c1")
        for before, after in zip(hits, hits[1:]):
            self.assertGreaterEqual(before[1], after[1])

    def test_identical_text_scores_near_one(self):
        """코사인 거리 0 이 점수 1 로 뒤집혀야 한다."""
        r = ChromaRetriever(self.backend, self.path)
        top_id, top_score = r.search(self.CHUNKS[0]["text"], 1)[0]
        self.assertEqual(top_id, "c1")
        self.assertAlmostEqual(top_score, 1.0, places=4)

    def test_metadata_filter_excludes_documents(self):
        r = ChromaRetriever(self.backend, self.path)
        where = {"title": {"$nin": ["상가건물 임대차보호법"]}}
        ids = [cid for cid, _ in r.search("갱신요구권", 5, where)]
        self.assertNotIn("c3", ids)

    def test_search_respects_k(self):
        r = ChromaRetriever(self.backend, self.path)
        self.assertLessEqual(len(r.search("대항력", 2)), 2)

    def test_shrinking_input_removes_stale_documents(self):
        """upsert 만 하면 이번 입력에서 빠진 청크가 계속 검색된다."""
        summary = build_index(self.CHUNKS[:1], self.backend, path=self.path)
        self.assertEqual(summary.indexed, 1)
        self.assertEqual(summary.removed, len(self.CHUNKS) - 1)
        r = ChromaRetriever(self.backend, self.path)
        self.assertNotIn("c2", [cid for cid, _ in r.search("보증금", 5)])

    def test_count_matches_indexed_total(self):
        r = ChromaRetriever(self.backend, self.path)
        self.assertEqual(r.count(), len(self.CHUNKS))


if __name__ == "__main__":
    unittest.main()
