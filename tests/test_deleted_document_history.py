from types import SimpleNamespace
from unittest.mock import Mock

from chat.services import initial_state, simplify_message
from chat.dialogue_contract import build_decision_input
from cases.services.reports import _source_snapshot
from cases.services.conversation_guidance import conversation_messages


def history():
    return [
        {'id':'old-u','role':'user','content':'삭제한 문서의 금액','context_excluded':True},
        {'id':'old-a','role':'assistant','status':'answered','content':'삭제된 문서 답변','context_excluded':True,'context_content':'삭제된 원문'},
        {'id':'new-u','role':'user','content':'일반적인 임대차 질문'},
    ]


def test_deleted_document_messages_remain_visible_but_not_planner_history():
    state = initial_state()
    state['messages'] = history()
    payload = build_decision_input(state, '계속 설명해줘')
    assert [m['content'] for m in payload['history']] == ['일반적인 임대차 질문']
    assert len(state['messages']) == 3
    assert simplify_message(state, 'old-a') is None


def test_new_reports_and_guidance_exclude_deleted_document_history():
    case = SimpleNamespace(pk='case', title='case', facts=Mock())
    case.facts.exclude.return_value = []
    messages = history()
    snapshot = _source_snapshot(case, [SimpleNamespace(state={'messages':messages})])
    assert snapshot['dialogue'] == [{'role':'user','content':'일반적인 임대차 질문'}]
    assert [m['id'] for m in conversation_messages(case, messages)] == ['new-u']
