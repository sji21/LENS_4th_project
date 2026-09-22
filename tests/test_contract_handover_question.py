import pytest
from chat.document_review import contract_handover_answer
from chat.services import initial_state
from tests.test_chatting_routing import runtime, call


def contract(text):
    return {'document_id':'c','kind':'contract','filename':'contract.pdf','page_count':1,
            'context':{'chunks':[{'page_number':1,'text':text}]}}


TEXT = '계약 체결일 2023년 01월 19일. 2023년 02월 19일 까지 임차인에게 인도하며, 임대차 기간은 인도일로부터\n2025년 02월 18일(24개월)까지로 한다.'


def test_reads_handover_not_signature_or_expiry():
    answer = contract_handover_answer(contract(TEXT), '입주일이 언제야?')
    assert '2023년 2월 19일' in answer
    assert '2025년 2월 18일' not in answer
    assert '2023년 1월 19일' not in answer
    assert '인도기한' in answer and '실제 입주' in answer


@pytest.mark.parametrize('text', ['계약일 2023년 02월 19일', '2023년 02월 30일까지 임차인에게 인도', '2023년 02월 19일까지 임차인에게 인도. 2023년 03월 19일까지 임차인에게 인도'])
def test_missing_invalid_or_conflicting_date_is_not_guessed(text):
    assert contract_handover_answer(contract(text), '입주일이 언제야?') is None


def test_document_date_question_never_asks_user_for_start_date(runtime):
    state = initial_state(); state['documents'] = [contract(TEXT)]
    runtime.evidences.return_value = ()
    message = call(state, runtime, '언제 입주해?')
    assert message['status'] == 'document_review'
    assert '2023년 2월 19일' in message['content']
    assert not message.get('followup_question')
    runtime.planner.assert_not_called()
    runtime.document.assert_not_called()
    assert state['dialogue']['active_document_id'] == 'c'


def test_unreadable_date_requests_excerpt_not_start_date(runtime):
    state=initial_state(); state['documents']=[contract('임대차계약서 판독 불가')]
    message=call(state,runtime,'입주일이 언제야?')
    assert '인도' in message['content'] and '촬영' in message['content']
    assert '계약 시작일을 알려' not in message['content']
    runtime.document.assert_not_called()
