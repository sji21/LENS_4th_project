import pytest

from src.generation.models import Answer
from src.generation.validation import _paragraph_issues
from src.retrieval.service import Evidence, RetrievalResult


def evidence(body, citation='검증용법 제3조의3'):
    return Evidence(1, 'synthetic', 'law', citation, body, 1.0)


@pytest.mark.parametrize('body,claim,citation,valid', [
    ('① 본문', '검증용법 제3조의3 제1항', '검증용법 제3조의3', True),
    ('① 본문', '검증용법 제3조의3제1항', '검증용법 제3조의3', True),
    ('① 본문', '검증용법 제3조의3 제5항', '검증용법 제3조의3', False),
    ('① 본문\n② 다른 법 제371조 제5항을 준용한다.', '검증용법 제3조의3 제5항', '검증용법 제3조의3', False),
    ('다른법 제3조의3 제5항을 준용한다.', '검증용법 제3조의3 제5항', '검증용법 제3조의3', False),
    ('① 본문', '다른법 제3조의3 제1항', '검증용법 제3조의3', False),
    ('① 본문', '검증용법 제3조 제1항', '검증용법 제3조의3', False),
    ('① 본문', '검증용법 제3조 제1항', '검증용법 제30조', False),
    ('① 본문\n“인용문\n⑤ 다른 본문”', '검증용법 제3조의3 제5항', '검증용법 제3조의3', False),
    ('① 본문\n② "인용\n③ 다른 내용"', '검증용법 제3조의3 제3항', '검증용법 제3조의3', False),
    ('① 본문\n> ② 인용문', '검증용법 제3조의3 제2항', '검증용법 제3조의3', False),
    ('① 본문\n```\n② 인용문\n```', '검증용법 제3조의3 제2항', '검증용법 제3조의3', False),
    ('일반 본문', '검증용법 제3조의3 제1항', '검증용법 제3조의3', False),
    ('① 본문', '제3조의3 제1항', '검증용법 제3조의3', False),
    ('[다른법 제3조의3]\n① 본문', '검증용법 제3조의3 제1항', '검증용법 제3조의3', False),
    ('[검증용법 제3조의3] ① 본문\n② 다음', '검증용법 제3조의3 제2항', '검증용법 제3조의3', True),
    ('① 본문\n제4조(다른 조)\n② 다른 항', '검증용법 제3조의3 제2항', '검증용법 제3조의3', False),
    ('① 본문\n다른법 제4조\n② 다른 항', '검증용법 제3조의3 제2항', '검증용법 제3조의3', False),
])
def test_paragraph_ownership(body, claim, citation, valid):
    answer = Answer('질문', 'answered', claim, raw_text=claim, laws=(evidence(body, citation),))
    assert (not _paragraph_issues(answer)) == valid


def test_civil_channel_uses_its_own_supplied_text_only():
    ev = evidence('① 임차인은 비용을 청구할 수 있다.', '민법 제626조')
    answer = Answer('질문', 'answered', '', raw_text='민법 제626조 제1항', civil_laws=(ev,))
    assert not _paragraph_issues(answer)
    # No DB/other-version structure is used to fill missing numbering.
    plain = evidence('번호 없는 본문', '민법 제626조')
    answer = Answer('질문', 'answered', '', raw_text='민법 제626조 제1항', civil_laws=(plain,))
    assert _paragraph_issues(answer)


@pytest.mark.parametrize('entrypoint', ['chain', 'graph'])
@pytest.mark.parametrize('paragraph,status', [(1, 'answered'), (5, 'abstained')])
def test_pipeline_propagates_paragraph_result(entrypoint, paragraph, status):
    from src.generation import chain, graph
    from src.generation.llm import get_llm
    body = '① 임차인은 비용의 상환을 청구할 수 있다.\n② 다른 법 제371조 제5항을 준용한다.'
    ev = evidence(body, '민법 제626조')
    class Service:
        def search(self, question, **kwargs):
            return RetrievalResult(question, civil_laws=[ev])
    fn = chain.answer_question if entrypoint == 'chain' else graph.answer_question
    raw = f'민법 제626조 제{paragraph}항에 따르면 임차인은 비용의 상환을 청구할 수 있습니다.'
    answer = fn('임차인이 수리비 상환을 청구할 수 있나요?', service=Service(),
                llm=get_llm(fake_responses=[raw, 'PASS']))
    assert answer.status == status
    if paragraph == 5:
        assert 'paragraph' in {issue.kind for issue in _paragraph_issues(
            Answer('질문', 'answered', '', raw_text=raw, civil_laws=(ev,)))}
