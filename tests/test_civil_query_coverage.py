"""Expense ownership and housing-use boundaries for the new civil routes."""
import json
from pathlib import Path

import pytest

from src.retrieval.service import detect_civil_topics


def articles(question):
    return [topic.article_id for topic in detect_civil_topics(question)]


@pytest.mark.parametrize('qid,article', [
    ('DEV-057', '민법-제626조'), ('DEV-058', '민법-제627조'),
    ('DEV-059', '민법-제626조'),
])
def test_development_context_routes(qid, article):
    path = Path(__file__).resolve().parents[1] / 'data/eval/dev100/questions.json'
    item = next(row for row in json.loads(path.read_text(encoding='utf-8')) if row['qid'] == qid)
    assert article in articles(item['modes']['context_diagnostic']['query'])


@pytest.mark.parametrize('question', [
    '잠금장치가 고장 나서 제가 교체했어요. 비용을 돌려달라고 하려면 영수증이 필요한가요?',
    '보일러 수리를 마쳤어요. 수리비를 돌려달라고 할 수 있나요?',
    '수리하고 20만 원을 냈는데요. 먼저 낸 20만 원은 누구에게 얘기해야 하나요?',
])
def test_repair_reimbursement_variants(question):
    assert '민법-제626조' in articles(question)


@pytest.mark.parametrize('question', [
    '보일러 수리는 집주인이 끝냈어요. 계약금을 냈습니다. 비용을 돌려달라고 할 수 있나요?',
    '보일러 수리를 마쳤어요. 보증금을 돌려달라고 할 수 있나요?',
    '수리하고 20만 원을 냈어요. 계약금도 냈어요. 먼저 낸 20만 원은 누구에게 얘기해야 하나요?',
    '잠금장치가 고장 났어요. 교체하지 않았고 돈도 안 냈어요. 비용을 돌려달라고 하면 되나요?',
    '보일러 수리를 마쳤어요. 비용을 돌려달라는 요청은 하지 않았어요.',
    '기사님이 윗집 문제래요. 먼저 낸 38만 원은 누구한테 얘기해야 하나요?',
])
def test_unrelated_ambiguous_or_missing_repair_payment(question):
    assert '민법-제626조' not in articles(question)


@pytest.mark.parametrize('question', [
    '배관 공사 때문에 집에서 물을 못 쓴대요. 월세는 어떻게 하나요?',
    '단수로 물을 사용할 수 없어요. 월세를 줄일 수 있나요?',
])
def test_water_outage_prevents_housing_use(question):
    assert '민법-제627조' in articles(question)


@pytest.mark.parametrize('question', [
    '내일 배관 공사로 단수된대요. 안내문은 어디서 확인하나요?',
    '배관 공사는 끝났어요. 여행 숙박비를 받을 수 있나요?',
    '단수 공지 때문에 못 쓴 안내문을 다시 작성하려고요.',
    '단수지만 물을 못 쓰는 건 아니에요. 월세는 그대로 내나요?',
    '단수 때문에 숙박비를 썼어요. 숙박비는 누가 부담하나요?',
])
def test_outage_word_alone_does_not_route_to_rent_reduction(question):
    assert '민법-제627조' not in articles(question)
