"""Independent intent and budget checks for the general-law retrieval change.

Synthetic text checks routing/selection behavior, not legal correctness or KURE
quality. The fixed DEV evaluation and its original answers remain separate.
"""
from copy import deepcopy
from dataclasses import replace

import pytest

from chat.dialogue_contract import Decision
from chat.dialogue_query import grounded_query
from chat.dialogue_state import apply_user_update
from src.retrieval.multi_evidence import (
    TaxLookupSelector, requested_tax_scopes, tax_lookup_match, tax_lookup_scopes,
)
from src.retrieval.service import route_law_corpus


@pytest.mark.parametrize("question, expected", [
    ("집주인이 체납한 세금을 계약 전에 조회할 수 있나요?", ("national", "local")),
    ("미납국세를 열람하려면 어떻게 신청하나요?", ("national",)),
    ("미납지방세 열람에 필요한 서류를 알려 주세요.", ("local",)),
    ("국세와 지방세 체납을 함께 확인하려면 어떻게 하나요?", ("national", "local")),
    ("임대인이 동의하지 않아도 미납국세를 열람할 수 있나요?", ("national",)),
    ("미납국세는 임대인 동의가 필요 없어도 열람 방법을 알려 주세요.", ("national",)),
    ("미납국세는 동의가 필요 없을 때 어떻게 열람하나요?", ("national",)),
    ("미납국세만 조회하고 미납지방세는 묻지 않습니다.", ("national",)),
    ("미납국세 조회가 아니고 미납지방세 열람 방법을 알려 주세요.", ("local",)),
    ("국세와 지방세 체납을 조회하고 싶지만 국세는 제외해 주세요.", ("local",)),
    ("국세와 지방세 체납 조회 방법을 알려 주세요. 지방세는 제외해 주세요.", ("national",)),
    ("국세는 제외하고 지방세 체납을 조회하려면 어떻게 하나요?", ("local",)),
    ("국세와 지방세 체납 조회 방법을 알려 주세요. 둘 다 제외해 주세요.", ()),
    ("국세와 지방세 체납 조회 방법을 알려 주세요. 다만 국세와 지방세는 제외해 주세요.", ()),
    ('예문은 "국세는 제외해 주세요"입니다. 미납국세 열람 방법은요?', ("national",)),
    ("이전 대화: 국세는 제외해 주세요.\n사용자 질문: 미납국세 열람 방법은요?", ("national",)),
])
def test_lookup_scope_follows_current_request(question, expected):
    assert requested_tax_scopes(question) == expected


@pytest.mark.parametrize("question", [
    "월세를 체납하면 계약이 해지되나요?",
    "임차인의 월세 체납 내역을 조회할 수 있나요?",
    "임대소득 세금은 어떻게 계산하나요?",
    "주택 세금 납부 방법을 알려 주세요.",
    "국세 환급 내역을 조회할 수 있나요?",
    "지방세 납부확인서를 어디서 발급하나요?",
    "임대인의 국세 체납 사실을 증명할 납세증명서는 어디서 발급받나요?",
    "임대인의 지방세 체납 여부를 확인할 납세증명서 발급 방법은요?",
    "임대인의 국세 체납 여부를 조회하려고 납세증명서를 발급받고 싶어요.",
    "전입신고 기한은 언제까지인가요?",
    "체납세금 조회는 묻지 않습니다. 중개보수를 알려 주세요.",
    "체납세금을 조회하지 않으려 합니다. 수리 비용이 궁금해요.",
    '예문은 "미납국세를 열람할 수 있나요?"입니다. 저는 중개보수만 궁금합니다.',
    "예문은 ‘미납국세 열람 방법’입니다. 저는 중개보수만 궁금합니다.",
    "이전 대화: 미납국세를 열람하고 싶어요.\n사용자 질문: 전입신고 기한은요?",
    "이전 대화: 집주인 체납세금을 조회하고 싶어요.\n사용자 추가 상황: 아직 전입하지 않았어요.\n사용자 질문: 전입신고 기한은요?",
])
def test_unrelated_denied_quoted_and_previous_topics_do_not_activate_lookup(question):
    assert requested_tax_scopes(question) == ()


def dialogue_decision(**changes):
    base = Decision(
        intent="followup", action="rag", topic=None, topic_changed=False,
        updates={}, clarify_field=None, question=None, search_query="unused",
        document_id=None, style="standard",
    )
    return replace(base, **changes)


def test_grounded_query_prior_lookup_cannot_activate_current_document_request():
    state = {"messages": [], "documents": []}
    apply_user_update(state, user="미납국세 조회 방법이 궁금해요.", topic="세금조회")
    state["dialogue"]["last_answer"] = {
        "validation": "existing_pipeline_passed",
        "request": "미납국세 조회 방법이 궁금해요.",
    }

    query = grounded_query(
        state, "이제 계약 준비 서류를 알려 주세요.",
        dialogue_decision(purpose="documents"),
    )

    assert "직전 답변 질문: 미납국세 조회 방법이 궁금해요." in query
    assert requested_tax_scopes(query) == ()


def test_grounded_query_topic_label_cannot_suppress_current_national_lookup():
    query = grounded_query(
        {"messages": [], "documents": []},
        "임대인의 미납국세를 열람하려면 어떻게 하나요?",
        dialogue_decision(
            intent="topic_change", topic="계약갱신", topic_changed=True,
        ),
    )

    assert "대화 주제: 계약갱신" in query
    assert requested_tax_scopes(query) == ("national",)


def test_grounded_query_pending_lookup_cannot_expand_current_local_scope():
    state = {"messages": [], "documents": []}
    apply_user_update(state, user="처음 질문입니다.", topic="세금조회")
    dialogue = state["dialogue"]
    dialogue["pending"] = {
        "request": "미납국세를 조회하려면 어떻게 하나요?",
        "epoch": dialogue["epoch"], "topic": dialogue["topic"],
        "document_id": dialogue["active_document_id"],
    }

    query = grounded_query(
        state, "미납지방세 열람 방법을 알려 주세요.",
        dialogue_decision(intent="clarification_answer"),
    )

    assert "이어서 상담할 사용자 질문: 미납국세" in query
    assert requested_tax_scopes(query) == ("local",)


def test_current_input_boundary_keeps_multiline_legacy_marker_text():
    query = (
        "직전 답변 질문: 전입신고 기한은요?\n"
        "사용자 입력: 미납지방세 열람 방법을 알려 주세요.\n"
        "사용자 질문: 미납국세 조회 방법도 알려 주세요."
    )

    assert requested_tax_scopes(query) == ("national", "local")


def test_nested_current_input_marker_fails_closed_instead_of_reusing_prior_scope():
    query = (
        "직전 답변 질문: 대화 주제: 계약준비\n"
        "사용자 입력: 미납국세 열람 방법은요?\n"
        "사용자 입력: 계약 준비 서류가 궁금해요."
    )

    assert requested_tax_scopes(query) == ()


def make_chunk(cid, text, *, title="검증용 법률", doc_type="law", status="current"):
    return {"chunk_id": cid, "text": text, "metadata": {
        "doc_type": doc_type, "title": title, "status": status,
        "article_id": f"{title}-{cid}", "article_no": cid,
        "article_title": "검증용 조문", "version": "검증판", "effective_date": "2026-01-01",
    }}


@pytest.mark.parametrize("text, scopes, expected", [
    ("[검증법 제1조] 임차인은 미납국세를 열람할 수 있다.", ("national",), True),
    ("[검증법 제2조] 미납 지방세 열람을 신청한다.", ("local",), True),
    ("국세 중 납부하지 아니한 세액을 열람한다.", ("national",), True),
    ("지방세 중 납부하지 않은 세액의 열람", ("local",), True),
    ("미납지방세 열람을 신청한다.", ("national",), False),
    ("미납국세 열람을 신청한다.", ("local",), False),
    ("[미납국세 열람] 본문은 보증금 반환에 관한 규정이다.", ("national",), False),
    ("미납국세의 징수와 세액 계산에 관한 규정이다.", ("national",), False),
    ("주민등록표를 열람한다.", ("national", "local"), False),
])
def test_direct_evidence_requires_matching_tax_scope_and_body(text, scopes, expected):
    assert tax_lookup_match(text, scopes) is expected


@pytest.fixture
def selector_payload():
    chunks = [
        make_chunk("rent", "계약 종료 후 보증금 반환"),
        make_chunk("national", "임차인이 미납국세를 열람하는 절차"),
        make_chunk("national-decree", "미납국세 열람 신청에 필요한 서류", doc_type="decree"),
        make_chunk("local", "미납지방세 열람을 신청하는 방법"),
        make_chunk("move", "거주지를 이동한 경우 전입신고"),
        make_chunk("header-only", "[미납국세 열람] 본문은 단순 보증금 반환"),
    ]
    ranked = [("rent", 0.4), ("move", 0.35), ("header-only", 0.3),
              ("local", 0.2), ("national-decree", 0.15), ("national", 0.1)]
    return chunks, ranked


def test_candidate_only_selection_keeps_direct_rank_order_and_original_scores(selector_payload):
    chunks, ranked = selector_payload
    original = deepcopy(ranked)
    result = TaxLookupSelector(chunks).select(
        "미납국세 열람을 신청하려면 어떻게 하나요?", ranked, 3,
    )
    assert result == [("national-decree", 0.15), ("national", 0.1), ("rent", 0.4)]
    assert ranked == original


def test_scope_exclusion_reranks_only_the_remaining_direct_evidence(selector_payload):
    chunks, ranked = selector_payload
    result = TaxLookupSelector(chunks).select(
        "국세와 지방세 체납을 조회하고 싶지만 국세는 제외해 주세요.", ranked, 2,
    )

    assert result == [("local", 0.2), ("rent", 0.4)]


@pytest.fixture
def unbalanced_payload():
    chunks = [
        make_chunk("both", "임대인의 미납국세와 미납지방세를 열람할 수 있다"),
        make_chunk("national", "임차인이 미납국세를 열람하는 절차"),
        make_chunk("national-decree", "미납국세 열람 신청에 필요한 서류", doc_type="decree"),
        make_chunk("local", "미납지방세 열람을 신청하는 방법"),
        make_chunk("local-decree", "미납지방세 열람 신청 서류", doc_type="decree"),
        make_chunk("rent", "계약 종료 후 보증금 반환"),
    ]
    ranked = [("both", 0.6), ("national", 0.5), ("national-decree", 0.4),
              ("local", 0.3), ("local-decree", 0.2), ("rent", 0.1)]
    return chunks, ranked


def test_both_requested_tax_systems_keep_a_slot_in_a_small_budget(unbalanced_payload):
    chunks, ranked = unbalanced_payload
    result = TaxLookupSelector(chunks).select("집주인 체납세금 조회 방법은요?", ranked, 3)

    assert result == [("both", 0.6), ("national", 0.5), ("local", 0.3)]


def test_balancing_never_drops_or_adds_candidates_when_the_budget_allows(unbalanced_payload):
    chunks, ranked = unbalanced_payload
    result = TaxLookupSelector(chunks).select("집주인 체납세금 조회 방법은요?", ranked, 6)

    assert {cid for cid, _ in result} == {cid for cid, _ in ranked}
    assert result[0] == ("both", 0.6)


def test_a_single_requested_tax_system_keeps_the_fused_order(unbalanced_payload):
    chunks, ranked = unbalanced_payload
    result = TaxLookupSelector(chunks).select("집주인 미납국세 열람 방법은요?", ranked, 3)

    assert result == [("both", 0.6), ("national", 0.5), ("national-decree", 0.4)]


@pytest.mark.parametrize("text, scopes, expected", [
    ("임대인의 미납국세와 미납지방세를 열람할 수 있다", ("national", "local"),
     ("national", "local")),
    ("임차인이 미납국세를 열람하는 절차", ("national", "local"), ("national",)),
    ("미납지방세 열람을 신청하는 방법", ("national", "local"), ("local",)),
    ("임대인의 미납국세와 미납지방세를 열람할 수 있다", ("local",), ("local",)),
    ("계약 종료 후 보증금 반환", ("national", "local"), ()),
])
def test_confirmed_scopes_follow_the_body_and_the_request(text, scopes, expected):
    assert tax_lookup_scopes(text, scopes) == expected


def test_certificate_issuance_keeps_the_existing_ranking(selector_payload):
    chunks, ranked = selector_payload
    query = "임대인의 국세 체납 여부를 확인할 납세증명서 발급 방법은요?"

    assert TaxLookupSelector(chunks).select(query, ranked, 3) == ranked[:3]


@pytest.mark.parametrize("k", [0, 1, 3, 5])
def test_selection_obeys_budget_and_never_duplicates_evidence(selector_payload, k):
    chunks, ranked = selector_payload
    selector = TaxLookupSelector(chunks)
    result = selector.select("체납세금 조회 방법은요?", ranked, k)
    assert len(result) == k
    assert len({cid for cid, _ in result}) == len(result)
    assert {cid for cid, _ in result} <= {c["chunk_id"] for c in chunks}


@pytest.mark.parametrize("query", [
    "전입신고 기한은요?",
    "체납세금 조회와 전입신고 절차를 함께 알려 주세요.",
    "미납국세 열람과 보증금 반환 방법을 알려 주세요.",
    "집주인의 체납세금 조회를 완료했습니다.",
    "집주인 미납세금 조회 방법과 경매 때 우선변제 순위를 함께 알려 주세요.",
    "집주인 미납세금 조회 방법과 배당요구 절차를 함께 알려 주세요.",
    "집주인 미납세금 조회 방법과 전세사기 신고 절차를 함께 알려 주세요.",
    "집주인 미납세금 조회 방법과 중개사의 확인 설명 의무를 함께 알려 주세요.",
    "집주인 미납세금 조회 방법과 중개수수료 계산을 함께 알려 주세요.",
])
def test_unmatched_or_multi_procedure_request_keeps_the_existing_ranking(selector_payload, query):
    chunks, ranked = selector_payload
    selector = TaxLookupSelector(chunks)
    assert selector.select(query, ranked, 5) == ranked[:5]


def test_zero_budget_returns_no_evidence(selector_payload):
    chunks, ranked = selector_payload
    selector = TaxLookupSelector(chunks)
    assert selector.select("미납국세 열람 방법은요?", ranked, 0) == []


def test_candidate_only_policy_cannot_add_a_missing_article(selector_payload):
    chunks, ranked = selector_payload
    selector = TaxLookupSelector(chunks)
    initial = ranked[:2]
    result = selector.select("미납국세 열람 방법은요?", initial, 5)
    assert result == initial


@pytest.mark.parametrize("commercial", [False, True])
def test_service_keeps_existing_filters_and_does_not_add_dense_calls(commercial):
    from src.retrieval.expanded import CIVIL_IDS, ExpandedLawRetrievalService
    from src.retrieval.retriever import matches

    chunks = [
        make_chunk("old", "미납국세 열람", status="historical"),
        make_chunk("commercial", "미납국세 열람", title="상가건물 임대차보호법"),
        make_chunk("case", "미납국세 열람", doc_type="case"),
        make_chunk("guide", "미납국세 열람", doc_type="guide"),
        make_chunk("valid", "미납국세 열람 신청서류", doc_type="decree"),
    ]
    for article in CIVIL_IDS:
        civil = make_chunk(article, "미납국세 열람", title="민법")
        civil["metadata"]["article_id"] = article
        chunks.append(civil)
    calls = []
    class FilteredDense:
        def search(self, query, k, where=None):
            calls.append((query, k, deepcopy(where)))
            return [(c["chunk_id"], 1.0) for c in chunks if matches(c["metadata"], where)][:k]

    query = ("상가주택" if commercial else "주택") + " 임대인의 미납국세를 열람하려면 어떻게 하나요?"
    service = ExpandedLawRetrievalService(chunks, FilteredDense())
    result = service.search(query, k_law=5, k_civil=0, k_case=0, k_guide=0)
    assert len(calls) == 1
    assert calls[0][1] == 20
    assert calls[0][2] == route_law_corpus(query).where()
    assert {e.chunk_id for e in result.laws} == ({"valid", "commercial"} if commercial else {"valid"})
    assert [e.rank for e in result.laws] == list(range(1, len(result.laws) + 1))
    assert result.civil_laws == result.cases == result.guides == []
