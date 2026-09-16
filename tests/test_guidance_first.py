from dataclasses import replace
from unittest.mock import Mock

import pytest

from chat import services
from chat.dialogue_clarification import prepare_clarification
from chat.dialogue_guides import GuidedSearch, renewal_guides, with_dialogue_guides
from chat.dialogue_state import apply_user_update
from src.retrieval.service import RetrievalResult
from tests.test_chatting_routing import runtime, proposal, call


USER = "월세집 계약이 끝나가는데 갱신은 어떻게 해?"


def renewal(**changes):
    return proposal(topic="계약갱신", purpose="procedure", updates={
        "contract_type": {"value": "월세", "evidence": "월세"}}, **changes)


def test_procedure_gives_validated_guidance_then_one_question(runtime):
    state = services.initial_state()
    runtime.planner.return_value.decision = renewal()
    message = call(state, runtime, USER)
    assert message["content"] == runtime.official.return_value.text
    assert message["followup_question"] == "계약 종료일을 알려주시겠어요?"
    assert state["dialogue"]["pending"]["mode"] == "after_answer"
    assert isinstance(runtime.official.call_args.kwargs["service"], GuidedSearch)


@pytest.mark.parametrize("known", ["2026년 12월 31일", "모름"])
def test_never_reask_known_or_unknown_date(known):
    state = services.initial_state()
    apply_user_update(state, user=known, topic="계약갱신", updates={"end_date": {"value": known, "evidence": known}})
    decision, pending = prepare_clarification(state, USER, renewal())
    assert decision.action == "rag"
    assert pending["field"] == "landlord_notified"


@pytest.mark.parametrize("purpose,intent", [("definition", "question"), ("summary", "explain"), ("eligibility", "followup")])
def test_interleaved_question_does_not_force_interview(purpose, intent):
    state = services.initial_state()
    _, pending = prepare_clarification(state, USER, replace(renewal(), purpose=purpose, intent=intent))
    assert pending is None


def test_no_personal_fact_means_no_automatic_interview():
    _, pending = prepare_clarification(services.initial_state(), "갱신 절차는?", replace(renewal(), updates={}))
    assert pending is None


def test_guides_preserve_shared_results_and_case_only_contract():
    base = RetrievalResult(question="q")
    service = Mock()
    service.search.return_value = base
    wrapped = GuidedSearch(service)
    result = wrapped.search("q", k_guide=2)
    assert base.guides == []
    assert len(result.guides) == 2
    assert result.guides[0].source_url.startswith("https://www.korea.kr/")
    assert len(wrapped.search("q", k_guide=1).guides) == 1
    assert wrapped.search("q", k_law=0, k_guide=0) is base
    assert all(guide.doc_type == "guide" for guide in renewal_guides())


def test_guides_only_in_renewal_official_search():
    service = object()
    assert with_dialogue_guides(service, proposal(topic="시설수리")) is service
    assert with_dialogue_guides(service, renewal(document_id="document-a")) is service
    assert isinstance(with_dialogue_guides(service, renewal()), GuidedSearch)
