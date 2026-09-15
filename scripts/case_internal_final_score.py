"""Score independently reviewed actual returned bodies after raw-result sealing."""
import argparse,json,hashlib,statistics
from fractions import Fraction
from pathlib import Path
from case_internal_run import sha,write
from src.evaluation.case_internal import capacity


def calculate(items):
    rows=[]
    for item in items:
        groups=item['groups'] if item['in_R'] else []
        available=[g for g in groups if g['corpus_supported']]
        success=[g for g in available if g['primary_supported'] and item['execution_status']=='ok']
        ranks=[max(g['primary_supporting_return_ranks']) for g in success]
        rows.append({'qid':item['qid'],'P':item['in_P'],'U':item['in_U'],'R':item['in_R'],
            'E':bool(available),'G':len(groups),'C':len(available),'recovered':len(success),
            'hit':bool(success),'mrr':1/min(ranks) if ranks else 0,
            'all_groups':len(success)==len(available) if available else None,
            'returned':item['raw_return_count'],'execution_status':item['execution_status'],
            'relevant_returns':sum(r['relevance']=='관련' for r in item['returns']),
            'unverified_returns':sum(r['relevance']=='확인불가' for r in item['returns']),
            'two_case_identity_capacity':capacity([g['available_keys'] for g in groups])})
    eligible=[r for r in rows if r['E']];confirmed=[r for r in rows if r['R']]
    P=sum(r['P'] for r in rows);U=sum(r['U'] for r in rows);R=len(confirmed);E=len(eligible)
    G=sum(r['G'] for r in rows);C=sum(r['C'] for r in rows);returned=sum(r['returned'] for r in rows)
    unique=set(k for i in items if i['in_R'] for g in i['groups'] if g['corpus_supported'] for k in g['available_keys'])
    ratio=lambda a,b:float(Fraction(a,b)) if b else None
    summary={'sample':len(items),'P':P,'U':U,'R':R,'E':E,'G':G,'C':C,
        'unique_available_gold_cases':len(unique),'R_over_P':ratio(R,P),
        'deferred_ratio':ratio(P-R+U,P+U),'corpus_group_coverage':ratio(C,G),
        'hit_at_2':ratio(sum(r['hit'] for r in eligible),E),
        'macro_group_recall_at_2':float(sum(Fraction(r['recovered'],r['C']) for r in eligible)/E) if E else None,
        'overall_micro_group_recall':ratio(sum(r['recovered'] for r in rows),G),
        'overall_macro_group_recall':float(sum(Fraction(r['recovered'],r['G']) for r in confirmed)/R) if R else None,
        'mrr_at_2':statistics.mean(r['mrr'] for r in eligible) if E else None,
        'all_groups_at_2':ratio(sum(r['all_groups'] for r in eligible),E),
        'returned':returned,'official_relevance_ratio':ratio(sum(r['relevant_returns'] for r in rows),returned),
        'unverified_returns':sum(r['unverified_returns'] for r in rows),
        'errors':sum(r['execution_status']!='ok' for r in rows),
        'timeouts':sum(r['execution_status']=='timeout' for r in rows),
        'eligible_empty_returns':sum(r['returned']==0 for r in eligible)}
    # A separate direct pass over reviewer judgments, not the derived rows.
    numerator=0;recall=Fraction();denominator=0
    for item in items:
        if not item['in_R']:continue
        present=[g for g in item['groups'] if g['corpus_supported']]
        if not present:continue
        denominator+=1;found=0
        if item['execution_status']=='ok':
            for g in present:
                if g['primary_supported']:found+=1
        numerator+=found>0;recall+=Fraction(found,len(present))
    if denominator:
        assert summary['hit_at_2']==float(Fraction(numerator,denominator))
        assert summary['macro_group_recall_at_2']==float(recall/denominator)
    summary['independent_recalculation_pass']=True
    return rows,summary


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--raw',type=Path,required=True)
    ap.add_argument('--membership',type=Path,required=True);ap.add_argument('--review',type=Path,required=True)
    ap.add_argument('--prereg',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    prereg=json.loads(args.prereg.read_text('utf-8'));review=json.loads(args.review.read_text('utf-8'))
    import jsonschema
    jsonschema.Draft202012Validator(prereg['result_json_schema']).validate(review)
    assert review['prereg_sha256']==sha(args.prereg)
    assert review['membership_sha256']==sha(args.membership) and review['raw_results_sha256']==sha(args.raw)
    seal=json.loads((args.raw.parent/'RESULT_SEAL.json').read_text('utf-8'))
    assert seal[args.raw.name]==sha(args.raw),'raw output not sealed before scoring'
    raw={r['qid']:r for r in [json.loads(l) for l in args.raw.read_text('utf-8').splitlines()]}
    membership=json.loads(args.membership.read_text('utf-8'));members={i['qid']:i for i in membership['items']}
    assert len(review['items'])==len(raw)==len(members)==100
    assert {i['qid'] for i in review['items']}==set(raw)==set(members)
    overflows=duplicates=unresolved=0
    for item in review['items']:
        result=raw[item['qid']];member=members[item['qid']];cases=result['cases']
        assert item['question_sha256']==hashlib.sha256(result['query'].encode()).hexdigest()
        assert item['raw_return_count']==len(cases)==len(item['returns'])
        keys=[e['canonical_case_key'] for e in cases]
        overflows+=len(cases)>2;duplicates+=len(keys)!=len(set(keys))
        for rank,(e,check) in enumerate(zip(cases,item['returns'])):
            assert check['canonical_case_key']==e['canonical_case_key']
            body_sources=result.get('trace',{}).get('body_sources',[])
            source_ids=body_sources[rank]['source_chunk_ids'] if rank<len(body_sources) else [e['chunk_id']]
            assert check['chunk_ids'] in ([e['chunk_id']],source_ids)
            assert check['text_sha256']==hashlib.sha256(e['text'].encode()).hexdigest()
        if result['error']:
            assert item['execution_status']==('timeout' if result['error']['type']=='TimeoutError' else 'error')
        else:assert item['execution_status']=='ok'
        assert not (item['in_P'] and item['in_U'])
        assert not item['in_R'] or item['in_P']
        goldgroups={g['group_id']:g for g in member['groups']}
        assert len(goldgroups)==len(item['groups'])
        assert set(goldgroups)=={g['group_id'] for g in item['groups']}
        for group in item['groups']:
            expected=goldgroups[group['group_id']]['available_keys']
            assert group['available_keys']==expected and group['corpus_supported']==bool(expected)
            unresolved+=group['review_status']=='unresolved'
            if group['primary_supported']:
                assert group['semantic_supported'] and group['identity_retrieved'] and expected
                ranks=group['primary_supporting_return_ranks'];assert ranks
                assert all(1<=r<=min(2,len(cases)) and cases[r-1]['canonical_case_key'] in expected for r in ranks)
                assert item['execution_status']=='ok'
                assert all(r['status'] not in ('unsupported','uncertain') for r in group['requirements'] if r['kind'] in ('필수명제','적용조건','예외'))
        for flag in ('in_P','in_U','in_R','in_E'):
            if flag in member:assert item[flag]==member[flag]
        assert item['in_E']==(item['in_R'] and any(g['corpus_supported'] for g in item['groups']))
    rows,summary=calculate(review['items'])
    summary.update(overflows=overflows,duplicates=duplicates,unresolved_body_reviews=unresolved,
        metric_kind='Primary: actual returned bodies support presealed permitted Gold propositions and conditions; identity-only recall is separate.',
        raw_results_sha256=sha(args.raw),membership_sha256=sha(args.membership),review_sha256=sha(args.review))
    write(args.output/'primary_rows.json',rows);write(args.output/'primary_metrics.json',summary)
    write(args.output/'SEAL.json',{p.name:sha(p) for p in args.output.iterdir() if p.is_file()})
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
