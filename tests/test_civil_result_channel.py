import pytest
from src.retrieval.service import Evidence, RetrievalResult, RetrievalService
from src.generation.models import Answer
from src.generation.evidence_routing import retrieve_staged
from src.generation.prompt import format_context


def civil():
    return Evidence(1,'civil623','law','민법 제623조(임대인의 의무)',
        '[민법 제623조(임대인의 의무)] 임대인은 목적물을 사용, 수익에 필요한 상태로 유지할 의무가 있다.',
        0.1,'https://www.law.go.kr/법령/민법/제623조')


class CivilOnlyService:
    def __init__(self):self.calls=[]
    def search(self,question,k_law=5,k_case=5,k_guide=2):
        self.calls.append((k_law,k_case,k_guide))
        return RetrievalResult(question,civil_laws=[civil()] if k_law else [],civil_topics=('수선의무',))


def test_civil_only_is_not_empty_and_has_separate_prompt_sources():
    result=RetrievalResult('질문',civil_laws=[civil()])
    assert not result.is_empty()
    context=format_context(result)
    assert '## 관련 민법 후보' in context
    assert '- 관련 민법 후보: 민법 제623조' in context
    assert result.evidences==[civil()]


@pytest.mark.parametrize('question,call_count', [('수선 의무가 있나요?',1),('판례도 알려주세요',2)])
def test_staged_retrieval_preserves_civil_and_topics(question,call_count):
    service=CivilOnlyService()
    routed=retrieve_staged(service,question,k_law=3,k_case=5,k_guide=2)
    assert routed.result.civil_laws==[civil()]
    assert routed.result.civil_topics==('수선의무',)
    assert len(service.calls)==call_count


def test_answer_sources_include_civil_law():
    answer=Answer('질문','answered','본문',civil_laws=(civil(),))
    assert answer.evidences==(civil(),)
    assert answer.sources()[0]['label']=='민법 제623조(임대인의 의무)'


def test_empty_query_and_missing_civil_data_return_empty():
    service=RetrievalService([])
    assert service.search(' ',k_civil=3).civil_laws==[]
    assert service.search('보일러 수리',k_civil=3).civil_laws==[]


@pytest.mark.parametrize('entrypoint',['chain','graph'])
def test_civil_survives_answer_path(entrypoint):
    from src.generation import chain,graph
    from src.generation.llm import get_llm
    fn=chain.answer_question if entrypoint=='chain' else graph.answer_question
    answer=fn('보일러가 고장 났는데 임대인에게 수리를 요구할 수 있나요?',
        service=CivilOnlyService(),llm=get_llm(fake_responses=[
            '민법 제623조에 따르면 임대인은 목적물을 사용, 수익에 필요한 상태로 유지할 의무가 있습니다.',
            'PASS']))
    assert answer.civil_laws==(civil(),)
    assert civil() in answer.evidences
    assert answer.sources()[0]['label']==civil().citation
