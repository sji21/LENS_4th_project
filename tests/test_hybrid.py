"""PATCH-016 Hybrid(RRF) 검색기 테스트.

임베딩 모델을 내려받지 않도록 가짜 검색기를 쓴다. 확인하려는 것은 검색 품질이
아니라 순위 합치기의 정확성이다.
"""

from __future__ import annotations

import unittest

from src.retrieval.hybrid import HybridRetriever, Member


class FakeRetriever:
    """미리 정한 순서와 점수를 그대로 돌려주는 검색기."""

    def __init__(self, ranked: list[tuple[str, float]]) -> None:
        self.ranked = ranked
        self.calls: list[tuple[str, int, dict | None, float]] = []

    def search(self, query, k, where=None, expand_weight=0.0):
        self.calls.append((query, k, where, expand_weight))
        return self.ranked[:k]


class ProtocolOnlyRetriever:
    """`Retriever` 프로토콜이 요구하는 만큼만 구현한 검색기.

    프로토콜은 `search(query, k, where)` 까지만 약속한다. expand_weight 는 어휘
    기반에만 있는 선택 인자다. 위 FakeRetriever 는 그 인자를 받으므로, 그것만으로
    테스트하면 안 받는 구현(예: TfidfRetriever)을 넣었을 때의 TypeError 를 놓친다.
    """

    def __init__(self, ranked: list[tuple[str, float]]) -> None:
        self.ranked = ranked
        self.calls: list[tuple[str, int, dict | None]] = []

    def search(self, query, k, where=None):
        self.calls.append((query, k, where))
        return self.ranked[:k]


def rrf(rank: int, rrf_k: int = 60, weight: float = 1.0) -> float:
    return weight / (rrf_k + rank)


class FusionTests(unittest.TestCase):
    def test_document_found_by_both_outranks_one_found_by_either(self):
        """양쪽에서 잡힌 문서가 한쪽에서만 1등인 문서보다 위로 와야 한다."""
        a = FakeRetriever([("only_a", 9.0), ("both", 8.0)])
        b = FakeRetriever([("only_b", 9.0), ("both", 8.0)])
        h = HybridRetriever([Member(a, "a"), Member(b, "b")])
        self.assertEqual(h.search("q", 3)[0][0], "both")

    def test_score_scale_does_not_dominate(self):
        """BM25 는 0~50, 코사인은 0~1 이다. 점수를 더하면 BM25 가 압도한다."""
        big = FakeRetriever([("big_first", 480.0), ("shared", 470.0)])
        small = FakeRetriever([("small_first", 0.71), ("shared", 0.70)])
        h = HybridRetriever([Member(big, "big"), Member(small, "small")])
        ranked = [cid for cid, _ in h.search("q", 3)]
        self.assertEqual(ranked[0], "shared")
        # 각 검색기의 1위는 서로 같은 대우를 받아야 한다
        self.assertEqual(
            sorted(ranked[1:3]), sorted(["big_first", "small_first"])
        )

    def test_rrf_scores_match_the_formula(self):
        a = FakeRetriever([("x", 1.0), ("y", 0.5)])
        b = FakeRetriever([("y", 1.0)])
        h = HybridRetriever([Member(a, "a"), Member(b, "b")], rrf_k=60)
        scores = dict(h.search("q", 5))
        self.assertAlmostEqual(scores["x"], rrf(1))
        self.assertAlmostEqual(scores["y"], rrf(2) + rrf(1))

    def test_weight_scales_a_member_contribution(self):
        a = FakeRetriever([("a1", 1.0)])
        b = FakeRetriever([("b1", 1.0)])
        h = HybridRetriever([Member(a, "a", weight=2.0), Member(b, "b", weight=1.0)])
        scores = dict(h.search("q", 5))
        self.assertAlmostEqual(scores["a1"], rrf(1, weight=2.0))
        self.assertGreater(scores["a1"], scores["b1"])

    def test_ties_break_on_id_so_reruns_match(self):
        """동점 처리가 흔들리면 같은 설정을 두 번 돌려도 수치가 달라진다."""
        a = FakeRetriever([("zebra", 1.0)])
        b = FakeRetriever([("apple", 1.0)])
        h = HybridRetriever([Member(a, "a"), Member(b, "b")])
        self.assertEqual([cid for cid, _ in h.search("q", 2)], ["apple", "zebra"])


class DelegationTests(unittest.TestCase):
    def test_members_keep_their_own_expand_weight(self):
        """용어 사전은 어휘 기반에만 효과가 있어 검색기마다 값이 다르다."""
        lexical = FakeRetriever([("a", 1.0)])
        dense = FakeRetriever([("b", 1.0)])
        h = HybridRetriever(
            [Member(lexical, "bm25", expand_weight=1.0),
             Member(dense, "kure", expand_weight=0.0)]
        )
        h.search("질문", 5, expand_weight=0.5)   # 인자는 무시된다
        self.assertEqual(lexical.calls[0][3], 1.0)
        self.assertEqual(dense.calls[0][3], 0.0)

    def test_retriever_without_expand_weight_works(self):
        """expand_weight 를 안 받는 구현도 그대로 합칠 수 있어야 한다."""
        plain = ProtocolOnlyRetriever([("p1", 1.0)])
        lexical = FakeRetriever([("a1", 1.0)])
        h = HybridRetriever(
            [Member(plain, "tfidf", expand_weight=1.0),
             Member(lexical, "bm25", expand_weight=1.0)]
        )
        ranked = [cid for cid, _ in h.search("질문", 5)]
        self.assertEqual(sorted(ranked), ["a1", "p1"])
        self.assertEqual(plain.calls[0][0], "질문")

    def test_expand_weight_still_reaches_members_that_accept_it(self):
        """안 받는 구현을 지원하느라 받는 구현까지 건너뛰면 안 된다."""
        lexical = FakeRetriever([("a1", 1.0)])
        h = HybridRetriever([Member(lexical, "bm25", expand_weight=1.0)])
        h.search("질문", 5)
        self.assertEqual(lexical.calls[0][3], 1.0)

    def test_filter_passes_through_to_members(self):
        a = FakeRetriever([("a", 1.0)])
        h = HybridRetriever([Member(a, "a")])
        where = {"doc_type": {"$in": ["law"]}}
        h.search("q", 5, where)
        self.assertEqual(a.calls[0][2], where)

    def test_members_are_asked_deeper_than_the_final_k(self):
        """최종 k 만큼만 뽑으면 한쪽에서만 상위인 문서가 합류하지 못한다."""
        a = FakeRetriever([(f"d{i}", 1.0) for i in range(30)])
        h = HybridRetriever([Member(a, "a")], depth=20)
        h.search("q", 5)
        self.assertEqual(a.calls[0][1], 20)

    def test_depth_never_falls_below_k(self):
        a = FakeRetriever([(f"d{i}", 1.0) for i in range(30)])
        h = HybridRetriever([Member(a, "a")], depth=3)
        h.search("q", 10)
        self.assertEqual(a.calls[0][1], 10)

    def test_search_respects_k(self):
        a = FakeRetriever([(f"d{i}", 1.0) for i in range(30)])
        h = HybridRetriever([Member(a, "a")])
        self.assertEqual(len(h.search("q", 4)), 4)

    def test_last_member_hits_shows_who_contributed(self):
        a = FakeRetriever([("a1", 1.0)])
        b = FakeRetriever([("b1", 1.0)])
        h = HybridRetriever([Member(a, "bm25"), Member(b, "kure")])
        h.search("q", 5)
        self.assertEqual(h.last_member_hits(), {"bm25": ["a1"], "kure": ["b1"]})

    def test_empty_member_results_do_not_break_fusion(self):
        empty = FakeRetriever([])
        other = FakeRetriever([("x", 1.0)])
        h = HybridRetriever([Member(empty, "empty"), Member(other, "other")])
        self.assertEqual([cid for cid, _ in h.search("q", 3)], ["x"])


if __name__ == "__main__":
    unittest.main()
