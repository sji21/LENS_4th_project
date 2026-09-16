"""Independent counterexamples for companion evidence selection."""
from copy import deepcopy

import pytest

from src.retrieval.companion_evidence import LeaseProtectionSelector, requests_lease_protection


QUESTION = "전입신고만 하고 확정일자를 안 받으면 어떻게 되나요?"


def _chunk(cid, number, body, **metadata):
    values = {
        "title": "가상주거법", "doc_type": "law", "status": "current",
        "article_id": "가상주거법-" + number, "article_no": number,
        "article_title": "검증 근거", "version": "법률 제123호",
        "effective_date": "2026-01-01",
    }
    values.update(metadata)
    return {"chunk_id": cid, "doc_id": "virtual-law-20260101", "metadata": values,
            "text": f"[가상주거법 {number}(검증 근거)]\n{body}"}


@pytest.fixture
def candidate_pool():
    opposition = _chunk("opposition", "제17조", "① 임차인이 주택의 인도와 주민등록을 마친 때에는 그 다음 날부터 제삼자에 대하여 효력이 생긴다.")
    priority = _chunk("priority", "제18조", "② 제17조제1항의 대항요건과 임대차계약증서상의 확정일자를 갖춘 임차인은 경매 또는 공매에서 후순위권리자보다 우선하여 보증금을 변제받을 권리가 있다.")
    chunks = [_chunk("top", "제1조", "① 전입신고의 일반 절차를 안내한다."), opposition,
              _chunk("third", "제4조", "① 확정일자의 부여 방법을 정한다."),
              _chunk("fourth", "제5조", "① 신청서에 기재할 사항을 정한다."), priority]
    ranked = [(chunk["chunk_id"], 1.0 / index) for index, chunk in enumerate(chunks, 1)]
    return chunks, ranked


@pytest.mark.parametrize("question", [
    QUESTION,
    "전입신고를 했는데 확정일자가 없으면 보증금은 보호되나요?",
    "대화 주제: 계약갱신\n직전 답변 질문: 월세를 올려도 되나요?\n사용자 입력: " + QUESTION,
    "계약을 체결했습니다.\n사용자 질문: " + QUESTION,
    "확정일자의 법적 효력은 제외하지 말고 알려주세요.",
])
def test_current_effect_question_keeps_first_hit_and_both_body_proven_rights(candidate_pool, question):
    chunks, ranked = candidate_pool
    untouched = deepcopy((chunks, ranked))
    actual = LeaseProtectionSelector(chunks).select(question, ranked, 3)
    assert requests_lease_protection(question)
    assert actual[0] == ranked[0]
    assert {cid for cid, score in actual} == {"top", "opposition", "priority"}
    assert len(actual) == 3
    assert all((cid, score) in ranked for cid, score in actual)
    assert (chunks, ranked) == untouched


@pytest.mark.parametrize("question", [
    "확정일자는 어디서 받고 어떤 서류가 필요한가요?",
    "전입신고와 확정일자 신청 방법을 알려 주세요.",
    "확정일자를 받았는지 옛날 기록을 조회하려면 어떻게 하나요?",
    "확정일자는 안 받았지만 그 효력은 묻지 않습니다. 발급 장소만 알려 주세요.",
    '예문은 "전입신고만 하고 확정일자를 안 받으면 어떻게 되나요?"입니다. 전입신고 신청서 양식은 어디 있나요?',
    "직전 답변 질문: " + QUESTION + "\n사용자 입력: 확정일자 수수료는 얼마인가요?",
    "상가 전입신고만 하고 확정일자를 안 받으면 어떻게 되나요?",
    "확정일자 온라인 신청과 주민센터 신청의 수수료 차이는 얼마인가요?",
    "확정일자 신청 장소와 접수 방법의 차이를 알려주세요.",
])
def test_procedure_quote_history_and_commercial_scope_keep_ranking(candidate_pool, question):
    chunks, ranked = candidate_pool
    assert not requests_lease_protection(question)
    assert LeaseProtectionSelector(chunks).select(question, ranked, 3) == ranked[:3]


@pytest.mark.parametrize("field,value", [
    ("status", "historical"), ("doc_type", "guide"), ("title", "별도법"),
    ("version", "법률 제124호"), ("effective_date", "2026-02-01"),
])
def test_priority_evidence_cannot_cross_source_or_edition(candidate_pool, field, value):
    chunks, ranked = candidate_pool
    chunks[-1]["metadata"][field] = value
    assert LeaseProtectionSelector(chunks).select(QUESTION, ranked, 3) == ranked[:3]


@pytest.mark.parametrize("replacement", [
    "② 확정일자 신청서를 제출하여야 한다.",
    "② 제99조제1항의 대항요건과 임대차계약증서상의 확정일자를 갖춘 임차인은 우선하여 보증금을 변제받을 권리가 있다.",
    "② 제17조제9항의 대항요건과 임대차계약증서상의 확정일자를 갖춘 임차인은 우선하여 보증금을 변제받을 권리가 있다.",
    "② 제17조제1항의 대항요건과 확정일자를 갖추어도 우선하여 보증금을 변제받을 권리가 없다.",
    "② 다른 법률 제17조제1항의 대항요건과 임대차계약증서상의 확정일자를 갖춘 임차인은 우선하여 보증금을 변제받을 권리가 있다.",
])
def test_article_header_or_wrong_reference_cannot_supply_missing_paragraph(candidate_pool, replacement):
    chunks, ranked = candidate_pool
    chunks[-1]["text"] = "[가상주거법 제18조(대항요건 확정일자 우선변제)]\n" + replacement
    assert LeaseProtectionSelector(chunks).select(QUESTION, ranked, 3) == ranked[:3]


@pytest.mark.parametrize("k", [-1, 0, 1, 2])
def test_insufficient_budget_preserves_original_ranking(candidate_pool, k):
    chunks, ranked = candidate_pool
    assert LeaseProtectionSelector(chunks).select(QUESTION, ranked, k) == ranked[:max(0, k)]


def test_required_companion_must_exist_in_this_search_candidate_pool(candidate_pool):
    chunks, ranked = candidate_pool
    assert LeaseProtectionSelector(chunks).select(QUESTION, ranked[:-1], 3) == ranked[:3]


def test_product_selection_reaches_graph_llm_at_default_budget(candidate_pool):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    from src.generation import graph, llm
    from src.retrieval.expanded import CIVIL_IDS, ExpandedLawRetrievalService
    from src.retrieval.service import route_law_corpus

    chunks, ranked = candidate_pool
    corpus = chunks + [_chunk(article, article.split("-", 1)[1], "① 계약 조문.",
                              title="민법", article_id=article) for article in CIVIL_IDS]
    searches, messages = [], []

    class Candidates:
        def search(self, query, k, where):
            searches.append((query, k, where))
            return ranked[:k]

    def capture(value):
        messages.extend(value.to_messages())
        return AIMessage(content="가상주거법 제17조에 따르면 주택의 인도와 주민등록을 마친 그 다음 날부터 효력이 생깁니다.")

    service = ExpandedLawRetrievalService(corpus)
    service._context_law = Candidates()
    answer = graph.answer_question(
        QUESTION, service=service, llm=RunnableLambda(capture),
        auxiliary_llm=llm.get_llm(fake_responses=["PASS"]),
    )
    human = next(message.content for message in messages if message.type == "human")
    assert searches == [(QUESTION, 20, route_law_corpus(QUESTION).where())]
    assert answer.laws[0].chunk_id == "top"
    assert {e.chunk_id for e in answer.laws} == {"top", "opposition", "priority"}
    assert chunks[1]["text"] in human
    assert chunks[-1]["text"] in human
