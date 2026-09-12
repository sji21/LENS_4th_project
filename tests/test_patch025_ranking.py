from types import SimpleNamespace
import pytest

from src.retrieval.service import RetrievalService, CIVIL, Evidence
from scripts.patch025_ranking import select


def service(dense=True):
    svc=object.__new__(RetrievalService)
    svc.civil=CIVIL
    evidence=lambda cid:Evidence(1,cid,'law','민법','본문',1.0,'')
    svc._search_civil=lambda q,t,k:[evidence('a'),evidence('b')][:k]
    svc._search_one_with_member_hits=lambda c,q,k:([evidence('c'),evidence('d')][:k], {
        '민법-bm25':['c','d'], '민법-dense':['d','c'],
    })
    members=[SimpleNamespace(name='민법-bm25',weight=1)]
    if dense: members.append(SimpleNamespace(name='민법-dense',weight=1))
    svc._retrievers={CIVIL.name:SimpleNamespace(members=members,rrf_k=5,
        last_member_hits=lambda:(_ for _ in ()).throw(AssertionError('stale ranks read')))}
    return svc


def test_only_third_result_changes_and_ranks_are_contiguous():
    result=service()._search_civil_candidates('질문',(),3)
    assert [e.chunk_id for e in result]==['a','b','d']
    assert [e.rank for e in result]==[1,2,3]


@pytest.mark.parametrize('limit', [0,1,2])
def test_smaller_limits_keep_original_results(limit):
    result=service()._search_civil_candidates('질문',(),limit)
    assert [e.chunk_id for e in result]==['a','b'][:limit]


def test_bm25_only_keeps_original_tail():
    assert [e.chunk_id for e in service(False)._search_civil_candidates('질문',(),3)]==['a','b','c']


def test_replay_preserves_top_two_and_deduplicates():
    row={'seed':['a','b'],'bm25':['c','d'],'dense':['d','c']}
    assert select(row,'baseline')==['a','b','c']
    assert select(row,'keep_top2_dense2')==['a','b','d']


def test_published_capture_and_live_results_replay():
    from scripts.patch025_ranking import ROOT, check
    result=check(ROOT/'data/eval/patch025-ranking')
    assert result['policies']['keep_top2_dense2']['lost_required']==[]
    assert result['policies']['keep_top2_dense2']['groups']['context_diagnostic']['union_all_required']['hits']==30


def test_missing_published_evidence_is_rejected_even_with_updated_manifest(tmp_path):
    import json
    from scripts.patch025_ranking import report
    (tmp_path/'manifest.json').write_text(json.dumps({}),encoding='utf-8')
    with pytest.raises(ValueError,match='Incomplete published bundle'):
        report(tmp_path)


def test_report_float_tolerance_does_not_hide_count_changes():
    from scripts.patch025_ranking import close
    assert close({'rate':0.1+0.2},{'rate':0.3})
    assert not close({'hits':29},{'hits':30})
