"""Independent PR review examples through the product selection boundary."""
from copy import deepcopy

import pytest

from src.retrieval.expanded import CIVIL_IDS, ExpandedLawRetrievalService
from src.retrieval.multi_evidence import requested_tax_scopes
from src.retrieval.service import route_law_corpus


@pytest.mark.parametrize("question,scopes,selected", [
    ("국세와 지방세 체납을 조회하고 싶지만 국세는 제외해 주세요.",
     ("local",), "local"),
    ("국세와 지방세 체납 조회 방법을 알려 주세요. 지방세는 제외해 주세요.",
     ("national",), "national"),
    ("국세와 지방세 체납 조회 방법을 알려 주세요. 국세는 묻지 않습니다.",
     ("local",), "local"),
    ("미납국세 조회 방법을 알려 주세요. 국세는 제외해 주세요.", (), "ordinary"),
    ("국세와 지방세 체납 조회 방법을 알려 주세요. 다만 국세와 지방세는 제외해 주세요.",
     (), "ordinary"),
    ("국세는 제외하고 지방세 체납 조회 방법은요?", ("local",), "local"),
    ("지방세는 제외하고 국세 체납 조회 방법은요?", ("national",), "national"),
    ("임대인의 국세 체납 사실을 증명할 납세증명서는 어디서 발급받나요?",
     (), "ordinary"),
    ("임대인의 지방세 체납 여부를 확인할 납세증명서 발급 방법은요?",
     (), "ordinary"),
    ("임대인의 국세 체납 여부를 조회하려고 납세증명서를 발급받고 싶어요.",
     (), "ordinary"),
    ("미납국세 열람 신청에 필요한 서류를 알려 주세요.",
     ("national",), "national"),
    ("미납국세는 임대인 동의가 필요 없어도 열람 방법을 알려주세요",
     ("national",), "national"),
    ("미납국세는 동의가 필요 없을 때 어떻게 열람하나요?",
     ("national",), "national"),
    ("미납지방세 열람 절차를 알려 주세요.", ("local",), "local"),
    ("국세와 지방세 체납 조회 방법을 알려 주세요.",
     ("national", "local"), "national"),
    ('예문은 "국세는 제외해 주세요"입니다. 미납국세 열람 방법은요?',
     ("national",), "national"),
    ('예문은 "납세증명서 발급"입니다. 미납국세 열람 방법은요?',
     ("national",), "national"),
    ("직전 답변 질문: 납세증명서 발급 방법은요?\n사용자 입력: 미납지방세 열람 방법은요?",
     ("local",), "local"),
    ("직전 답변 질문: 국세는 제외해 주세요.\n사용자 입력: 미납국세 열람 방법은요?",
     ("national",), "national"),
    ("직전 답변 질문: 미납국세 열람 방법은요?\n사용자 입력: 납세증명서는 어디서 발급받나요?",
     (), "ordinary"),
])
def test_review_examples_choose_only_current_requested_inspection_evidence(question, scopes, selected):
    def chunk(cid, text, title="검증법"):
        return {"chunk_id": cid, "text": text, "metadata": {
            "title": title, "doc_type": "law", "status": "current",
            "article_id": cid, "article_no": "1", "article_title": "검증",
            "version": "검증판", "effective_date": "2026-01-01",
        }}

    chunks = [chunk("ordinary", "납세증명서 발급 일반 절차"),
              chunk("national", "미납국세 열람 신청 절차"),
              chunk("local", "미납지방세 열람 신청 절차")]
    chunks += [chunk(cid, "민법 계약 조문", "민법") for cid in CIVIL_IDS]
    ranked = [("ordinary", .3), ("national", .2), ("local", .1)]
    before = deepcopy((chunks, ranked))
    calls = []

    class FusedCandidates:
        def search(self, query, k, where):
            calls.append((query, k, where))
            return ranked[:k]

    service = ExpandedLawRetrievalService(chunks)
    service._context_law = FusedCandidates()
    result = service.search(question, k_law=1, k_civil=0, k_case=0, k_guide=0)

    assert requested_tax_scopes(question) == scopes
    assert [(e.chunk_id, e.rank, e.score) for e in result.laws] == [(selected, 1, dict(ranked)[selected])]
    assert calls == [(question, 20 if scopes else 1, route_law_corpus(question).where())]
    assert result.civil_laws == result.cases == result.guides == []
    assert (chunks, ranked) == before
