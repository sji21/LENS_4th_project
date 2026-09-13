from concurrent.futures import ThreadPoolExecutor

import pytest

from src.evaluation.baseline import settings
from src.retrieval.partitioned import PartitionedBM25Retriever
from src.retrieval.retriever import BM25Retriever, matches
from src.retrieval.service import LAW, RetrievalService


def chunk(cid, title, text, status="current", doc_type="law"):
    return {"chunk_id": cid, "text": text, "metadata": {
        "title": title, "article_id": title + "-제1조", "article_no": "제1조",
        "doc_type": doc_type, "status": status,
    }}


CORE = [chunk("a", "주택임대차보호법", "전입신고 임차인 대항력"),
        chunk("b", "주택임대차보호법", "계약 기간 반환"),
        chunk("c", "상가건물 임대차보호법", "전입신고")]
PROC = [chunk("p", "국세징수법 시행령", "체납 세금 열람 신고", doc_type="decree"),
        chunk("r", "주민등록법", "전입신고 거주지 신고"),
        chunk("old", "주민등록법", "전입신고", status="repealed")]


def test_core_statistics_and_old_database_are_preserved():
    old = RetrievalService(CORE)
    new = RetrievalService(CORE + PROC)
    old_bm = old._retrievers[LAW.name].members[0].retriever
    new_bm = new._retrievers[LAW.name].members[0].retriever
    assert isinstance(old_bm, BM25Retriever)
    assert isinstance(new_bm, PartitionedBM25Retriever)
    for query in ("전입신고", "계약 기간", "세금 체납", "상가"):
        assert old_bm.search(query, 20, expand_weight=1) == new_bm.partitions["core"].search(query, 20, expand_weight=1)
    assert settings(old)["corpora"]["law"]["retriever"]["bm25"] == {"k1": 1.5, "b": .75, "char_ngram": 2}
    new_bm.partitions["procedure"].b = .2
    config = settings(new)["corpora"]["law"]["retriever"]["bm25"]
    assert config["partitions"]["procedure"]["b"] == .2
    assert "국세징수법 시행령" in config["procedure_titles"]


@pytest.mark.parametrize("k", [-1, 0, 1, 3, 5, 30])
def test_pool_filter_depth_and_concurrent_queries(k):
    pooled = RetrievalService(CORE + PROC)._retrievers[LAW.name].members[0].retriever
    where = {"$and": [{"status": "current"}, {"title": {"$nin": ["상가건물 임대차보호법"]}}]}
    queries = ["전입신고", "계약 기간", "체납 세금"] * 4
    def run(q):
        return pooled.search(q, k, where, 1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        observed = list(executor.map(run, queries))
    for q, result in zip(queries, observed):
        all_hits = [hit for r in pooled.partitions.values() for hit in r.search(q, 100, where, 1)]
        expected = sorted(all_hits, key=lambda h: (-h[1], h[0]))[:max(0, k)]
        assert result == expected
        assert len({cid for cid, _ in result}) == len(result)
        assert not {"old", "c"} & {cid for cid, _ in result}


def test_one_dense_call_and_normal_result_contract():
    class Dense:
        calls = 0
        def search(self, query, k, where=None):
            self.calls += 1
            return [(c["chunk_id"], 1) for c in CORE + PROC if matches(c["metadata"], where)][:k]
    dense = Dense()
    service = RetrievalService(CORE + PROC, dense)
    result = service.search("전입신고", k_law=3, k_case=0, k_civil=0, k_guide=0)
    assert dense.calls == 1
    assert len(result.laws) == 3
    assert [e.rank for e in result.laws] == [1, 2, 3]
    assert not result.civil_laws
    assert "old" not in [e.chunk_id for e in result.laws]
    assert "## 관련 법령" in result.as_prompt_context()


def test_procedure_only_and_duplicate_partition_ids():
    service = RetrievalService(PROC)
    assert service.search("세금 열람", k_case=0, k_civil=0, k_guide=0).laws
    bm = BM25Retriever(CORE)
    with pytest.raises(ValueError, match="disjoint"):
        PartitionedBM25Retriever({"a": bm, "b": bm}, ())
