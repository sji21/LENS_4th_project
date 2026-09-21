"""Semantic retrieval policies, with no evaluation IDs or answer lookups."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.generation.evidence_routing import (classify_question_type, retrieve_staged,
                                             requires_case_interpretation)
from src.retrieval.case_profile import SupplementalCaseDense, _supplemental_cases
from src.retrieval.companion_evidence import requests_lease_protection
from src.retrieval.context_policy import civil_concepts
from src.retrieval.expanded import CIVIL_IDS, LEGACY_CIVIL_IDS, LEGACY_POLICY, POLICY
from src.retrieval.retrieval_intent import LawIntentSelector, requested_law_intents
from src.retrieval.service import Evidence, RetrievalResult


@pytest.mark.parametrize("question", [
    "보증금을 돌려주지 않은 채 집을 판다면 새 소유자에게 반환 의무가 있나요?",
    "제3자가 제 주소를 몰래 바꿨습니다. 대항력은 계속 유효한가요?",
    "건물을 돌려주었는데 보증금 반환을 거부하면 손해배상 책임이 있나요?",
    "월세 연체액은 합의 없이 보증금에서 자동 공제되나요?",
    "갱신을 요구하자 임대인이 직접 거주한다며 거절했습니다. 인정되나요?",
])
def test_fact_dependent_dispute_requests_case_interpretation(question):
    assert classify_question_type(question) == "case"


@pytest.mark.parametrize("question", [
    "대항력은 언제 생기나요?",
    "손해배상 책임의 정의는 무엇인가요?",
    "근저당권의 효력이란 어떤 의미인가요?",
    "확정일자는 어디서 받나요?",
    "계약갱신요구권을 몇 번 쓸 수 있나요?",
    "임대차분쟁조정 신청에 필요한 서류는 무엇인가요?",
    "강제집행 조정서에 어떤 사항을 기재하나요?",
    '이전 대화: 대법원 판례에 대해 알려줘\n사용자 질문: 전입신고 접수처는 어디인가요?',
    '"보증금 반환 거부 판례"는 책 제목이고 확정일자 발급 수수료만 알려주세요.',
    "판례는 제외하고 대항력의 법정 요건만 설명해주세요.",
])
def test_simple_procedure_and_excluded_case_topics_do_not_add_cases(question):
    assert classify_question_type(question) != "case"


def test_dispute_adds_cases_even_when_primary_law_exists():
    calls = []
    law = Evidence(1, "law", "law", "법령", "본문", 1)
    case = Evidence(1, "case", "case", "판례", "본문", 1)
    class Service:
        def search(self, question, k_law=5, k_case=5, k_guide=2):
            calls.append((k_law, k_case, k_guide))
            return RetrievalResult(question, laws=[law] if k_law else [], cases=[case] if k_case else [])
    route = retrieve_staged(Service(), "제3자가 주소를 바꾼 뒤에도 대항력이 유효한가요?", k_law=3, k_case=2, k_guide=2)
    assert calls == [(3, 0, 2), (0, 2, 0)]
    assert route.result.laws == [law] and route.result.cases == [case]


def test_changed_deposit_priority_and_key_handover_have_legal_concepts():
    assert requests_lease_protection("확정일자 이후 보증금을 증액하면 기존 보증금의 순위는 달라지나요?")
    assert "쌍무계약 동시이행의 항변권 채무이행 거절" in civil_concepts("열쇠를 반납했지만 보증금을 받지 못했어요")
    assert not requests_lease_protection("확정일자 기록은 어디서 조회하나요?")


@pytest.fixture
def official_chunks():
    from test_patch051_generation_delivery import _official_snapshot_chunks
    return _official_snapshot_chunks()


def test_mediation_keeps_scope_agreement_and_enforcement_from_existing_pool(official_chunks):
    selector = LawIntentSelector(official_chunks)
    by_article = {c["metadata"]["article_id"]: c["chunk_id"] for c in official_chunks}
    articles = ["주택임대차보호법-제" + str(n) + "조" for n in (21, 26, 23, 14, 27)]
    ranked = [(by_article[a], 1 / (i + 1)) for i, a in enumerate(articles)]
    result = selector.select("보증금 반환 분쟁도 분쟁조정을 신청할 수 있나요? 조정서로 강제집행하려면요?", ranked, 3)
    assert {cid for cid, _ in result} == {by_article[a] for a in articles if a.endswith(("제14조", "제26조", "제27조"))}
    # A missing candidate is never injected from a stored answer/article list.
    assert by_article[articles[-1]] not in {cid for cid, _ in selector.select("조정서의 강제집행 효력을 설명해주세요", ranked[:-1], 3)}


def test_record_lookup_keeps_right_and_eligibility_not_only_issuance(official_chunks):
    selector = LawIntentSelector(official_chunks)
    chunks = {c["metadata"]["article_id"]: c for c in official_chunks}
    articles = ["주택임대차보호법 시행령-제4조", "주택임대차보호법-제3조의6", "주택임대차보호법 시행령-제5조"]
    ranked = [(chunks[a]["chunk_id"], .9 - .1 * i) for i, a in enumerate(articles)]
    intents = requested_law_intents("계약서를 분실했어요. 확정일자 기록을 열람할 수 있나요?")
    assert intents == ("date_information_right", "date_information_eligibility")
    assert {cid for cid, _ in selector.select("확정일자 기록을 조회할 수 있나요?", ranked, 3)} == {cid for cid, _ in ranked}
    assert "date_information_right" in selector.intents[ranked[1][0]]
    assert "date_information_eligibility" in selector.intents[ranked[2][0]]


@pytest.mark.parametrize("policy,civil_ids", [(LEGACY_POLICY, LEGACY_CIVIL_IDS), (POLICY, CIVIL_IDS)])
def test_old_and_new_profile_civil_sets_remain_bound(tmp_path, policy, civil_ids):
    from src.retrieval import profile as profiles
    chunks = [{"chunk_id": cid, "text": cid, "metadata": {"title": "민법", "article_id": cid, "doc_type": "law", "status": "current"}} for cid in civil_ids]
    for name in profiles.FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) if name == "chunks/chunks.jsonl" else "", encoding="utf-8")
    data = {"version": 1, "policy": policy, "model": "nlpai-lab/KURE-v1", "civil_ids": list(civil_ids),
            "files": {p: profiles.file_hash(tmp_path / p) for p in profiles.FILES}, "index_hashes": ["a" * 64, "b" * 64]}
    (tmp_path / profiles.PROFILE).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / profiles.PROFILE).write_text(json.dumps(data), encoding="utf-8")
    service = profiles.build_profiled_service(chunks, [tmp_path / p for p in profiles.CHUNKS])
    assert service.civil.include_ids == civil_ids and service.profile_name == policy
    data["civil_ids"] = list(CIVIL_IDS if policy == LEGACY_POLICY else LEGACY_CIVIL_IDS)
    (tmp_path / profiles.PROFILE).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(profiles.RetrievalProfileError):
        profiles.read_profile(tmp_path, chunks, [tmp_path / p for p in profiles.CHUNKS])


def test_supplement_dense_uses_same_backend_and_one_cosine_ranking():
    calls = []
    class Backend:
        name = "pinned-test-model"
        def embed(self, texts):
            calls.append(list(texts))
            return [[.1, .995] if t == "low relevance" else [1, 0] for t in texts]
    backend = Backend()
    class Frozen:
        def __init__(self):
            self.backend = backend
        def search(self, query, k, where=None):
            return [("existing-high", .95), ("existing-middle", .6)][:k]
    supplement = {"chunk_id": "extra", "text": "low relevance", "metadata": {"doc_type": "case", "status": "current"}}
    merged = SupplementalCaseDense(Frozen(), [supplement])
    assert merged.backend is backend
    assert calls == [["low relevance"]]
    assert merged.search("question", 2) == [("existing-high", .95), ("existing-middle", .6)]
    assert merged.search("question", 3)[-1][0] == "extra"


def test_supplement_does_not_add_seed_cases_or_existing_canonical_case():
    def chunk(cid, key, role="case_supplement"):
        return {"chunk_id": cid, "text": "case", "metadata": {"doc_type": "case", "status": "current", "corpus_role": role, "canonical_case_key": key}}
    old = chunk("old", "a" * 64)
    extra = chunk("extra", "b" * 64)
    duplicate = chunk("same-case-other-id", "a" * 64)
    seed = chunk("seed", "c" * 64, "seed")
    base = SimpleNamespace(_chunks={c["chunk_id"]: c for c in [extra, duplicate, seed]})
    assert _supplemental_cases([old], base) == [extra]


def test_attaching_base_rebuilds_case_bm25_and_does_not_accumulate_old_supplements():
    from src.retrieval.case_profile import CaseCorpusRetrievalService
    def chunk(cid, key, role=""):
        return {"chunk_id": cid, "text": "임대차 보증금", "metadata": {"doc_type": "case", "status": "current", "corpus_role": role, "canonical_case_key": key}}
    old = chunk("old", "a" * 64)
    extra = chunk("extra", "b" * 64, "case_supplement")
    seed = chunk("seed", "c" * 64)
    profile = {"case_rrf_k": 60, "candidate_depth": 80, "return_k": 5, "rerank_policy": "pure_rrf"}
    service = CaseCorpusRetrievalService([old], None, profile)
    service.attach_base_service(SimpleNamespace(_chunks={c["chunk_id"]: c for c in [extra, seed]}))
    retriever = service._retrievers[service.corpora[1].name]
    assert retriever.members[0].retriever.chunk_ids == ["old", "extra"]
    assert len(retriever.members) == 1
    service.attach_base_service(None)
    assert list(service._chunks) == ["old"]


def test_legacy_service_never_searches_new_civil_articles():
    from src.retrieval.expanded import ExpandedLawRetrievalService
    chunks = [{"chunk_id": cid, "text": "임대차 계약 반환 " + cid, "metadata": {"title": "민법", "article_id": cid, "article_no": cid.split("-")[1], "doc_type": "law", "status": "current"}} for cid in CIVIL_IDS]
    service = ExpandedLawRetrievalService(chunks, civil_ids=LEGACY_CIVIL_IDS, policy=LEGACY_POLICY)
    result = service.search("민법 제131조 계약", k_law=0, k_case=0, k_guide=0)
    assert {e.chunk_id for e in result.civil_laws} <= set(LEGACY_CIVIL_IDS)


@pytest.mark.parametrize("question", [
    "보증금을 못 받은 경우 반환 청구 신청서를 어디서 내려받나요?",
    "임대인이 보증금 반환을 거절했어요. 반환 청구 신청서 다운로드 주소만 알려주세요.",
    "집주인이 갱신을 거절했습니다. 분쟁조정 신청에 필요한 서류와 접수처를 알려주세요.",
    "보증금을 안 주는데 반환 청구 절차와 수수료가 궁금해요.",
    "보증금을 못 받았는데 반환 청구를 어떻게 접수하나요?",
    "보증금 반환을 거절당했어요. 분쟁조정은 어디서 신청하나요?",
    "보증금 반환 청구 서류는 어디에 제출하고 어떤 양식으로 작성하나요?",
])
def test_dispute_background_does_not_turn_paperwork_request_into_case_question(question):
    assert requires_case_interpretation(question) is False
    assert classify_question_type(question) != "case"


@pytest.mark.parametrize("question", [
    "보증금을 못 받았는데 새 소유자에게 반환 청구를 할 수 있나요? 신청서도 어디서 받는지 알려주세요.",
    "보증금을 못 받은 경우 반환 청구 신청서는 어디서 내려받고, 임대인의 지체책임은 물을 수 있나요?",
    "집주인이 실거주를 이유로 갱신을 거절했어요. 거절이 유효한지와 분쟁조정 신청서 양식을 알려주세요.",
    "반환 청구 신청서를 내려받고 싶어요. 보증금을 안 주는 임대인에게 손해배상 책임이 있나요?",
    "보증금을 못 받았는데 새 소유자의 반환 의무도 설명해주시고 신청서 다운로드도 알려주세요.",
])
def test_legal_outcome_and_paperwork_requests_still_add_cases(question):
    assert requires_case_interpretation(question) is True
    assert classify_question_type(question) == "case"


@pytest.mark.parametrize("question", [
    "확정일자 받은 기록을 열람할 수 있나요?",
    "확정일자를 부여받은 내역을 조회하고 싶어요.",
    "확정일자를 받았던 기록은 어디서 찾나요?",
    "확정 일자가 부여된 기록을 확인하려고 해요.",
])
def test_date_record_recognizes_received_record_modifiers(question):
    from src.retrieval.context_policy import requests_date_record, final_law_concepts
    assert requests_date_record(question)
    assert requested_law_intents(question) == ("date_information_right", "date_information_eligibility")
    assert "정보제공 요청 이해관계인 임차인 범위" in final_law_concepts(question)


@pytest.mark.parametrize("question", [
    "확정일자를 받은 경우 우선변제 효력이 생기는지 확인하고 싶어요.",
    "확정일자 받은 기록은 조회하지 말고 대항력 효력만 설명해주세요.",
    '"확정일자를 받은 기록을 열람하고 싶어요"라는 문장과 관계없이 수수료만 알려주세요.',
])
def test_received_date_fact_does_not_become_record_lookup(question):
    from src.retrieval.context_policy import requests_date_record
    assert not requests_date_record(question)


@pytest.mark.parametrize("ending", ["가능한가요", "가능합니까"])
def test_legal_possibility_question_survives_accompanying_form_request(ending):
    question = f"보증금을 못 받았어요. 손해배상 청구 {ending}? 신청서도 알려주세요."
    assert requires_case_interpretation(question) is True
    assert classify_question_type(question) == "case"


def test_form_download_possibility_is_not_legal_entitlement():
    question = "보증금을 못 받았어요. 손해배상 청구 신청서 다운로드가 가능한가요?"
    assert requires_case_interpretation(question) is False
    assert classify_question_type(question) != "case"
