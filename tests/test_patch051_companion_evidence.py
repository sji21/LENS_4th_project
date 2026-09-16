"""Companion selection contracts, independent of evaluation row identities."""
from copy import deepcopy

import pytest

from src.retrieval.companion_evidence import (
    LeaseProtectionSelector, requests_lease_protection,
)
from src.retrieval.expanded import ExpandedLawRetrievalService
from src.retrieval.retriever import load_chunks
from src.retrieval.service import route_law_corpus


@pytest.mark.parametrize("question", [
    "전입신고만 하고 확정일자를 안 받으면 어떻게 되나요?",
    "전입 신고를 마쳤는데 확정 일자가 없으면 보증금이 보호되나요？",
    "확정일자 없이 살면 우선변제권이 생기나요?",
    "전입신고와 확정일자의 효력은 어떤 차이가 있나요?",
    "확정일자만 받았는데 대항력도 생기나요?",
    "확정일자를 받지 않아도 보증금을 먼저 돌려받을 권리가 있나요?",
    "전입신고만 하면 보증금을 보호받을 수 있나요?",
    "주민등록만 해 두면 보증금은 안전한가요?",
    "확정일자 신청 방법과 대항력의 차이도 알려 주세요.",
    "확정일자를 받으면 임차인의 권리가 보호되나요?",
    "확정일자를 받으면 임차인의 법적 권리가 생기나요?",
    "확정일자를 받으면 세입자에게 어떤 권리가 생기나요?",
    "확정일자를 받으면 보증금을 제대로 보호받을 수 있나요?",
    "확정일자가 없으면 보증금도 보호되나요?",
    "확정일자를 받으면 보증금은 얼마나 안전한가요?",
    "확정일자가 있으면 보호받는 보증금의 범위가 달라지나요?",
    "전입신고만 하면 보증금을 충분히 보호받을 수 있나요?",
    "확정일자를 안 받으면 어떤 불이익이 있나요?",
    "확정일자를 안 받으면 보증금에 어떤 위험이 있나요?",
    "확정일자 신청 방법과 보증금이 보호되는지도 알려 주세요.",
    "확정일자 발급 절차와 세입자의 권리가 생기는지도 알려 주세요.",
    "확정일자의 법적 효력을 알려 주세요.",
    "전입신고의 효력을 알려 주세요.",
    "확정일자를 받으면 효력이 생기는지 알려 주세요.",
    "확정일자 신청 방법을 알려 주세요. 보증금은 보호되나요?",
    "확정일자 발급 절차와 임차인의 권리가 생기나요?",
    "확정일자를 받았는데 효력이 언제 생기나요?",
    "확정일자는 어떤 효력이 있나요?",
])
def test_protection_concepts_recognize_requirement_and_effect_questions(question):
    assert requests_lease_protection(question)


@pytest.mark.parametrize("question", [
    "확정일자 신청은 어디서 하나요?",
    "전입신고를 했다가 잠시 다른 곳으로 옮기면 보증금 보호 효력이 사라지나요?",
    "주택임대차보호법상 확정일자는 어디서 받나요?",
    "주택임대차보호법 확정일자 수수료는 얼마인가요?",
    "예전에 확정일자를 받으면 보증금이 보호된다고 들었습니다. 지금은 발급 장소만 알려주세요.",
    "확정일자가 없어서 다시 발급받는 방법을 알려 주세요.",
    "확정일자를 받았는지 기록을 확인하고 싶어요.",
    "전입신고와 확정일자 수수료가 어떻게 되나요?",
    "확정일자 효력은 묻지 않고 전입신고 기한만 알려 주세요.",
    "확정일자는 안 받았습니다. 보일러 수리는 어떻게 하나요?",
    "상가에서 확정일자를 안 받으면 보증금이 보호되나요?",
    "집주인 미납국세 조회 방법이 궁금해요.",
    "확정일자 신청과 등기부 권리관계 조회 방법을 알려 주세요.",
    "확정일자 신청과 임대차계약서 권리관계 조회 방법을 알려 주세요.",
    "확정일자 신청과 임대차계약서 개인정보 보호 방법을 알려 주세요.",
    "확정일자 신청과 임차인 개인정보 보호 방법을 알려 주세요.",
    "확정일자 신청과 임차인의 개인정보 열람 권리 조회 방법을 알려 주세요.",
    "확정일자 신청과 임차인 권리관계 조회 방법을 알려 주세요.",
    "확정일자 신청과 보증금, 등기부 권리관계 조회 방법을 알려 주세요.",
    "확정일자를 신청한 임차인의 권리로 계약서 열람이 가능한가요?",
    "확정일자 신청과 건물 위험 현황 조회 방법을 알려 주세요.",
    "확정일자 신청과 신용상 불이익 조회 방법을 알려 주세요.",
    "확정일자 신청과 보증금 보호 방법을 알려 주세요.",
    "확정일자 신청과 전자계약 효력 조회 방법을 알려 주세요.",
    "확정일자 신청과 다른 계약의 효력 조회 방법을 알려 주세요.",
    "이전 대화: 확정일자 없이도 보증금이 보호되나요?",
    "이전 대화: 확정일자 없이도 보증금이 보호되나요?\n사용자 질문: 수수료는요?",
    "직전 답변 질문: 확정일자 없이도 보증금이 보호되나요?\n사용자 입력: 전입신고 기한은요?",
    "사용자 입력: 확정일자 없이도 보증금이 보호되나요?\n사용자 입력: 수수료는요?",
    '예문은 “확정일자가 없으면 보증금이 보호되나요?”입니다. 신청 방법만 알려 주세요.',
    "예문은 ‘확정일자가 없으면 보증금이 보호되나요?’입니다. 신청 방법만 알려 주세요.",
])
def test_non_effect_requests_do_not_activate_companion_selection(question):
    assert not requests_lease_protection(question)


def sample_service():
    chunks = load_chunks("data/sample/chunks_expanded.jsonl")
    # The legacy sample omits the version field; use a declared test edition
    # only in this fixture. Production must still reject unverified editions.
    for chunk in chunks:
        chunk["metadata"]["version"] = "test-only sample edition"
    return ExpandedLawRetrievalService(chunks)


def test_real_sample_body_pair_survives_three_and_five_law_budgets():
    service = sample_service()
    question = "전입신고만 하고 확정일자를 안 받으면 어떻게 되나요?"
    corpus = route_law_corpus(question)
    raw = service._context_law.search(question, 20, corpus.where())
    original = deepcopy(raw)
    for k in (3, 5):
        actual = service._search_one(corpus, question, k)
        ids = [service._chunks[row.chunk_id]["metadata"]["article_id"] for row in actual]
        assert "주택임대차보호법-제3조" in ids[:3]
        assert "주택임대차보호법-제3조의2" in ids[:3]
        assert actual[0].chunk_id == raw[0][0]
        assert len(actual) == k
        assert all((row.chunk_id, row.score) in [(cid, round(score, 4)) for cid, score in raw]
                   for row in actual)
    assert raw == original


def test_selector_cannot_recover_body_outside_supplied_candidates():
    service = sample_service()
    selector = LeaseProtectionSelector(list(service._chunks.values()))
    question = "전입신고만 하고 확정일자를 안 받으면 어떻게 되나요?"
    raw = service._context_law.search(question, 3, route_law_corpus(question).where())
    assert selector.select(question, raw, 3) == raw


def test_where_filter_is_respected_for_both_members_of_pair():
    service = sample_service()
    selector = LeaseProtectionSelector(list(service._chunks.values()))
    question = "전입신고만 하고 확정일자를 안 받으면 어떻게 되나요?"
    raw = service._context_law.search(question, 20, route_law_corpus(question).where())
    where = {"article_id": {"$ne": "주택임대차보호법-제3조의2"}}
    actual = selector.select(question, raw, 3, where)
    assert all(service._chunks[cid]["metadata"]["article_id"] != "주택임대차보호법-제3조의2"
               for cid, _ in actual)


def test_inactive_query_preserves_all_service_law_results():
    service = sample_service()
    question = "확정일자 신청 방법과 수수료를 알려 주세요."
    corpus = route_law_corpus(question)
    raw = service._context_law.search(question, 5, corpus.where())
    actual = service._search_one(corpus, question, 5)
    assert [(row.chunk_id, row.score) for row in actual] == [(cid, round(score, 4)) for cid, score in raw]


@pytest.mark.parametrize("question", [
    "확정일자 신청과 등기부 권리관계 조회 방법을 알려 주세요.",
    "확정일자 신청과 임대차계약서 권리관계 조회 방법을 알려 주세요.",
    "확정일자 신청과 임차인 개인정보 보호 방법을 알려 주세요.",
    "확정일자 신청과 임차인의 개인정보 열람 권리 조회 방법을 알려 주세요.",
    "확정일자 신청과 임차인 권리관계 조회 방법을 알려 주세요.",
    "확정일자 신청과 보증금, 등기부 권리관계 조회 방법을 알려 주세요.",
    "확정일자를 신청한 임차인의 권리로 계약서 열람이 가능한가요?",
    "확정일자 신청과 건물 위험 현황 조회 방법을 알려 주세요.",
    "확정일자 신청과 신용상 불이익 조회 방법을 알려 주세요.",
    "확정일자 신청과 보증금 보호 방법을 알려 주세요.",
    "확정일자 신청과 전자계약 효력 조회 방법을 알려 주세요.",
    "확정일자 신청과 다른 계약의 효력 조회 방법을 알려 주세요.",
])
def test_registry_rights_procedure_does_not_expand_product_candidate_pool(question):
    service = sample_service()
    corpus = route_law_corpus(question)
    where = corpus.where()
    raw = service._context_law.search(question, 3, where)
    calls = []

    class RecordingCandidates:
        def search(self, query, k, filter_where):
            calls.append((query, k, filter_where))
            return raw[:k]

    service._context_law = RecordingCandidates()
    actual = service._search_one(corpus, question, 3)

    assert calls == [(question, 3, where)]
    assert [(row.chunk_id, row.score) for row in actual] == [
        (cid, round(score, 4)) for cid, score in raw
    ]


@pytest.mark.parametrize("question", [
    "확정일자를 받으면 보증금을 제대로 보호받을 수 있나요?",
    "확정일자가 없으면 보증금도 보호되나요?",
    "확정일자를 받으면 보증금은 얼마나 안전한가요?",
    "확정일자가 있으면 보호받는 보증금의 범위가 달라지나요?",
    "전입신고만 하면 보증금을 충분히 보호받을 수 있나요?",
    "확정일자를 안 받으면 어떤 불이익이 있나요?",
    "확정일자를 안 받으면 보증금에 어떤 위험이 있나요?",
    "확정일자 신청 방법과 보증금이 보호되는지도 알려 주세요.",
    "확정일자 발급 절차와 세입자의 권리가 생기는지도 알려 주세요.",
    "확정일자의 법적 효력을 알려 주세요.",
    "전입신고의 효력을 알려 주세요.",
    "확정일자를 받으면 효력이 생기는지 알려 주세요.",
    "확정일자 신청 방법을 알려 주세요. 보증금은 보호되나요?",
    "확정일자 발급 절차와 임차인의 권리가 생기나요?",
    "확정일자를 받았는데 효력이 언제 생기나요?",
    "확정일자는 어떤 효력이 있나요?",
])
def test_protection_questions_expand_product_candidate_pool(question):
    service = sample_service()
    corpus = route_law_corpus(question)
    where = corpus.where()
    raw = service._context_law.search(question, 20, where)
    calls = []

    class RecordingCandidates:
        def search(self, query, k, filter_where):
            calls.append((query, k, filter_where))
            return raw[:k]

    service._context_law = RecordingCandidates()
    service._search_one(corpus, question, 3)

    assert calls == [(question, 20, where)]
