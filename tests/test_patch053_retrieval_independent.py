"""Independent adversarial checks for current intent and generation payloads.

These are shared development regressions, not an unseen retrieval holdout.
No KURE index or generation API is accessed by these tests.
"""
from copy import deepcopy

import pytest

from src.retrieval.retrieval_intent import LawIntentSelector, requested_law_intents


@pytest.mark.parametrize("question, expected", [
    ("등록민간임대주택의 계약 신고 절차만 알려주세요.", {"private_report"}),
    ("등록민간임대주택에서 재계약을 거절할 수 있는 조건은요?", {"private_renewal"}),
    ("등록민간임대주택 계약서 서식은 무엇을 쓰나요?", {"private_form"}),
    ("등록민간임대주택의 계약 신고와 재계약에 필요한 서류는요?",
     {"private_report", "private_renewal", "private_form"}),
    ("등록민간임대주택입니다. 재계약은 묻지 않고 계약 신고 절차만 알려주세요.",
     {"private_report"}),
    ("등록민간임대주택입니다. 계약 신고는 묻지 않고 재계약 조건만 알려주세요.",
     {"private_renewal"}),
    ("전입신고는 이사하고 언제까지 해야 하나요?", {"residence_procedure"}),
    ("전입신고는 어디서 신청하나요?", {"residence_procedure"}),
    ("전입신고를 아직 안 했는데 언제까지 해야 하나요?", {"residence_procedure"}),
    ("전입신고를 못 했는데 어디서 접수하나요?", {"residence_procedure"}),
    ("전입신고 절차와 보증금 보호 효과를 함께 알려주세요.", {"residence_procedure"}),
])
def test_each_current_question_owns_only_its_explicit_intents(question, expected):
    assert set(requested_law_intents(question)) == expected


@pytest.mark.parametrize("question", [
    "공공임대주택의 계약 신고와 재계약에 필요한 서류는요?",
    "일반 전세 계약의 신고와 재계약에 필요한 서류는요?",
    "상가 임대사업자의 계약 신고와 재계약에 필요한 서류는요?",
    "전입신고를 마쳤습니다. 보증금의 대항력은 언제 생기나요?",
    "전입신고의 절차는 묻지 않습니다. 보증금 보호 효과만 궁금합니다.",
    '예문은 "등록민간임대주택 계약 신고와 재계약 서류는요?"입니다. 중개보수만 알려주세요.',
    "`등록민간임대주택 계약 신고와 재계약 서류`는 예시입니다. 중개보수만 알려주세요.",
    "> 등록민간임대주택 계약 신고와 재계약 서류는요?\n중개보수만 알려주세요.",
    "등록민간임대주택의 계약 신고와 재계약 서류가 궁금했습니다. 지금은 중개보수만 알려주세요.",
    "직전 답변 질문: 등록민간임대주택 계약 신고와 재계약 서류는요?\n사용자 입력: 중개보수만 알려주세요.",
    "이전 대화: 등록민간임대주택 계약 신고와 재계약 서류는요?\n사용자 질문: 중개보수만 알려주세요.",
    "직전 답변 질문: 등록민간임대주택에 거주합니다.\n사용자 입력: 계약 신고와 재계약 서류는요?",
    "사용자 입력: 등록민간임대주택 계약 신고와 재계약 서류는요?\n사용자 입력: 중개보수만 알려주세요.",
    "사용자 추가 상황: 등록민간임대가 아닌 일반 전세주택임.\n사용자 질문: 계약 신고와 재계약 서류는요?",
    "사용자 추가 상황: 과거 집은 등록민간임대였지만 지금 계약하려는 집은 일반 주택임.\n사용자 질문: 계약 신고와 재계약 서류는요?",
])
def test_other_scope_denial_quotation_and_prior_turn_do_not_activate(question):
    assert requested_law_intents(question) == ()


def test_current_facts_supply_scope_without_reusing_old_topic():
    query = ("이전 대화: 공공임대주택의 계약 갱신은요?\n"
             "사용자 추가 상황: 지금 임차하려는 집은 등록민간임대주택임.\n"
             "사용자 질문: 계약 신고와 재계약에 필요한 서류는요?")
    assert set(requested_law_intents(query)) == {
        "private_report", "private_renewal", "private_form",
    }


def test_current_input_does_not_reparse_embedded_legacy_label():
    query = ("직전 답변 질문: 공공임대주택입니다.\n"
             "사용자 입력: 등록민간임대주택의 재계약 조건은요?\n"
             "사용자 질문: 계약 신고 절차도 알려주세요.")
    assert set(requested_law_intents(query)) == {"private_renewal", "private_report"}


def _chunk(cid, number, body, *, title="검증주거법", doc_type="law", **metadata):
    values = {
        "title": title, "doc_type": doc_type, "status": "current",
        "article_id": title + "-" + number, "article_no": number,
        "article_title": "검증 조문", "version": "법률 검증판",
        "effective_date": "2026-01-01", "source_url": "https://example.invalid/" + cid,
    }
    values.update(metadata)
    return {"chunk_id": cid, "doc_id": title + "-20260101", "metadata": values,
            "text": f"[{title} {number}(검증 조문)]\n{body}"}


@pytest.mark.parametrize("question", [
    "등록민간임대주택의 신고와 재계약 서류, 보증보험과 미납국세 안내 및 관련 판례를 알려주세요.",
    "전입신고의 신청 기한과 보증보험 및 미납국세 안내, 관련 판례를 알려주세요.",
])
def test_real_graph_payload_preserves_four_separate_channel_budgets(question):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    from src.generation import graph, llm
    from src.retrieval.expanded import CIVIL_IDS, ExpandedLawRetrievalService
    from src.retrieval.hybrid import HybridRetriever, Member
    from src.retrieval.retriever import matches
    from src.retrieval.service import GUIDE_TOPICS

    chunks = [_chunk(f"law-{i}", f"제{i}조", f"① 주택 계약 신고 검증내용{i}.")
              for i in range(1, 6)]
    chunks += [_chunk(article, article.split("-", 1)[1], "① 주택 임대차 계약 규정.", title="민법")
               for article in CIVIL_IDS]
    chunks += [_chunk(f"case-{i}", str(i), f"주택 계약 분쟁 검증판결{i}.",
                      title="검증법원", doc_type="case") for i in range(1, 5)]
    chunks += [_chunk(f"guide-{i}", str(i), f"주택 안내 검증자료{i}.",
                      title="검증기관", doc_type="guide", article_id=topic.guide_id)
               for i, topic in enumerate(GUIDE_TOPICS, 1)]
    original = deepcopy(chunks)

    class FixedCandidates:
        def search(self, query, k, where=None):
            return [(chunk["chunk_id"], 1 / (i + 1)) for i, chunk in enumerate(chunks)
                    if matches(chunk["metadata"], where)][:max(0, k)]

    class ObservedService(ExpandedLawRetrievalService):
        def search(self, question, *args, **kwargs):
            value = super().search(question, *args, **kwargs)
            outputs.append((kwargs, value))
            return value

    outputs, captured = [], []
    service = ObservedService(chunks)
    fixed = FixedCandidates()
    service._context_law = HybridRetriever([Member(fixed, "independent_candidates")], rrf_k=5, depth=20)
    service._context_civil = HybridRetriever([Member(fixed, "bm25_context")], rrf_k=5, depth=26)
    service._retrievers[service.corpora[1].name] = fixed
    service._retrievers[service.corpora[2].name] = fixed

    def capture(value):
        captured.append(value.to_messages())
        return AIMessage(content="제공된 근거만으로는 답변하기 어렵습니다.")

    graph.answer_question(question, service=service, llm=RunnableLambda(capture),
                          auxiliary_llm=llm.get_llm(fake_responses=["PASS"]))
    assert len(outputs) == 2
    assert outputs[0][0] == {"k_law": 3, "k_case": 0, "k_guide": 2}
    assert outputs[1][0] == {"k_law": 0, "k_case": 2, "k_guide": 0}
    first, second = outputs[0][1], outputs[1][1]
    assert tuple(map(len, (first.laws, first.civil_laws, second.cases, first.guides))) == (3, 3, 2, 2)
    assert not first.cases and not second.laws and not second.civil_laws and not second.guides
    assert all(e.citation.startswith("민법") for e in first.civil_laws)
    assert all(not e.citation.startswith("민법") for e in first.laws)
    assert captured
    human = next(message.content for message in captured[0] if message.type == "human")
    evidence = first.laws + first.civil_laws + second.cases + first.guides
    assert len({e.chunk_id for e in evidence}) == 10
    for entry in evidence:
        assert entry.text in human
    for chunk in chunks:
        if chunk["chunk_id"] not in {entry.chunk_id for entry in evidence}:
            assert chunk["text"] not in human
    assert chunks == original


@pytest.fixture
def independent_intent_pool():
    private = "검증민간임대법"
    chunks = [_chunk("top", "제1조", "① 일반 계약 규정이다."),
              _chunk("second", "제2조", "① 보증금 반환 규정이다."),
              _chunk("third", "제3조", "① 계약 신고의 일반 규정이다."),
              _chunk("renewal", "제81조", "① 임대사업자는 임차인의 귀책사유가 없으면 재계약을 거절할 수 없다.", title=private),
              _chunk("report", "제72조", "① 임대사업자는 임대차계약을 체결한 경우 신고를 하여야 한다.", title=private),
              _chunk("form", "제63조", "① 임대사업자가 임대차계약을 체결하는 경우 표준임대차계약서를 사용하여야 한다.", title=private),
              _chunk("resident", "제54조", "① 거주지를 이동하면 전입한 날부터 정해진 기한 이내에 전입신고를 하여야 한다.")]
    return chunks, [(c["chunk_id"], 1 / rank) for rank, c in enumerate(chunks, 1)]


def test_compound_selects_existing_body_proven_representatives_without_mutation(independent_intent_pool):
    chunks, ranked = independent_intent_pool
    before = deepcopy((chunks, ranked))
    question = "등록민간임대주택의 계약 신고와 재계약에 필요한 서류는요?"
    selected = LawIntentSelector(chunks).select(question, ranked, 3)
    assert [cid for cid, _ in selected] == ["renewal", "report", "form"]
    assert all(hit in ranked for hit in selected)
    assert (chunks, ranked) == before


@pytest.mark.parametrize("replacement", [
    "① 예전 문구는 ‘임대사업자는 재계약을 거절할 수 없다’였다.",
    '① 안내문에 "임대사업자는 재계약을 거절할 수 없다"라고 적는다.',
    "① 임대사업자는 재계약을 거절할 수 없다는 규정을 적용하지 아니한다.",
    "① 임대사업자는 재계약을 거절할 수 없다고 보지 아니한다.",
])
def test_quoted_or_negated_predicate_cannot_promote_renewal(independent_intent_pool, replacement):
    chunks, ranked = independent_intent_pool
    chunks[3]["text"] = "[검증민간임대법 제81조(검증 조문)]\n" + replacement
    query = "등록민간임대주택에서 재계약을 거절할 수 있는 조건은요?"
    assert LawIntentSelector(chunks).select(query, ranked, 3) == ranked[:3]


@pytest.mark.parametrize("changes", [
    {"status": "historical"}, {"doc_type": "guide"}, {"version": ""},
    {"effective_date": ""}, {"article_id": "검증민간임대법-제99조"},
])
def test_ineligible_metadata_cannot_supply_missing_intent(independent_intent_pool, changes):
    chunks, ranked = independent_intent_pool
    chunks[3]["metadata"].update(changes)
    query = "등록민간임대주택에서 재계약을 거절할 수 있는 조건은요?"
    assert LawIntentSelector(chunks).select(query, ranked, 3) == ranked[:3]


def test_representative_must_be_retrieved_and_match_request_filter(independent_intent_pool):
    chunks, ranked = independent_intent_pool
    selector = LawIntentSelector(chunks)
    query = "등록민간임대주택에서 재계약을 거절할 수 있는 조건은요?"
    assert selector.select(query, ranked[:3], 3) == ranked[:3]
    assert selector.select(query, ranked, 3, {"title": "검증주거법"}) == ranked[:3]


@pytest.mark.parametrize("k", [3, 5, 21])
def test_full_union_uses_one_existing_depth_search_per_member(k):
    from src.retrieval.hybrid import HybridRetriever, Member
    from src.retrieval.retrieval_intent import existing_member_union

    calls = []
    where = {"status": "current"}

    class RankedMember:
        def __init__(self, prefix):
            self.prefix = prefix

        def search(self, query, depth, where=None, expand_weight=0):
            calls.append((self.prefix, query, depth, where, expand_weight))
            return [(self.prefix + str(i), 1.0) for i in range(depth)]

    retriever = HybridRetriever([
        Member(RankedMember("a"), "lexical", 2.0, 0.75),
        Member(RankedMember("b"), "dense", 1.0),
    ], depth=20, rrf_k=5)
    retriever._last = {"stale": ["must-not-enter"]}
    actual = existing_member_union(retriever, "현재 요청", k, where)
    depth = max(20, k)
    assert calls == [("a", "현재 요청", depth, where, 0.75),
                     ("b", "현재 요청", depth, where, 0)]
    expected = [(prefix + str(i), weight / (6 + i))
                for prefix, weight in (("a", 2.0), ("b", 1.0)) for i in range(depth)]
    assert actual == sorted(expected, key=lambda hit: (-hit[1], hit[0]))
    assert len(actual) == 2 * depth
    assert retriever._last == {"stale": ["must-not-enter"]}


@pytest.mark.parametrize("question, expected", [
    ("등록민간임대에서 재계약하면서 올린 임대료 인상 제한과 변경 신고에 필요한 서류를 알려주세요.",
     {"private_report", "private_form"}),
    ("등록민간임대에서 갱신한 뒤 임대료를 올렸습니다. 변경 신고에 필요한 서류는요?",
     {"private_report", "private_form"}),
    ("등록민간임대 재계약은 마쳤습니다. 임대료 인상 한도와 변경 신고 서식을 알려주세요.",
     {"private_report", "private_form"}),
    ("등록민간임대에서 재계약하면서 올린 임대료 한도만 알려주세요.", set()),
    ("등록민간임대에서 재계약하면서 변경 신고 서류를 알려주세요.",
     {"private_report", "private_form"}),
    ("등록민간임대 계약 신고와 재계약 서류는 모두 제외하고 보증금 반환 절차만 알려주세요.", set()),
    ("등록민간임대 계약 신고와 재계약 서류는 제외하고 보증금 반환 절차만 알려주세요.", set()),
    ("등록민간임대 계약 신고, 재계약, 계약서 서식은 모두 묻지 않습니다. 반환 절차만 알려주세요.", set()),
    ("등록민간임대의 계약 신고와 계약서 서류는 모두 제외하고 재계약 조건만 알려주세요.",
     {"private_renewal"}),
    ("등록민간임대 계약 신고와 재계약 서류는 모두 제외합니다. 다만 계약 신고 절차는 알려주세요.",
     {"private_report"}),
    ("등록민간임대의 신고 서류는 묻지 않습니다. 재계약 조건은 알려주세요.",
     {"private_renewal"}),
    ("등록민간임대의 계약 신고와 재계약 서류는 제외하지 말고 알려주세요.",
     {"private_report", "private_renewal", "private_form"}),
    ("전입신고 방법은 묻지 않습니다. 주민등록은 완료했습니다. 계약 해지 절차만 알려주세요.", set()),
    ("주민등록은 완료했습니다. 전입신고 방법은 제외하고 계약 해지 절차만 알려주세요.", set()),
    ("전입신고의 방법과 주민등록 절차는 모두 제외하고 보증금 반환 방법만 알려주세요.", set()),
    ("전입신고 방법은 묻지 않습니다. 주민등록 기한만 알려주세요.", {"residence_procedure"}),
    ("주민등록은 완료했습니다. 다른 집으로 이사할 때 전입신고를 다시 하려면 어디로 가나요?",
     {"residence_procedure"}),
    ("전입신고는 안 마쳤습니다. 주민등록 신청은 어디서 하나요?", {"residence_procedure"}),
    ("등록민간임대 집이면 임대차 신고나 재계약할 때 일반 집이랑 다른 서류나 절차가 더 있나요?",
     {"private_report", "private_renewal", "private_form"}),
    ("등록민간임대에서 재계약 조건과 변경 신고에 필요한 서류를 알려주세요.",
     {"private_report", "private_renewal", "private_form"}),
    ("등록민간임대의 재계약과 계약 신고에 필요한 서류는요?",
     {"private_report", "private_renewal", "private_form"}),
    ("등록민간임대의 재계약 및 변경 신고 절차와 서류를 알려주세요.",
     {"private_report", "private_renewal", "private_form"}),
    ("등록민간임대 재계약 조건은 알려주세요, 계약 신고와 서류는 모두 제외합니다.",
     {"private_renewal"}),
])
def test_review_reported_requests_keep_negation_and_background_in_their_own_scope(question, expected):
    assert set(requested_law_intents(question)) == expected


def test_review_background_renewal_cannot_evict_existing_rent_evidence(independent_intent_pool):
    chunks, _ = independent_intent_pool
    chunks[0] = _chunk("top", "제91조", "① 임대료 증액청구는 정해진 제한을 따른다.",
                       title="검증민간임대법", article_title="임대료의 제한")
    ranked = [(cid, score) for cid, score in (
        ("top", 1.0), ("report", .9), ("form", .8), ("renewal", .7))]
    question = "등록민간임대에서 재계약하면서 올린 임대료 인상 제한과 변경 신고에 필요한 서류를 알려주세요."
    assert LawIntentSelector(chunks).select(question, ranked, 3) == ranked[:3]


@pytest.mark.parametrize("question", [
    "등록민간임대 계약 신고와 재계약 서류는 모두 제외하고 보증금 반환 절차만 알려주세요.",
    "전입신고 방법은 묻지 않습니다. 주민등록은 완료했습니다. 계약 해지 절차만 알려주세요.",
])
def test_review_excluded_requests_leave_existing_three_hits_untouched(independent_intent_pool, question):
    chunks, ranked = independent_intent_pool
    assert LawIntentSelector(chunks).select(question, ranked, 3) == ranked[:3]
