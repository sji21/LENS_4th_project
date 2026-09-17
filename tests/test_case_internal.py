import pytest
from dataclasses import replace
from src.retrieval.case_internal import CaseInternalPolicy, CaseInternalRetriever


def chunk(cid, key, text='임대차 보증금 반환'):
    return {'chunk_id':cid, 'text':'[대법원 사건]\n'+text,
            'metadata':{'doc_type':'case', 'status':'current', 'canonical_case_key':key, 'source_url':'https://www.law.go.kr/LSW/precInfoP.do?precSeq=1'}}


class Dense:
    def __init__(self, hits):
        self.hits, self.calls = hits, []
    def search(self, query, k, where):
        self.calls.append((query,k,where))
        return self.hits[:k]


def test_duplicate_chunks_do_not_consume_second_case_slot():
    retriever = CaseInternalRetriever([chunk('a','same'),chunk('b','same'),chunk('c','other')], Dense([]),
        CaseInternalPolicy(rerank='pure_rrf'))
    assert retriever.select('보증금', [('a',.9),('b',.8),('c',.7)], 2) == [('a',.9),('c',.7)]


def test_threshold_returns_zero_one_or_two_and_keeps_explicit_k():
    retriever = CaseInternalRetriever([chunk(str(i),str(i)) for i in range(5)], Dense([]),
        CaseInternalPolicy(rerank='cross_encoder',min_relevance=.5), cross_encoder=object())
    hits = [(str(i),1/(i+1)) for i in range(5)]
    assert retriever.select('질문', hits, 2, [.1]*5) == []
    assert len(retriever.select('질문', hits, 2, [.8,.1,.1,.1,.1])) == 1
    assert len(retriever.select('질문', hits, 2, [.8]*5)) == 2
    assert len(retriever.select('질문', hits, 5, [.8]*5)) == 5


def test_empty_and_zero_do_not_call_members_or_model():
    dense = Dense([('a',1)])
    retriever = CaseInternalRetriever([chunk('a','a')], dense)
    assert retriever.search('  ',2) == []
    assert retriever.search('보증금',0) == []
    assert dense.calls == []
    with pytest.raises(ValueError): retriever.search('보증금',-1)


def test_member_depth_is_independent_of_fusion_depth_and_query_is_preserved():
    dense = Dense([('a',1)])
    policy = CaseInternalPolicy(candidate_depth=80,fusion_depth=160,rerank='pure_rrf')
    retriever = CaseInternalRetriever([chunk('a','a')],dense,policy)
    query = '원래 문맥\n보증금은 돌려받나요?'
    hits, trace = retriever.candidates(query)
    assert dense.calls[0][0:2] == (query,80)
    assert trace['query'] == query
    assert hits and trace['member_hits']['dense'] == ['a']


def test_model_errors_are_not_converted_to_irrelevant_fallback():
    class Broken:
        def score(self,*args): raise RuntimeError('model failure')
    retriever = CaseInternalRetriever([chunk('a','a')],Dense([('a',1)]),
        CaseInternalPolicy(rerank='cross_encoder'),cross_encoder=Broken())
    with pytest.raises(RuntimeError,match='model failure'):
        retriever.search('보증금',2)


def test_missing_identity_and_nonfinite_scores_fail_closed():
    retriever = CaseInternalRetriever([chunk('a','')],Dense([]),CaseInternalPolicy(rerank='pure_rrf'))
    with pytest.raises(ValueError,match='identity'): retriever.select('질문',[('a',.5)],2)
    retriever.policy = replace(retriever.policy,rerank='cross_encoder')
    with pytest.raises(ValueError,match='scores'): retriever.select('질문',[('a',.5)],2,[float('nan')])


def test_other_corpora_are_rejected():
    other = chunk('law','law'); other['metadata']['doc_type'] = 'law'
    with pytest.raises(ValueError,match='only case'):
        CaseInternalRetriever([other],Dense([]))


def test_missing_required_case_filter_metadata_fails_at_load():
    case=chunk('a','a');del case['metadata']['status']
    with pytest.raises(ValueError,match='existing case filter'):
        CaseInternalRetriever([case],Dense([]))


def test_application_profile_boundary_uses_existing_result_contract():
    from src.retrieval.case_internal_profile import AppliedCaseInternalService
    from src.retrieval.service import RetrievalResult
    backend=CaseInternalRetriever([chunk('a','a')],Dense([('a',1)]),CaseInternalPolicy(rerank='pure_rrf'))
    service=AppliedCaseInternalService(backend,{'version':'test'})
    result,trace=service.search_with_trace('보증금',k_law=0,k_case=2,k_guide=0,k_civil=0)
    assert isinstance(result,RetrievalResult)
    assert [e.chunk_id for e in result.cases]==['a']
    assert result.cases[0].text==backend.chunks['a']['text']
    assert set(service.evidence_payload(result)['channels'])=={'cases','laws','civil_laws','guides'}
    assert trace['selected']==[('a',trace['selected'][0][1])]
    assert service.search('보증금',k_law=0,k_case=-1,k_guide=0,k_civil=0).cases==[]


def test_existing_factory_dispatches_new_profile_with_product_base(monkeypatch,tmp_path):
    from src.retrieval.service import RetrievalService
    import src.retrieval.case_internal_profile as module
    profile=tmp_path/'case.json';profile.write_text('{"schema":"lens-case-internal-v1"}')
    sentinel=object();base=object();calls=[]
    def load(path,*,base_service): calls.append((path,base_service));return sentinel
    monkeypatch.setattr(RetrievalService,'_from_index_without_case_profile',classmethod(lambda cls:base))
    monkeypatch.setattr(module,'load_internal_case_profile',load)
    monkeypatch.setenv('LENS_CASE_RETRIEVAL_PROFILE',str(profile))
    assert RetrievalService.from_index() is sentinel
    assert calls==[(str(profile),base)]


def test_existing_application_channels_pass_through_unchanged():
    from src.retrieval.case_internal_profile import AppliedCaseInternalService
    from src.retrieval.service import Evidence,RetrievalResult
    law=Evidence(1,'law','law','원래 인용','원래 본문',.9,'https://example.org/original')
    class Existing:
        def search(self,question,**kwargs):
            assert kwargs=={'k_law':3,'k_case':0,'k_guide':1,'k_civil':2}
            return RetrievalResult(question=question,laws=[law],civil_laws=[law],guides=[law])
    backend=CaseInternalRetriever([chunk('a','a')],Dense([('a',1)]),CaseInternalPolicy(rerank='pure_rrf'))
    service=AppliedCaseInternalService(backend,{'version':'test'},Existing())
    result=service.search('보증금',k_law=3,k_case=2,k_guide=1,k_civil=2)
    assert result.laws[0] is law and result.civil_laws[0] is law and result.guides[0] is law
    assert len(result.cases)==1


def test_product_route_combines_existing_law_with_new_case_top2():
    from src.generation.evidence_routing import retrieve_staged
    from src.retrieval.case_internal_profile import AppliedCaseInternalService
    from src.retrieval.service import Evidence,RetrievalResult
    law=Evidence(1,'law','law','주택임대차보호법 제3조','조문 본문',.9,'https://example.org/law')
    class Existing:
        def search(self,question,**kwargs):
            return RetrievalResult(question=question,laws=[law] if kwargs['k_law'] else [])
    cases=[chunk(str(i),str(i)) for i in range(3)]
    backend=CaseInternalRetriever(cases,Dense([(str(i),1) for i in range(3)]),
                                  CaseInternalPolicy(rerank='pure_rrf'))
    service=AppliedCaseInternalService(backend,{'version':'test'},Existing())
    routed=retrieve_staged(service,'보증금 반환 관련 판례를 알려줘',k_law=3,k_case=2,k_guide=0)
    assert routed.result.laws == [law]
    assert len(routed.result.cases) == 2
    assert routed.route.cases_added
    law_only=retrieve_staged(service,'보증금 반환 법령을 알려줘',k_law=3,k_case=2,k_guide=0)
    assert law_only.result.laws == [law]
    assert law_only.result.cases == []


def test_profile_default_k_and_explicit_top2_are_separate():
    from src.retrieval.case_internal_profile import AppliedCaseInternalService
    backend=CaseInternalRetriever([chunk(str(i),str(i)) for i in range(25)],Dense([(str(i),1) for i in range(25)]),CaseInternalPolicy(rerank='pure_rrf'))
    service=AppliedCaseInternalService(backend,{'version':'test','public_default_case_k':20})
    assert len(service.search('임대차 보증금',k_law=0,k_guide=0,k_civil=0).cases)==20
    assert len(service.search('임대차 보증금',k_law=0,k_case=2,k_guide=0,k_civil=0).cases)==2


def test_internal_case_evidence_reaches_the_llm_prompt_unchanged():
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from src.generation.chain import build_qa_chain
    from src.generation.prompt import format_context
    from src.retrieval.case_internal_profile import AppliedCaseInternalService

    original = chunk('case-sealed', 'sealed-key', '배포 판례의 고유한 본문')
    backend = CaseInternalRetriever([original], Dense([('case-sealed', 1)]), CaseInternalPolicy(rerank='pure_rrf'))
    service = AppliedCaseInternalService(backend, {'version': 'sealed-test'})
    result = service.search('보증금 반환 판례', k_law=0, k_case=2, k_guide=0, k_civil=0)
    captured = []

    def generate(prompt):
        captured.extend(message.content for message in prompt.to_messages())
        return AIMessage(content='판례 근거를 확인했습니다.')

    answer = build_qa_chain(RunnableLambda(generate)).invoke({
        'context': format_context(result),
        'question': '보증금 반환 판례',
    })
    prompt_text = '\n'.join(captured)
    assert answer == '판례 근거를 확인했습니다.'
    assert '## 관련 판례' in prompt_text
    assert '배포 판례의 고유한 본문' in prompt_text


def test_continuous_official_body_preserves_conditions_and_single_case_identity():
    full='보증금을 반환한다. 다만 동시이행 조건과 예외를 확인한다.'
    parts=[]
    for cid,a,b in [('first',0,18),('second',12,len(full))]:
        c=chunk(cid,'same',full[a:b]);c['metadata'].update(source_start=a,source_end=b,
            source_relative_path='sources/same.txt',source_sha256='source-hash',case_id='same')
        parts.append(c)
    anchor=chunk('anchor','same','짧은 요약');other=chunk('other','other')
    backend=CaseInternalRetriever([anchor,other]+parts,Dense([]),
        CaseInternalPolicy(body_policy='continuous_official_case'))
    rendered,source=backend.render('anchor',1,.9)
    from src.retrieval.service import _to_evidence
    original=_to_evidence(1,anchor,.9)
    assert rendered.text=='[대법원 사건]\n'+full
    assert rendered.chunk_id=='anchor' and rendered.citation==original.citation
    assert source['source_chunk_ids']==['first','second']
    assert source['source_start']==0 and source['source_end']==len(full)
    assert backend.render('other',2,.8)[0].text==other['text']


def test_context_gaps_and_conflicting_overlap_fail_closed():
    from src.retrieval.case_internal import continuous_case_bodies
    a=chunk('a','same','abc');b=chunk('b','same','xyz')
    for c,start in ((a,0),(b,5)):
        c['metadata'].update(source_start=start,source_end=start+3,
            source_relative_path='sources/same.txt',source_sha256='hash',case_id='same')
    with pytest.raises(ValueError,match='gap'):continuous_case_bodies([a,b])
    b['metadata'].update(source_start=2,source_end=5)
    with pytest.raises(ValueError,match='overlap'):continuous_case_bodies([a,b])


def test_lexical_gate_returns_zero_one_or_two_without_dense_filling():
    chunks=[chunk('a','a','uniquealpha'),chunk('b','b','uniquebeta'),chunk('c','c','other')]
    backend=CaseInternalRetriever(chunks,Dense([('c',.9),('a',.8),('b',.7)]),
        CaseInternalPolicy(rerank='pure_rrf',require_lexical_support=True))
    assert backend.search('zzzzzz',2)==[]
    # A disjoint word isolates lexical support despite other dense candidates.
    assert [e.chunk_id for e in backend.search('other',2)]==['c']
    assert len(backend.search('uniquealpha uniquebeta',2))==2
