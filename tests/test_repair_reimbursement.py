"""Repair reimbursement requires repair context and an expense-reclaim intent."""
import pytest
from src.retrieval.service import detect_civil_topics


@pytest.mark.parametrize('question', [
    '새벽에 천장에서 물이 계속 새서 기사 불러서 일단 수리하고 38만 원 냈어요. 집주인은 연락이 안 됐는데 이 돈 달라고 할 수 있나요?',
    '보일러를 고치고 20만 원을 냈습니다. 집주인에게 이 돈을 달라고 해도 되나요?',
    '싱크대 수리를 맡기고 돈을 냈는데 이 돈 받을 수 있을까요?',
    '변기가 고장 나서 수리비를 결제했어요. 수리비를 달라고 하고 싶어요.',
    '난방을 고치고 비용을 지불했어요. 수선비를 달라고 해도 되나요?',
])
def test_paid_repair_reclaim_routes_to_reimbursement(question):
    assert [t.article_id for t in detect_civil_topics(question)][:2] == ['민법-제626조', '민법-제623조']


@pytest.mark.parametrize('question', [
    '계약금 38만 원 냈어요. 이 돈 달라고 할 수 있나요?',
    '보증금을 냈는데 이 돈을 달라고 할 수 있나요?',
    '수리하고 38만 원 냈어요. 집주인에게 고장 사실을 알려야 하나요?',
    '보일러 수리하고 돈을 냈습니다. 앞으로 관리는 어떻게 하나요?',
    '보일러 고장 때문에 이 돈을 달라고 하는데 아직 결제하지 않았어요.',
    '수리비 고지서를 받았어요. 납부 기한이 언제인가요?',
])
def test_payment_or_money_alone_does_not_add_reimbursement(question):
    assert '민법-제626조' not in [t.article_id for t in detect_civil_topics(question)]


@pytest.mark.parametrize('money', ['계약금', '보증금', '예약금', '관리비', '중개수수료'])
def test_unrelated_payment_after_landlord_repair_does_not_add_reimbursement(money):
    question = f'보일러 수리는 집주인이 끝냈어요. 저는 {money}을 냈습니다. 계약이 취소됐는데 이 돈 달라고 할 수 있나요?'
    assert '민법-제626조' not in [t.article_id for t in detect_civil_topics(question)]


def test_review_counterexample_does_not_add_reimbursement():
    question = '보일러 수리는 집주인이 끝냈어요. 저는 계약금을 냈습니다. 계약이 취소됐는데 이 돈 달라고 할 수 있나요?'
    assert '민법-제626조' not in [t.article_id for t in detect_civil_topics(question)]


@pytest.mark.parametrize('question', [
    '보일러 수리는 끝났고 계약금을 냈습니다. 이 돈을 달라고 해도 되나요?',
    '보일러 수리하고 비용을 냈습니다. 계약금도 냈어요. 이 돈을 달라고 해도 되나요?',
    '보일러 수리를 끝냈어요. 이 돈 달라고 해도 되나요?',
])
def test_ambiguous_or_non_payment_context_does_not_add_reimbursement(question):
    assert '민법-제626조' not in [t.article_id for t in detect_civil_topics(question)]


def test_explicit_repair_cost_request_with_separate_deposit_payment():
    question = '보증금을 냈습니다. 보일러를 수리하고 비용을 결제했어요. 수리비를 달라고 해도 되나요?'
    assert [t.article_id for t in detect_civil_topics(question)][:2] == ['민법-제626조', '민법-제623조']
