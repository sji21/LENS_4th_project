from copy import deepcopy
import re

import pytest

from scripts.patch027_tuning import concepts,dense_query,GENERAL,CIVILS,general_select,civil_select,configure_expansion


@pytest.mark.parametrize('query,phrase',[
    ('형제 둘이 지분을 나눠 갖는 집의 임대는 누구와 의논하나요?','공유물의 관리'),
    ('반송된 서류는 다시 어떻게 보내나요?','공시송달'),
    ('등기우편을 부쳤는데 수취인이 없다며 돌아왔습니다.','공시송달'),
    ('이삿짐을 빼기 전에 보증금을 받을 수 있나요?','동시이행'),
    ('전세집 도배 비용이 다툼이 됐습니다.','원상회복의무'),
])
def test_concepts_generalize_without_article_ids(query,phrase):
    result=concepts(query,'civil')
    assert any(phrase in term for term in result)
    assert not any(re.search(r'DEV-|CIV-|제\d+조',term) for term in result)
    assert dense_query(query,'civil').startswith(query+'\n')


@pytest.mark.parametrize('query',['앱 공유 버튼을 못 찾겠어요.','우리 동네 우편번호가 궁금해요.','좋은 아침입니다.','휴가를 마치고 집으로 돌아왔어요.'])
def test_no_matching_concept_keeps_dense_query_unchanged(query):
    assert concepts(query,'civil')==[]
    assert dense_query(query,'civil')==query


def make_row():
    members={'bm25_original':['a','b','c','d','e'], 'dense_original':['a','b','d','c','e'],
             'bm25_expanded':['x','a','b','c','d','e'],'dense_expanded':['x','y','a','b','c','d']}
    return {'current_laws':['a','b','c','d','e'],'current_civil':['a','b','c'],
            'core':['a','b','c','d','e'],'civil_seed':['a','b'],
            'general':deepcopy(members),'civil':deepcopy(members)}


@pytest.mark.parametrize('channel,policies,selector,limit',[
    ('general',GENERAL,general_select,5),('civil',CIVILS,civil_select,3)])
def test_selection_is_bounded_and_does_not_read_question_ids(channel,policies,selector,limit):
    r=make_row()
    for p in policies:
        result=selector(r,p)
        assert len(result)==len(set(result))==limit
        assert result==selector({**r,'qid':'DIFFERENT','gold':['unrelated']},p)
    assert general_select(r,'core3_blend')[:3]==r['core'][:3]
    with pytest.raises(ValueError):selector(r,'invalid')


def test_expansion_does_not_mutate_baseline_or_other_channels():
    from dataclasses import replace
    from src.retrieval.service import RetrievalService,CIVIL
    from src.retrieval.terms import expand,expand_law,expand_civil
    def chunk(cid,title,kind):
        return {'chunk_id':cid,'text':'공유물 보존',
                'metadata':{'title':title,'doc_type':kind,'article_id':cid,'status':'current'}}
    chunks=[chunk('h','주택임대차보호법','law'),chunk('p','주민등록법','law'),
            chunk('c','민법','law'),chunk('case','판례','case'),chunk('guide','안내','guide')]
    base=RetrievalService(chunks,civil=replace(CIVIL,include_ids=('c',)))
    tuned=RetrievalService(chunks,civil=replace(CIVIL,include_ids=('c',)))
    configure_expansion(tuned)
    for member in base._retrievers['법령'].members[0].retriever.partitions.values():assert member.query_expander is expand_law
    assert base._retrievers['민법'].members[0].retriever.query_expander is expand_civil
    for name in ('판례','안내'):assert tuned._retrievers[name].members[0].retriever.query_expander is expand
    for member in tuned._retrievers['법령'].members[0].retriever.partitions.values():
        assert '미납국세 미납지방세 열람 납세증명' in member.query_expander('체납 확인')


def test_frozen_results_replay_with_improvement_and_remaining_regressions():
    from scripts.patch027_tuning import check
    r=check()
    assert r['civil']['both']['groups']['question_only']['channel_metrics']['civil']['all_target_items']['hit@3']==23/32
    assert len({a for x in r['civil']['both']['new_required_civil_hits'] for a in x['anchors']})==8
    assert r['general']['core3_blend']['top3_general_loss_inputs']==0
    assert not any(x['adoption']['passed'] for ps in r.values() for x in ps.values())


def test_no_expansion_keeps_both_members_and_default_selection_unchanged():
    from scripts.patch027_tuning import BUNDLE
    from scripts.patch015_baseline import read
    for r in read(BUNDLE/'traces.json'):
        for channel,selector,key in (('general',general_select,'current_laws'),('civil',civil_select,'current_civil')):
            if not r['expansion'][channel]:
                assert r[channel]['bm25_expanded']==r[channel]['bm25_original']
                assert r[channel]['dense_expanded']==r[channel]['dense_original']
                assert selector(r,'both')==r[key]


@pytest.fixture
def bundle(tmp_path):
    from shutil import copytree
    from scripts.patch027_tuning import BUNDLE
    copytree(BUNDLE,tmp_path/'bundle')
    return tmp_path/'bundle'


def refresh(bundle,trace=False):
    from scripts.patch015_baseline import read,write,sha
    if trace:
        a=read(bundle/'audit.json');a['traces_sha256']=sha(bundle/'traces.json');write(bundle/'audit.json',a)
    m=read(bundle/'manifest.json');write(bundle/'manifest.json',{p:sha(bundle/p) for p in m})


def test_missing_capture_and_manifest_entry_rejected(bundle):
    from scripts.patch015_baseline import read,write
    from scripts.patch027_tuning import check
    (bundle/'traces.json').unlink();m=read(bundle/'manifest.json');del m['traces.json'];write(bundle/'manifest.json',m)
    with pytest.raises(ValueError,match='Incomplete'):check(bundle)


@pytest.mark.parametrize('mutation',['missing_input','expansion','wrong_channel','source','score'])
def test_semantic_corruption_rejected_after_refreshing_hashes(bundle,mutation):
    from scripts.patch015_baseline import read,write
    from scripts.patch027_tuning import check,PREVIOUS
    if mutation in ('missing_input','expansion','wrong_channel'):
        rows=read(bundle/'traces.json')
        if mutation=='missing_input':rows.pop()
        if mutation=='expansion':rows[0]['expansion']['civil']=['제999조']
        if mutation=='wrong_channel':rows[0]['civil']['bm25_expanded']=[read(PREVIOUS/'audit.json')['core_ids'][0]]
        write(bundle/'traces.json',rows);refresh(bundle,trace=True)
    elif mutation=='source':
        a=read(bundle/'audit.json');a['script_sha256']='0'*64;write(bundle/'audit.json',a);refresh(bundle)
    else:
        r=read(bundle/'comparison.json');r['civil']['both']['adoption']['passed']=True
        write(bundle/'comparison.json',r);refresh(bundle)
    with pytest.raises(ValueError):check(bundle)
