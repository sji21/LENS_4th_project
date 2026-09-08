"""PATCH-018 검색 진입점 테스트.

임베딩 모델(2.3GB)을 내려받지 않도록 가짜 Dense 검색기를 쓴다. 확인하려는 것은
검색 품질이 아니라 **법령과 판례가 섞이지 않고 따로 나오는지**, 그리고 생성 쪽이
쓸 출처가 제대로 붙는지다.
"""

from __future__ import annotations

import json
import logging
import unittest
from pathlib import Path
from unittest.mock import patch

from src.retrieval.retriever import matches
from src.retrieval.terms import expand, expand_law
from src.retrieval.hybrid import DEFAULT_RRF_K
from src.retrieval.service import (
    CASE,
    CIVIL,
    CIVIL_ARTICLE_IDS,
    CIVIL_TITLE,
    COMMERCIAL_LAWS,
    GUIDE,
    LAW,
    LAW_RRF_K,
    detect_guide_topics,
    Corpus,
    Evidence,
    RetrievalService,
    citation_of,
    detect_civil_topics,
    route_law_corpus,
    split_by_type,
)
from src.retrieval import service as service_module


def law_chunk(chunk_id: str, text: str, no: str, title: str = "주택임대차보호법",
              doc_type: str = "law") -> dict:
    return {
        "chunk_id": chunk_id,
        "doc_id": f"law-{title}",
        "text": text,
        "metadata": {
            "title": title,
            "doc_type": doc_type,
            "article_id": f"{title}-{no}",
            "article_no": no,
            "article_title": "대항력 등",
            "source_url": "https://law.go.kr/x",
            "status": "current",
        },
    }


def case_chunk(chunk_id: str, text: str, number: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "doc_id": f"case-document:{chunk_id}",
        "text": text,
        "metadata": {
            "title": "추심금",
            "doc_type": "case",
            "article_id": chunk_id,
            "court_name": "대법원",
            "case_number": number,
            "decision_date": "2013-01-17",
            "source_url": "https://law.go.kr/y",
            "status": "current",
        },
    }


def guide_chunk(chunk_id: str, text: str, title: str, topic: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "doc_id": f"guide-document:{chunk_id}",
        "text": text,
        "metadata": {
            "title": title,
            "doc_type": "guide",
            "article_id": chunk_id.split("#")[0],
            "topic": topic,
            "source_url": "https://www.khug.or.kr/z",
            "status": "current",
        },
    }


REPEALED = law_chunk(
    "law_old", "구법 시절의 대항력 조문이다. 지금은 폐지되었다", "제3조",
)
REPEALED["metadata"]["status"] = "repealed"

CHUNKS = [
    law_chunk("law1", "임차인이 주택의 인도와 주민등록을 마친 때에는 대항력이 생긴다", "제3조"),
    law_chunk("law2", "보증금은 후순위권리자보다 우선하여 변제받을 권리가 있다", "제3조의2"),
    law_chunk("dec1", "우선변제를 받을 임차인의 범위는 다음과 같다", "제10조",
              title="주택임대차보호법 시행령", doc_type="decree"),
    case_chunk("case1", "임차주택이 양도되면 양수인이 임대인의 지위를 승계한다", "2011다49523"),
    case_chunk("case2", "공동임차인 중 1명의 대항력은 임대차 전체에 미친다", "2021다238650"),
    guide_chunk("guide-HUG-전세보증금반환보증#0",
                "전세보증금반환보증은 임대인이 보증금을 돌려주지 않을 때 공사가 대신 지급한다",
                "HUG 전세보증금반환보증 상품안내", "전세보증금 반환보증"),
    guide_chunk("guide-HUG-전세보증금반환보증#1",
                "보증 신청 절차는 위탁 금융기관 또는 모바일 앱으로 진행한다",
                "HUG 전세보증금반환보증 상품안내", "전세보증금 반환보증"),
    guide_chunk("guide-국세청-미납국세열람#0",
                "임대인의 미납국세는 임대차개시일까지 세무서에서 열람 신청할 수 있다",
                "국세청 미납국세 등 열람신청 안내", "임대인 미납국세 열람"),
    REPEALED,
]

CIVIL_CHUNKS = [
    law_chunk(f"civil-{number}", text, f"제{number}조", title=CIVIL_TITLE)
    for number, text in (
        (623, "임대인은 목적물을 임차인에게 인도하고 사용 수익에 필요한 상태를 유지하게 할 의무를 부담한다"),
        (626, "임차인이 임차물 보존에 관한 필요비를 지출한 때에는 임대인에게 상환을 청구할 수 있다"),
        (627, "임차물 일부를 사용 수익할 수 없게 된 때에는 차임의 감액을 청구하고 목적을 달성할 수 없으면 해지할 수 있다"),
        (629, "임차인은 임대인의 동의 없이 권리를 양도하거나 임차물을 전대하지 못한다"),
        (632, "건물의 소부분을 타인에게 사용하게 하는 경우 전대 제한의 예외를 둔다"),
        (634, "임차물에 수리를 요하거나 권리를 주장하는 자가 있을 때에는 임대인에게 지체 없이 통지하여야 한다"),
        (640, "차임연체액이 2기의 차임액에 달하는 때에는 임대인은 계약을 해지할 수 있다"),
    )
]


class FakeDense:
    """모든 청크를 고정 순서로 돌려주는 Dense 대역.

    프로토콜이 요구하는 `search(query, k, where)` 만 구현한다. where 를 실제로
    적용해야 공유 컬렉션에서 묶음이 갈리는지 확인할 수 있다.
    """

    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks
        self.calls: list[tuple[str, int, dict | None]] = []

    def search(self, query, k, where=None):
        self.calls.append((query, k, where))
        # 실제 Chroma 와 같은 필터 의미를 쓴다. 직접 해석하면 $and 를 놓친다.
        hits = [
            (c["chunk_id"], 1.0 - i * 0.01)
            for i, c in enumerate(self.chunks)
            if matches(c["metadata"], where)
        ]
        return hits[:k]


def build(dense: bool = True) -> RetrievalService:
    return RetrievalService(CHUNKS, FakeDense(CHUNKS) if dense else None)


class SeparationTests(unittest.TestCase):
    """법령과 판례가 섞이면 안 된다."""

    def test_laws_and_cases_come_back_separately(self):
        result = build().search("집주인이 바뀌면 보증금은?", k_law=3, k_case=2)
        self.assertTrue(all(e.doc_type in LAW.doc_types for e in result.laws))
        self.assertTrue(all(e.doc_type in CASE.doc_types for e in result.cases))

    def test_decrees_are_grouped_with_laws(self):
        """시행령은 법령과 함께 다뤄야 한다. 따로 빠지면 근거가 반쪽이 된다."""
        result = build().search("우선변제를 받을 임차인의 범위", k_law=5, k_case=5)
        self.assertIn("dec1", [e.chunk_id for e in result.laws])

    def test_each_side_respects_its_own_k(self):
        result = build().search("대항력", k_law=2, k_case=1)
        self.assertEqual(len(result.laws), 2)
        self.assertEqual(len(result.cases), 1)

    def test_zero_k_skips_that_side(self):
        result = build().search("대항력", k_law=3, k_case=0)
        self.assertEqual(result.cases, [])
        self.assertTrue(result.laws)

    def test_shared_dense_index_is_filtered_per_corpus(self):
        """Chroma 는 컬렉션 하나를 공유한다. 필터가 빠지면 판례가 법령 쪽에 섞인다."""
        dense = FakeDense(CHUNKS)
        # 안내는 질문이 그 주제일 때만 검색하므로 신호어가 있는 질문을 쓴다.
        RetrievalService(CHUNKS, dense).search("전세보증금반환보증 대항력", k_law=3, k_case=3)
        filters = [call[2] for call in dense.calls]
        self.assertEqual(len(filters), 3)
        wanted = [LAW.doc_types, CASE.doc_types, GUIDE.doc_types]
        for where, doc_types in zip(filters, wanted):
            self.assertIn({"doc_type": {"$in": list(doc_types)}}, where["$and"])

    def test_guide_search_is_skipped_when_the_question_has_no_guide_topic(self):
        """관련 없는 질문에서는 안내 검색을 아예 돌리지 않는다."""
        dense = FakeDense(CHUNKS)
        RetrievalService(CHUNKS, dense).search("대항력", k_law=3, k_case=3)
        self.assertEqual(len(dense.calls), 2)

    def test_bm25_is_built_per_corpus_not_shared(self):
        """IDF 가 코퍼스 전체 기준이라 쪼개지 않으면 서로의 점수를 왜곡한다."""
        service = build(dense=False)
        law_bm25 = service._retrievers["법령"].members[0].retriever
        case_bm25 = service._retrievers["판례"].members[0].retriever
        # 색인은 doc_type 으로만 가른다. 폐지 청크도 법령 색인에는 들어 있고,
        # 걸러내는 것은 질의 시점의 status 필터다.
        self.assertEqual(len(law_bm25.chunks), 4)
        self.assertEqual(len(case_bm25.chunks), 2)


class CitationTests(unittest.TestCase):
    """출처가 없으면 모델이 "관련 법에 따르면" 이라고 쓴다."""

    def test_case_citation_carries_court_and_number(self):
        text = citation_of(CHUNKS[3]["metadata"])
        self.assertIn("대법원", text)
        self.assertIn("2011다49523", text)
        self.assertIn("2013-01-17", text)

    def test_law_citation_carries_article_number(self):
        text = citation_of(CHUNKS[0]["metadata"])
        self.assertIn("주택임대차보호법", text)
        self.assertIn("제3조", text)

    def test_evidence_keeps_text_and_source_url(self):
        result = build().search("대항력", k_law=1, k_case=0)
        evidence = result.laws[0]
        self.assertTrue(evidence.text)
        self.assertTrue(evidence.source_url)
        self.assertEqual(evidence.rank, 1)


class PromptContextTests(unittest.TestCase):
    def test_context_labels_the_two_kinds(self):
        """판례를 조문과 같은 무게로 읽으면 안 되므로 구분을 유지한다."""
        context = build().search("집주인이 바뀌면", k_law=2, k_case=2).as_prompt_context()
        self.assertIn("## 관련 법령", context)
        self.assertIn("## 관련 판례", context)
        self.assertLess(context.index("## 관련 법령"), context.index("## 관련 판례"))

    def test_context_omits_an_empty_section(self):
        context = build().search("대항력", k_law=2, k_case=0).as_prompt_context()
        self.assertNotIn("## 관련 판례", context)

    def test_blocks_carry_the_citation(self):
        context = build().search("집주인이 바뀌면", k_law=0, k_case=1).as_prompt_context()
        self.assertIn("2011다49523", context)


class DegradedInputTests(unittest.TestCase):
    """데이터가 덜 갖춰진 상태에서도 나머지로 돌아가야 한다."""

    def test_works_without_a_dense_retriever(self):
        """인덱스가 아직 없어도 어휘 검색만으로 앱을 띄울 수 있어야 한다."""
        result = build(dense=False).search("대항력", k_law=3, k_case=3)
        self.assertTrue(result.laws)
        self.assertTrue(result.cases)

    def test_missing_case_corpus_still_returns_laws(self):
        laws_only = split_by_type(CHUNKS, LAW.doc_types)
        result = RetrievalService(laws_only, FakeDense(laws_only)).search("대항력")
        self.assertTrue(result.laws)
        self.assertEqual(result.cases, [])
        self.assertFalse(result.is_empty())

    def test_empty_corpus_returns_empty_result(self):
        result = RetrievalService([], None).search("대항력")
        self.assertTrue(result.is_empty())
        self.assertEqual(result.as_prompt_context(), "")


class ConfigTests(unittest.TestCase):
    def test_corpora_keep_the_same_unmodified_base_parameters(self):
        """법령 RRF·확장 외의 기존 BM25 설정은 묶음별로 달라지지 않는다."""
        self.assertEqual(LAW.bm25_b, CASE.bm25_b)
        self.assertEqual(LAW.expand_weight, CASE.expand_weight)

    def test_tuning_one_corpus_does_not_touch_the_other(self):
        tuned = Corpus("법령", LAW.doc_types, bm25_b=0.25, rrf_k=7)
        service = RetrievalService(CHUNKS, None, law=tuned)
        self.assertEqual(service._retrievers["법령"].members[0].retriever.b, 0.25)
        self.assertEqual(service._retrievers["법령"].rrf_k, 7)
        self.assertEqual(service._retrievers["판례"].members[0].retriever.b, CASE.bm25_b)
        self.assertEqual(service._retrievers["판례"].rrf_k, DEFAULT_RRF_K)

    def test_law_rrf_tuning_does_not_change_case_or_guide(self):
        service = build()
        self.assertEqual(LAW.rrf_k, LAW_RRF_K)
        self.assertEqual(service._retrievers["법령"].rrf_k, 5)
        self.assertEqual(service._retrievers["판례"].rrf_k, DEFAULT_RRF_K)
        self.assertEqual(service._retrievers["안내"].rrf_k, DEFAULT_RRF_K)

    def test_law_context_expansion_does_not_reach_case_or_guide(self):
        service = build()
        law_bm25 = service._retrievers["법령"].members[0].retriever
        case_bm25 = service._retrievers["판례"].members[0].retriever
        guide_bm25 = service._retrievers["안내"].members[0].retriever

        self.assertIs(law_bm25.query_expander, expand_law)
        self.assertIs(case_bm25.query_expander, expand)
        self.assertIs(guide_bm25.query_expander, expand)


if __name__ == "__main__":
    unittest.main()


class RoutingTests(unittest.TestCase):
    """전세ON 은 주택 서비스다. 기본 범위가 주택이어야 한다.

    코퍼스의 법령 133청크 중 57청크(43%)가 상가 법령이라, 빼지 않으면 주택
    질문에서 상가 조문이 상위를 차지한다. 실제로 "집주인이 바뀌면" 질문에
    상가건물 임대차보호법 제5조가 1위로 올라왔다.
    """

    HOUSING = [
        "전세 사는 중에 집주인이 바뀌면 보증금은 어떻게 되나요?",
        "대항력은 언제부터 생기나요?",
        "묵시적 갱신이면 기간이 얼마가 되나요?",
        "월세로 전환할 때 상한이 있나요?",
    ]
    COMMERCIAL = [
        "상가 임대차도 계약갱신요구권이 있나요?",
        "점포를 넘길 때 권리금은 어떻게 되나요?",
        "환산보증금을 넘으면 어떤 차이가 있나요?",
        "사무실 임대차도 같은 법이 적용되나요?",
    ]

    def test_housing_questions_exclude_commercial_laws(self):
        for question in self.HOUSING:
            with self.subTest(question=question):
                self.assertEqual(
                    route_law_corpus(question).exclude_titles,
                    COMMERCIAL_LAWS + (CIVIL_TITLE,),
                )

    def test_commercial_questions_keep_commercial_laws(self):
        for question in self.COMMERCIAL:
            with self.subTest(question=question):
                self.assertEqual(
                    route_law_corpus(question).exclude_titles, (CIVIL_TITLE,)
                )

    def test_commercial_signal_widens_instead_of_switching(self):
        """상가로 바꾸는 것이 아니라 제외를 푸는 것이다.

        "상가주택"처럼 둘 다 걸린 질문에서 주택 조문이 사라지면 안 된다.
        """
        corpus = route_law_corpus("상가주택인데 주택 부분만 전세로 살고 있어요")
        self.assertEqual(corpus.exclude_titles, (CIVIL_TITLE,))
        self.assertEqual(corpus.doc_types, LAW.doc_types)

    def test_routing_keeps_the_tuned_parameters(self):
        """범위만 바꾸고 파라미터는 건드리지 않는다."""
        tuned = Corpus("법령", LAW.doc_types, bm25_b=0.25)
        self.assertEqual(route_law_corpus("대항력", tuned).bm25_b, 0.25)


class WhereClauseTests(unittest.TestCase):
    """Chroma 는 조건이 둘 이상이면 $and 를 요구한다."""

    def test_single_condition_stays_flat(self):
        """조건이 하나면 $and 로 감싸지 않는다."""
        plain = Corpus("법령", LAW.doc_types, status="")
        self.assertEqual(plain.where(), {"doc_type": {"$in": list(LAW.doc_types)}})

    def test_conditions_are_wrapped_in_and(self):
        where = route_law_corpus("대항력은 언제 생기나요?").where()
        self.assertIn("$and", where)
        # doc_type + status + 상가 제외
        self.assertEqual(len(where["$and"]), 3)

    def test_memory_filter_understands_the_same_clause(self):
        """같은 필터를 BM25 와 Chroma 에 그대로 넘길 수 있어야 한다."""
        where = route_law_corpus("대항력은 언제 생기나요?").where()
        housing = {"doc_type": "law", "title": "주택임대차보호법", "status": "current"}
        commercial = {"doc_type": "law", "title": COMMERCIAL_LAWS[0], "status": "current"}
        repealed = {**housing, "status": "repealed"}
        self.assertTrue(matches(housing, where))
        self.assertFalse(matches(commercial, where))
        self.assertFalse(matches(repealed, where))


class CivilRoutingTests(unittest.TestCase):
    """민법은 의도가 확인될 때만 0~2건, 전체 법령 3칸 안에서 나온다."""

    def service(self, dense: bool = True) -> RetrievalService:
        chunks = CHUNKS + CIVIL_CHUNKS
        return RetrievalService(chunks, FakeDense(chunks) if dense else None)

    def test_candidate_ids_are_locked_to_the_reviewed_seven_articles(self):
        self.assertEqual(
            set(CIVIL_ARTICLE_IDS),
            {f"민법-제{number}조" for number in (623, 626, 627, 629, 632, 634, 640)},
        )

    def test_unrelated_question_gets_no_civil_article(self):
        question = "전입신고를 늦게 하면 어떤 문제가 생기나요?"
        self.assertEqual(detect_civil_topics(question), ())
        result = self.service().search(question, k_law=3, k_case=0)
        self.assertFalse(any(e.citation.startswith("민법") for e in result.laws))
        self.assertEqual(result.civil_topics, ())

    def test_repair_question_uses_one_of_three_law_slots(self):
        result = self.service().search(
            "보일러가 고장 났는데 집주인이 고쳐주는 게 맞나요?",
            k_law=3,
            k_case=0,
        )
        self.assertEqual(len(result.laws), 3)
        self.assertEqual(result.laws[0].citation.split()[0], "민법")
        self.assertIn("제623조", result.laws[0].citation)
        self.assertEqual(result.civil_topics, ("수선의무",))

    def test_two_distinct_repair_grounds_use_at_most_two_slots(self):
        result = self.service().search(
            "보일러가 고장 나서 제 돈으로 먼저 수리비를 냈는데 돌려받을 수 있나요?",
            k_law=3,
            k_case=0,
        )
        civil = [e for e in result.laws if e.citation.startswith("민법")]
        self.assertEqual(len(result.laws), 3)
        self.assertEqual(len(civil), 2)
        self.assertTrue(any("제623조" in e.citation for e in civil))
        self.assertTrue(any("제626조" in e.citation for e in civil))

    def test_arrears_termination_routes_to_article_640(self):
        result = self.service().search(
            "월세를 두 달 밀렸다고 바로 나가라고 합니다.", k_law=3, k_case=0
        )
        self.assertIn("제640조", result.laws[0].citation)

    def test_arrears_renewal_question_stays_in_housing_law(self):
        question = "월세를 두 달 밀렸는데 다음 계약 갱신을 거절할 수 있나요?"
        self.assertEqual(detect_civil_topics(question), ())
        result = self.service().search(question, k_law=3, k_case=0)
        self.assertFalse(any(e.citation.startswith("민법") for e in result.laws))

    def test_sublet_routes_to_article_629(self):
        result = self.service().search(
            "집주인 허락 없이 방 하나를 친구한테 빌려줘도 되나요?",
            k_law=3,
            k_case=0,
        )
        self.assertIn("제629조", result.laws[0].citation)

    def test_natural_reimbursement_wording_routes_to_article_626(self):
        result = self.service().search(
            "싱크대가 막혀서 제 돈으로 사람을 불러 고쳤는데 비용을 받을 수 있나요?",
            k_law=3,
            k_case=0,
        )
        self.assertTrue(any("제626조" in e.citation for e in result.laws[:2]))

    def test_natural_sublet_wording_routes_to_article_629(self):
        result = self.service().search(
            "투룸에 친구를 들여 방 하나만 쓰게 하고 돈을 조금 받아도 되나요?",
            k_law=3,
            k_case=0,
        )
        self.assertIn("제629조", result.laws[0].citation)

    def test_unusable_room_wording_routes_to_article_627(self):
        result = self.service().search(
            "누수 때문에 방을 쓰지 못하면 월세를 줄일 수 있나요?",
            k_law=3,
            k_case=0,
        )
        self.assertIn("제627조", result.laws[0].citation)

    def test_repair_notice_wording_routes_to_article_634(self):
        result = self.service().search(
            "집에 수리가 필요하면 집주인에게 알려야 하나요?",
            k_law=3,
            k_case=0,
        )
        self.assertIn("제634조", result.laws[0].citation)

    def test_zero_law_limit_also_disables_civil_search(self):
        result = self.service().search("보일러가 고장 났어요", k_law=0, k_case=0)
        self.assertEqual(result.laws, [])
        self.assertEqual(result.civil_topics, ())

    def test_bm25_indexes_are_physically_separated(self):
        service = self.service(dense=False)
        standard = service._retrievers["법령"].members[0].retriever.chunks
        civil = service._retrievers[CIVIL.name].members[0].retriever.chunks
        self.assertFalse(any(c["metadata"]["title"] == CIVIL_TITLE for c in standard))
        self.assertEqual(len(civil), 7)

    def test_civil_uses_separate_dense_backend(self):
        standard_dense = FakeDense(CHUNKS)
        civil_dense = FakeDense(CIVIL_CHUNKS)
        service = RetrievalService(CHUNKS + CIVIL_CHUNKS, standard_dense, civil_dense=civil_dense)
        self.assertIs(service._retrievers[CIVIL.name].members[1].retriever, civil_dense)
        self.assertIs(service._retrievers[LAW.name].members[1].retriever, standard_dense)
        self.assertIn("제623조", service.search("보일러가 고장 났어요", k_law=3).laws[0].citation)

    def test_or_clause_is_supported_too(self):
        where = {"$or": [{"doc_type": "law"}, {"doc_type": "case"}]}
        self.assertTrue(matches({"doc_type": "case"}, where))
        self.assertFalse(matches({"doc_type": "guide"}, where))


# 아래 문항은 3차 PATCH-033에서 작성한 공개 회귀 문항이다. 4차 독립 평가가 아니다.
# 3차 기록에 따르면 신호어 표를 보지 않고 먼저 썼다. 표에 있는 문구를 그대로 옮겨
# 문항을 만들면 표를 표로 검증하는 셈이라, 통과해도 실제 사용자 표현에 대한
# 보증이 되지 않는다. 실제로 이 방식으로 제629조의 자연어 표현 누락을 찾았다.
CIVIL_MUST_FIRE: tuple[tuple[str, str], ...] = (
    # 전대 — 사이에 말이 끼거나 어미가 달라도 잡혀야 한다
    ("민법-제629조", "친구에게 방을 다시 빌려줘도 되나요?"),
    ("민법-제629조", "집주인 허락 없이 다른 사람에게 재임대해도 되나요?"),
    ("민법-제629조", "제가 사는 집 방 하나를 세를 놓아도 되나요?"),
    ("민법-제629조", "계약 기간이 남았는데 원룸을 다른 사람에게 넘겨도 되나요?"),
    # 수선의무 — "고장" 이라고 쓰지 않고 증상만 적는 질문
    ("민법-제623조", "에어컨이 안 나오는데 집주인한테 말하면 되나요?"),
    ("민법-제623조", "보일러가 작동을 안 하는데 누가 고쳐야 하나요?"),
    ("민법-제623조", "싱크대 물이 안 내려가요. 집주인이 해결해줘야 하나요?"),
    ("민법-제623조", "세탁기가 안 돌아가는데 집주인이 바꿔줘야 하나요?"),
)

CIVIL_MUST_STAY_QUIET: tuple[str, ...] = (
    "집주인이 내일 계약하자고 하는데 확정일자는 언제 받나요?",
    "상가 월세가 밀렸는데 계약 해지하겠다고 합니다",
    "전세를 월세로 바꾸자고 하는데 따라야 하나요?",
    "집주인이 바뀌면 계약서를 새로 써야 하나요?",
    "보증금을 다른 사람보다 먼저 돌려받을 수 있나요?",
    "확정일자를 안 받으면 어떻게 되나요?",
    # 설비명·증상 단독으로 수선의무가 발동하면 안 된다
    "확정일자 제도는 어떻게 작동하나요?",
    "전세보증금반환보증은 어떻게 작동하나요?",
    "에어컨이 옵션으로 포함되어 있나요?",
    # 집주인이 남에게 세를 놓는 경우는 전대가 아니라 주임법 제6조의3 문제다
    "집주인이 실제로 살겠다며 내보낸 뒤 다른 사람에게 세를 놓으면 "
    "손해배상을 청구할 수 있나요?",
    "계약 연장을 요구했는데 집주인이 직접 산다고 해서 나왔습니다. "
    "그런데 다른 사람에게 다시 세를 놓으면 어떻게 하나요?",
)


class CivilNaturalWordingTests(unittest.TestCase):
    """3차 공개 문항 및 4차 오발동 회귀 사례로 판정을 잠근다."""

    def test_sublet_includes_small_portion_exception(self):
        chunks = CHUNKS + CIVIL_CHUNKS
        result = RetrievalService(chunks, FakeDense(chunks)).search(
            "제가 사는 집 방 하나를 세를 놓아도 되나요?", k_law=3, k_case=0,
        )
        self.assertIn("제629조", result.laws[0].citation)
        self.assertIn("제632조", result.laws[1].citation)
        self.assertEqual(len(result.laws), 3)

    def test_natural_wording_is_detected(self):
        for article_id, question in CIVIL_MUST_FIRE:
            with self.subTest(question=question):
                found = [t.article_id for t in detect_civil_topics(question)]
                self.assertIn(article_id, found)

    def test_housing_law_questions_stay_quiet(self):
        for question in CIVIL_MUST_STAY_QUIET:
            with self.subTest(question=question):
                self.assertEqual(detect_civil_topics(question), ())

    def test_evaluation_sets_never_trigger_civil_routing(self):
        """dev·holdout 은 전부 주택임대차 질문이다. 하나라도 걸리면 오발동이다."""
        root = Path(__file__).resolve().parents[1]
        for name in ("dev", "holdout"):
            path = root / "data/eval" / f"{name}.jsonl"
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                with self.subTest(qid=item["qid"]):
                    self.assertEqual(detect_civil_topics(item["question"]), ())


class CivilCorpusWarningTests(unittest.TestCase):
    """민법 청크가 없으면 조건부 검색이 조용히 죽는다. 경고로 드러낸다."""

    def test_from_index_rejects_civil_in_standard_collection(self):
        from unittest.mock import Mock
        dense = Mock()
        dense.collection.get.return_value = {"ids": ["civil-id"]}
        with patch.object(service_module, "_load_index_chunks", return_value=CHUNKS), \
             patch.object(service_module, "SentenceTransformerEmbedding"), \
             patch("src.retrieval.dense.ChromaRetriever", return_value=dense):
            with self.assertRaisesRegex(ValueError, "기본 인덱스"):
                RetrievalService.from_index()

    def test_from_index_requires_civil_index_when_chunks_exist(self):
        from unittest.mock import Mock
        dense = Mock()
        dense.collection.get.return_value = {"ids": []}
        with patch.object(service_module, "_load_index_chunks", return_value=CHUNKS + CIVIL_CHUNKS), \
             patch.object(service_module, "SentenceTransformerEmbedding"), \
             patch("src.retrieval.dense.ChromaRetriever", return_value=dense), \
             patch.object(Path, "is_file", return_value=False):
            with self.assertRaisesRegex(ValueError, "별도 민법 인덱스"):
                RetrievalService.from_index()

    def test_missing_civil_chunks_are_reported(self):
        with self.assertLogs("src.retrieval.service", level="WARNING") as captured:
            RetrievalService(CHUNKS, None)
        message = "\n".join(captured.output)
        self.assertIn("민법", message)
        self.assertIn("fetch_minbeop", message)
        self.assertIn("data/chunks/chunks.jsonl", message)
        self.assertNotIn("data/chunks/knowledge_chunks.jsonl", message)

    def test_complete_civil_corpus_is_silent(self):
        chunks = CHUNKS + CIVIL_CHUNKS
        logger = logging.getLogger("src.retrieval.service")
        with patch.object(logger, "warning") as warned:
            RetrievalService(chunks, None)
        warned.assert_not_called()


class PromptBlockTests(unittest.TestCase):
    """청크 159건 전부가 출처 헤더로 시작한다. 앞에 또 붙이면 두 번 들어간다."""

    def test_header_is_not_repeated(self):
        evidence = Evidence(1, "c1", "law", "주택임대차보호법 제3조",
                            "[주택임대차보호법 제3조(대항력 등)]\n임차인이 인도와 주민등록을",
                            0.5)
        block = evidence.as_prompt_block()
        self.assertEqual(block.count("주택임대차보호법 제3조"), 1)
        self.assertTrue(block.startswith("[1] ["))

    def test_citation_is_added_when_the_text_has_no_header(self):
        evidence = Evidence(1, "c1", "law", "주택임대차보호법 제3조",
                            "임차인이 인도와 주민등록을 마친 때", 0.5)
        block = evidence.as_prompt_block()
        self.assertIn("주택임대차보호법 제3조", block)
        self.assertIn("임차인이 인도와", block)

    def test_citation_field_survives_for_the_ui(self):
        """본문에서 헤더를 다시 떼어내지 않아도 되도록 필드는 남긴다."""
        result = build().search("대항력", k_law=1, k_case=0)
        self.assertTrue(result.laws[0].citation)


class StatusFilterTests(unittest.TestCase):
    """폐지된 조문을 근거로 답하면 사용자가 지금 없는 권리를 믿는다.

    청크 규격(docs/chunk-schema.md)도 검색 기본 필터를 {"status": "current"} 로
    정하고 있다. 픽스처에 폐지 청크를 넣어 두지 않으면 필터가 빠져도 알 수 없다.
    """

    def test_repealed_chunks_are_not_returned(self):
        result = build().search("대항력", k_law=5, k_case=5)
        self.assertNotIn("law_old", [e.chunk_id for e in result.laws])

    def test_status_is_part_of_the_filter(self):
        self.assertIn({"status": "current"}, LAW.where()["$and"])
        self.assertIn({"status": "current"}, CASE.where()["$and"])

    def test_routing_keeps_the_status_filter(self):
        """상가 제외가 붙어도 status 조건이 밀려나면 안 된다."""
        conditions = route_law_corpus("대항력은 언제 생기나요?").where()["$and"]
        self.assertIn({"status": "current"}, conditions)

    def test_status_can_be_widened_deliberately(self):
        """옛 조문을 일부러 찾아야 하는 화면이 생기면 설정으로 푼다."""
        historical = Corpus("법령", LAW.doc_types, status="")
        service = RetrievalService(CHUNKS, FakeDense(CHUNKS), law=historical)
        result = service.search("대항력", k_law=5, k_case=0)
        self.assertIn("law_old", [e.chunk_id for e in result.laws])


class BlankQueryTests(unittest.TestCase):
    """BM25 는 토큰이 없어 아무것도 안 내지만 임베딩은 공백도 벡터로 바꾼다.

    그대로 두면 사용자가 엔터만 쳐도 무관한 근거 10건이 LLM 에 넘어간다.
    """

    def test_blank_questions_return_nothing(self):
        for question in ("", " ", "\t\n", "   "):
            with self.subTest(question=repr(question)):
                result = build().search(question)
                self.assertTrue(result.is_empty())
                self.assertEqual(result.as_prompt_context(), "")

    def test_blank_question_does_not_reach_the_retrievers(self):
        dense = FakeDense(CHUNKS)
        RetrievalService(CHUNKS, dense).search("   ")
        self.assertEqual(dense.calls, [])

    def test_a_real_question_still_works(self):
        self.assertFalse(build().search("대항력").is_empty())


class GuideRoutingTests(unittest.TestCase):
    """안내는 질문이 그 주제일 때만 나간다.

    법령·판례처럼 고정 개수로 내보내면 관련 없는 질문에도 항상 따라붙는다.
    안내가 6청크뿐이라 어떤 질문에도 상위 몇 건이 나오기 때문이다.
    """

    UNRELATED = [
        "묵시적 갱신이면 계약 기간이 얼마가 되나요?",
        "계약갱신요구권은 몇 번까지 쓸 수 있나요?",
        "2기 차임을 연체하면 어떻게 되나요?",
        "관리비 체납액은 얼마인가요?",
        "지방세 체납액도 열람할 수 있나요?",
    ]

    def test_unrelated_questions_get_no_guides(self):
        for question in self.UNRELATED:
            with self.subTest(question=question):
                self.assertEqual(detect_guide_topics(question), ())
                self.assertEqual(build().search(question).guides, [])

    def test_one_topic_returns_one_chunk_by_default(self):
        """순위가 2위라는 이유만으로 두 번째를 넣지 않는다."""
        result = build().search("전세보증금반환보증은 어떤 제도인가요?")
        self.assertEqual(len(result.guides), 1)

    def test_second_chunk_joins_only_when_it_carries_query_terms(self):
        result = build().search("보증 신청 절차는 어떻게 되나요?")
        self.assertEqual(len(result.guides), 2)
        self.assertIn("절차", result.guides[1].text)

    def test_two_topics_return_one_chunk_each(self):
        question = "전세보증 가입하려는데 임대인 미납국세도 확인하고 싶어요"
        self.assertEqual(len(detect_guide_topics(question)), 2)
        guides = build().search(question).guides
        self.assertEqual(len(guides), 2)
        self.assertEqual(
            {e.chunk_id.split("#")[0] for e in guides},
            {"guide-HUG-전세보증금반환보증", "guide-국세청-미납국세열람"},
        )
        self.assertEqual([e.rank for e in guides], [1, 2])

    def test_never_more_than_the_limit(self):
        for limit in (0, 1, 2):
            with self.subTest(limit=limit):
                guides = build().search(
                    "전세보증 가입과 미납국세 열람 절차", k_guide=limit
                ).guides
                self.assertLessEqual(len(guides), limit)

    def test_guides_do_not_change_the_law_or_case_counts(self):
        with_guides = build().search("전세보증금반환보증은 어떤 제도인가요?")
        without = build().search("전세보증금반환보증은 어떤 제도인가요?", k_guide=0)
        self.assertEqual(len(with_guides.laws), len(without.laws))
        self.assertEqual(len(with_guides.cases), len(without.cases))


class GuidePresentationTests(unittest.TestCase):
    """안내는 법적 근거가 아니다. 조문과 섞이면 모델이 법조문처럼 인용한다."""

    QUESTION = "전세보증금반환보증은 어떤 제도인가요?"

    def test_guides_come_back_in_their_own_list(self):
        result = build().search(self.QUESTION)
        self.assertTrue(result.guides)
        self.assertTrue(all(e.doc_type in GUIDE.doc_types for e in result.guides))
        self.assertTrue(all(e.doc_type not in GUIDE.doc_types for e in result.laws))

    def test_prompt_tells_the_model_to_ignore_unrelated_guides(self):
        """지시를 근거와 함께 보낸다. 별도 안내로 두면 프롬프트에서 빠뜨린다."""
        context = build().search(self.QUESTION).as_prompt_context()
        self.assertIn("## 참고 안내", context)
        self.assertIn("법적 근거가 아닌", context)
        self.assertIn("관련이 없으면 무시", context)

    def test_guides_come_after_laws_and_cases_in_the_prompt(self):
        context = build().search(self.QUESTION, k_law=1, k_case=1).as_prompt_context()
        self.assertLess(context.index("## 관련 법령"), context.index("## 참고 안내"))
        self.assertLess(context.index("## 관련 판례"), context.index("## 참고 안내"))

    def test_guide_citation_carries_agency_and_topic(self):
        guides = build().search(self.QUESTION).guides
        self.assertIn("HUG", guides[0].citation)
        self.assertIn("전세보증금 반환보증", guides[0].citation)

    def test_zero_k_guide_skips_them(self):
        result = build().search(self.QUESTION, k_guide=0)
        self.assertEqual(result.guides, [])
        self.assertNotIn("## 참고 안내", result.as_prompt_context())

    def test_a_result_with_only_guides_is_not_empty(self):
        result = build().search(self.QUESTION, k_law=0, k_case=0)
        self.assertFalse(result.is_empty())

    def test_interpretations_are_not_lumped_in_with_guides(self):
        """법령해석은 조문의 뜻을 정하는 자료라 실무 안내와 무게가 다르다."""
        self.assertNotIn("interp", GUIDE.doc_types)


class IndexChunkFallbackTests(unittest.TestCase):
    def test_sample_chunks_are_used_when_generated_chunks_are_missing(self):
        sample = Path("/tmp/chunks_expanded.jsonl")
        expected = [law_chunk("law-12", "미등기 전세에도 이 법을 준용합니다.", "제12조")]

        with (
            patch.object(service_module, "SAMPLE_CHUNKS", sample),
            patch.object(service_module, "_load_all", return_value=[]),
            patch.object(service_module, "load_chunks", return_value=expected) as load,
            patch.object(Path, "exists", return_value=True),
        ):
            actual = service_module._load_index_chunks((Path("data/chunks/chunks.jsonl"),))

        self.assertEqual(expected, actual)
        load.assert_called_once_with(sample)
