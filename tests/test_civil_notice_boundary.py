"""Money nouns must not activate the crack-notification route."""
import pytest
from src.retrieval.service import detect_civil_topics


@pytest.mark.parametrize('money', ['보증금이', '계약금이', '잔금이', '지원금이'])
@pytest.mark.parametrize('notice', ['어떤 절차인지 알려주세요', '사고 통지 여부를 확인하려고 합니다'])
def test_money_is_not_a_crack(money, notice):
    assert not detect_civil_topics(f'{money} 아직 지급되지 않았습니다. {notice}')


@pytest.mark.parametrize('question', [
    '벽에 금이 갔는데 집주인에게 알려야 하나요?',
    '금이 생겼는데 임대인에게 통지해야 하나요?',
    '보증금이 남아 있지만 벽에 금이 갔어요. 집주인에게 알려야 하나요?',
])
def test_actual_crack_keeps_notification_route(question):
    assert any(t.article_id == '민법-제634조' for t in detect_civil_topics(question))
