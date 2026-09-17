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


def test_procedure_spacing_is_applied_before_annotation_offsets():
    import time
    from src.generation.models import Answer
    from src.retrieval.service import Evidence
    evidence = Evidence(rank=1, chunk_id='test-law', doc_type='law',
                        citation='주택임대차보호법 제6조의3', text='갱신 요구', score=1)
    text = '언제: 시기를 확인하세요. 어떻게: 주택임대차보호법 제6조의3을 확인하세요. 확인할 사항: 조건입니다.'
    answer = Answer(question='갱신 방법', status='answered', text=text, raw_text=text, laws=(evidence,))
    message = services.answer_message(answer, time.perf_counter())
    assert '\n\n어떻게:' in message['content']
    assert '\n\n확인할 사항:' in message['content']
    span = message['citations'][0]
    assert message['content'][span['start']:span['end']] == '주택임대차보호법 제6조의3'


@pytest.mark.parametrize('duration', ['2달 남았어', '세 달 정도 남았어요'])
def test_date_reply_without_model_selected_question_continues_interview(runtime, duration):
    state = services.initial_state()
    runtime.planner.return_value.decision = renewal()
    call(state, runtime, USER)
    runtime.planner.return_value.decision = replace(renewal(), intent='clarification_answer', purpose='general',
        updates={'end_date': {'value': duration, 'evidence': duration}}, clarify_field=None)
    message = call(state, runtime, duration)
    assert message['action'] == 'clarify'
    assert state['dialogue']['facts']['end_date']['value'] == duration
    assert state['dialogue']['pending']['field'] == 'landlord_notified'
    runtime.official.assert_called_once()
