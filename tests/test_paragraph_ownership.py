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
@pytest.mark.parametrize('reference,status', [('제1항', 'answered'), ('제5항', 'abstained'),
    ('제1항·제5항', 'abstained'), ('제1항부터 제5항까지', 'abstained'),
    ('제1항·제2항', 'answered'), ('제1항부터 제2항까지', 'answered'),
    ('제1항, 제5항', 'abstained'), ('제1항 및 제5항', 'abstained')])
def test_pipeline_propagates_paragraph_result(entrypoint, reference, status):
    from src.generation import chain, graph
    from src.generation.llm import get_llm
    body = '① 임차인은 비용의 상환을 청구할 수 있다.\n② 다른 법 제371조 제5항을 준용한다.'
    ev = evidence(body, '민법 제626조')
    class Service:
        def search(self, question, **kwargs):
            return RetrievalResult(question, civil_laws=[ev])
    fn = chain.answer_question if entrypoint == 'chain' else graph.answer_question
    raw = f'민법 제626조 {reference}: 임차인은 비용의 상환을 청구할 수 있습니다.'
    answer = fn('임차인이 수리비 상환을 청구할 수 있나요?', service=Service(),
                llm=get_llm(fake_responses=[raw, 'PASS']))
    assert answer.status == status
    if status == 'abstained':
        assert 'paragraph' in {issue.kind for issue in _paragraph_issues(
            Answer('질문', 'answered', '', raw_text=raw, civil_laws=(ev,)))}


@pytest.mark.parametrize('suffix', ['·제{n}항', ', 제{n}항', ' 및 제{n}항',
    '부터 제{n}항까지', ' 내지 제{n}항', '~제{n}항', ', 및 제{n}항'])
@pytest.mark.parametrize('last,valid', [(2, True), (5, False)])
def test_all_list_and_range_paragraphs_are_checked(suffix, last, valid):
    raw = '민법 제626조 제1항' + suffix.format(n=last)
    ev = evidence('① 첫 항\n② 둘째 항', '민법 제626조')
    answer = Answer('질문', 'answered', '', raw_text=raw, civil_laws=(ev,))
    assert (not _paragraph_issues(answer)) == valid


@pytest.mark.parametrize('note', [
    '[제2조에서 이동, 종전 제8조는 제15조로 이동 <2013. 12. 30.>]',
    '[전문개정 2013. 12. 30.]', '[본조신설 2013. 12. 30.]'])
def test_history_notes_preserve_real_paragraphs(note):
    ev = evidence('[주택임대차보호법 시행령 제8조]\n① 첫 항\n② 둘째 항\n' + note,
                  '주택임대차보호법 시행령 제8조')
    answer = Answer('질문', 'answered', '', raw_text='주택임대차보호법 시행령 제8조 제1항 및 제2항', laws=(ev,))
    assert not _paragraph_issues(answer)


@pytest.mark.parametrize('note', ['[다른법 제8조]', '[제8조(다른 조문)]',
    '[제2조에서 이동, 다른법 제5조 제1항]', '[알 수 없는 주석]'])
def test_history_exception_does_not_allow_other_headers(note):
    ev = evidence('① 첫 항\n' + note + '\n② 다른 항')
    answer = Answer('질문', 'answered', '', raw_text='검증용법 제3조의3 제2항', laws=(ev,))
    assert _paragraph_issues(answer)


def test_range_checks_missing_interior(monkeypatch):
    from src.generation import validation
    monkeypatch.setattr(validation, 'evidence_paragraphs', lambda ev, identity: {1, 3})
    ev = evidence('synthetic', '민법 제626조')
    answer = Answer('질문', 'answered', '', raw_text='민법 제626조 제1항부터 제3항까지', laws=(ev,))
    assert _paragraph_issues(answer)


@pytest.mark.parametrize('reference', ['제3항부터 제1항까지', '제1항부터 제999999999항까지'])
def test_invalid_ranges_are_unverifiable(reference):
    answer = Answer('질문', 'answered', '', raw_text='검증용법 제3조의3 ' + reference,
                    laws=(evidence('① 첫 항\n② 둘째 항\n③ 셋째 항'),))
    assert _paragraph_issues(answer)
