"""Direct-topic preservation and product contracts for law intent selection."""
from copy import deepcopy

import pytest

from src.retrieval.retrieval_intent import LawIntentSelector, requested_law_intents


def chunk(cid, title, body, law="검증주택법", kind="law"):
    return {
        "chunk_id": cid, "text": f"[{law} 제81조({title})]\n{body}",
        "metadata": {"title": law, "article_id": law + "-제81조", "article_no": "제81조",
                     "article_title": title, "doc_type": kind, "status": "current",
                     "version": "검증판", "effective_date": "2026-01-01"},
    }


def residence():
    return chunk("residence", "거주지의 이동",
                 "① 거주지를 이동하면 신고의무자는 전입한 날부터 일정 기간 이내에 전입신고를 하여야 한다.")


def hits(chunks):
    return [(c["chunk_id"], 1 / (i + 1)) for i, c in enumerate(chunks)]


def test_direct_date_authority_and_resident_effect_survive_new_procedure():
    chunks = [chunk("deemed", "다른 신고의 의제", "① 신고한 것으로 본다."),
              chunk("date-form", "신청서 양식", "① 확정일자 신청서를 제출한다.", kind="rule"),
              chunk("effect", "대항력", "① 주택의 인도와 주민등록을 마치면 제삼자에 대하여 효력이 생긴다."),
              chunk("fee", "수수료", "① 신청 수수료를 낸다.", kind="rule"),
              chunk("date-law", "확정일자 부여 및 정보제공", "① 확정일자를 부여한다."),
              residence()]
    original = deepcopy(chunks)
    ranked = hits(chunks)
    selector = LawIntentSelector(chunks)
    selected = selector.select("전입신고와 확정일자는 언제 해야 하나요?", ranked, 5)
    assert {cid for cid, _ in selected} == {"deemed", "date-form", "effect", "date-law", "residence"}
    assert selected == [hit for hit in ranked if hit[0] != "fee"]
    assert chunks == original


def test_direct_filing_authority_and_filing_method_have_priority_over_deeming():
    chunks = [chunk("deemed", "주택 임대차 계약 신고의 의제", "① 신고한 것으로 본다."),
              chunk("method", "주택 임대차 계약의 신고", "① 신고서를 제출하여야 한다.", kind="rule"),
              chunk("authority", "주택 임대차 계약의 신고", "① 계약을 신고하여야 한다."), residence()]
    ranked = hits(chunks)
    result = LawIntentSelector(chunks).select("임대차 신고와 전입신고는 언제 하는 절차인가요?", ranked, 3)
    assert result == ranked[1:]


def test_more_direct_topics_than_budget_keep_original_candidates():
    chunks = [chunk("opposition", "대항력", "① 주택의 인도와 주민등록을 마치면 제삼자에 대하여 효력이 생긴다."),
              chunk("method", "주택 임대차 계약의 신고", "① 신고서를 제출한다.", kind="rule"),
              chunk("authority", "주택 임대차 계약의 신고", "① 계약을 신고한다."), residence()]
    ranked = hits(chunks)
    query = "임대차 신고와 전입신고는 언제 하는 절차인가요?"
    assert LawIntentSelector(chunks).select(query, ranked, 3) == ranked[:3]


@pytest.mark.parametrize("query", [
    "등록민간임대에서 재계약하면서 보증금을 올린다는데 인상 한도가 얼마인가요?",
    "등록민간임대에서 갱신하면서 월세를 두 배 올린다고 해요. 일반 월세와 다른가요?",
    "전입신고는 이미 했고 확정일자 효력이 언제 생기는지 궁금해요.",
])
def test_a_background_event_does_not_become_a_separate_requested_topic(query):
    assert requested_law_intents(query) == ()


@pytest.mark.parametrize("query, expected", [
    ("등록민간임대에서 재계약하면서 올린 임대료 인상 제한과 변경 신고에 필요한 서류를 알려주세요.",
     {"private_report", "private_form"}),
    ("등록민간임대 계약 신고와 재계약 서류는 모두 제외하고 보증금 반환 절차만 알려주세요.", set()),
    ("전입신고 방법은 묻지 않습니다. 주민등록은 완료했습니다. 계약 해지 절차만 알려주세요.", set()),
    ("등록민간임대 재계약 조건은 알려주세요, 계약 신고와 서류는 모두 제외합니다.",
     {"private_renewal"}),
])
def test_review_reported_intent_scope_regressions(query, expected):
    assert set(requested_law_intents(query)) == expected


@pytest.mark.parametrize("k", [0, 1, 2])
def test_small_budgets_preserve_original_ranking(k):
    chunks = [chunk("first", "다른 규정", "① 계약한다."), residence()]
    ranked = hits(chunks)
    assert LawIntentSelector(chunks).select("전입신고는 언제 하나요?", ranked, k) == ranked[:k]


@pytest.fixture
def official_lease_corpus():
    import hashlib
    import json
    from pathlib import Path

    from test_patch051_generation_delivery import _official_snapshot_chunks

    root = Path(__file__).resolve().parents[1] / "data/eval/patch027-full"
    path = root / "capture/new-chunks.json"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["capture/new-chunks.json"]
    corpus = {c["metadata"]["article_id"]: c for c in _official_snapshot_chunks()}
    corpus.update({c["metadata"]["article_id"]: c for c in json.loads(path.read_text(encoding="utf-8"))})
    return corpus


def test_official_private_lease_bodies_reach_native_generation_request(monkeypatch, official_lease_corpus):
    """Use real lexical retrieval and serialization, with only HTTP mocked."""
    from test_patch051_generation_delivery import _capture_native_requests
    from src.generation import graph, llm
    from src.retrieval.expanded import ExpandedLawRetrievalService

    corpus = official_lease_corpus
    chunks = list(corpus.values())
    service = ExpandedLawRetrievalService(chunks)
    payloads = _capture_native_requests(monkeypatch)
    question = "등록민간임대주택에서 계약 신고와 재계약을 하려면 어떤 서류와 절차가 필요한가요?"
    answer = graph.answer_question(
        question, service=service, llm=llm.get_llm(), response_style="standard",
        auxiliary_llm=llm.get_llm(fake_responses=["PASS"]),
    )
    by_id = {c["chunk_id"]: c for c in chunks}
    returned = {by_id[e.chunk_id]["metadata"]["article_id"]: e for e in answer.laws}
    expected = {"민간임대주택에 관한 특별법-제" + str(number) + "조" for number in (45, 46, 47)}
    assert set(returned) == expected
    assert len(payloads) == 1
    human = next(m["content"] for m in payloads[0]["messages"] if m["role"] == "user")
    for article in expected:
        assert returned[article].text == corpus[article]["text"]
        assert returned[article].text in human
    assert "재계약을 거절할 수 없다" in human
    assert "신고 또는 변경신고를 하여야 한다" in human
    assert "표준임대차계약서를 사용하여야 한다" in human


def test_actual_bm25_retains_rent_rule_when_renewal_is_background(official_lease_corpus):
    from src.retrieval.expanded import ExpandedLawRetrievalService
    from src.retrieval.service import route_law_corpus

    service = ExpandedLawRetrievalService(list(official_lease_corpus.values()))
    query = "등록민간임대에서 재계약하면서 올린 임대료 인상 제한과 변경 신고에 필요한 서류를 알려주세요."
    corpus = route_law_corpus(query)
    before = service._context_law.search(query, 3, corpus.where())
    actual = service._search_one(corpus, query, 3)
    expected = {"민간임대주택에 관한 특별법-제" + str(number) + "조" for number in (44, 46, 47)}
    assert {service._chunks[cid]["metadata"]["article_id"] for cid, _ in before} == expected
    assert [(e.chunk_id, e.score) for e in actual] == [(cid, round(score, 4)) for cid, score in before]
