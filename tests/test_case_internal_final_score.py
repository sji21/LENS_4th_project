"""Protect final denominators and body support against identity-only success."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from case_internal_final_score import calculate


def item(qid,groups,*,error=False,returned=2):
    return {'qid':qid,'in_P':True,'in_U':False,'in_R':True,'groups':groups,
        'raw_return_count':returned,'execution_status':'error' if error else 'ok',
        'returns':[{'relevance':'관련'} for _ in range(returned)]}


def group(key,success=False):
    return {'corpus_supported':key is not None,'available_keys':[key] if key else [],
        'identity_retrieved':True,'primary_supported':success,'primary_supporting_return_ranks':[2] if success else []}


def test_body_failure_is_not_identity_success_and_corpus_gap_keeps_denominator():
    rows,s=calculate([item('a',[group('a',True),group('b')]),item('b',[group(None)])])
    assert (s['P'],s['R'],s['E'],s['G'],s['C'])==(2,2,1,3,2)
    assert s['hit_at_2']==1 and s['macro_group_recall_at_2']==.5
    assert s['overall_micro_group_recall']==1/3 and s['mrr_at_2']==.5


def test_errors_and_empty_returns_never_pass():
    _,s=calculate([item('a',[group('a',True)],error=True,returned=0),item('b',[group('b')],returned=0)])
    assert s['hit_at_2']==0 and s['macro_group_recall_at_2']==0
    assert s['official_relevance_ratio'] is None and s['eligible_empty_returns']==2
    assert s['errors']==1
