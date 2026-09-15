"""Group scoring over sealed case results; never imported by retrieval code."""
import itertools
import statistics


def capacity(groups):
    present=[set(g) for g in groups if g]
    universe=sorted(set().union(*present)) if present else []
    choices=[()] + [(k,) for k in universe] + list(itertools.combinations(universe,2))
    scored=[(sum(bool(set(c)&g) for g in present),c) for c in choices]
    best=max((n for n,_ in scored),default=0)
    return {'available_groups':len(present),'max_supported_by_two':best,
        'all_groups_fit_two':best==len(groups),'alternative_combinations':[list(c) for n,c in scored if n==best]}


def score(items,results,k=2):
    byid={r['qid']:r for r in results}
    if len(byid)!=len(results):raise ValueError('duplicate result IDs')
    if set(byid)!={g['qid'] for g in items}:raise ValueError('question/result IDs differ')
    rows=[]
    for item in items:
        r=byid[item['qid']];cases=r.get('cases',r.get('channels',{}).get('cases',[]))
        keys=[e['canonical_case_key'] for e in cases[:k]] if not r.get('error') else []
        groups=[set(g['available_keys']) for g in item['groups']]
        available=[g for g in groups if g]
        recalled=sum(bool(g.intersection(keys)) for g in available)
        union=set().union(*available) if available else set()
        first=next((rank for rank,key in enumerate(keys,1) if key in union),None)
        trace=r.get('trace',{})
        rows.append({'qid':item['qid'],'groups':len(groups),'available_groups':len(available),
            'recalled_groups':recalled,'eligible':bool(available),'hit':int(recalled>0),
            'macro_recall':recalled/len(available) if available else None,
            'overall_recall':recalled/len(groups) if groups else None,
            'all_groups':int(recalled==len(available)) if available else None,
            'mrr':1/first if first else 0,'returned':len(cases),'overflow':len(cases)>k,
            'duplicate_cases':len(keys)!=len(set(keys)),'error':r.get('error'),
            'latency_seconds':r.get('latency_seconds'),'two_case_capacity':capacity(groups)})
    eligible=[r for r in rows if r['eligible']];confirmed=[r for r in rows if r['groups']]
    mean=lambda values:statistics.mean(values) if values else None
    c=sum(r['available_groups'] for r in rows);g=sum(r['groups'] for r in rows)
    summary={'processed':len(rows),'E':len(eligible),'R_with_groups':len(confirmed),'G':g,'C':c,
        'corpus_group_coverage':c/g if g else None,'hit_at_2':mean([r['hit'] for r in eligible]),
        'macro_group_recall_at_2':mean([r['macro_recall'] for r in eligible]),
        'overall_macro_group_recall':mean([r['overall_recall'] for r in confirmed]),
        'overall_micro_group_recall':sum(r['recalled_groups'] for r in rows)/g if g else None,
        'all_groups_at_2':mean([r['all_groups'] for r in eligible]),'mrr_at_2':mean([r['mrr'] for r in eligible]),
        'errors':sum(bool(r['error']) for r in rows),'overflows':sum(r['overflow'] for r in rows),
        'duplicates':sum(r['duplicate_cases'] for r in rows),'returned':sum(r['returned'] for r in rows),
        'eligible_empty_returns':sum(r['returned']==0 for r in eligible),
        'median_seconds':mean([])}
    lat=[r['latency_seconds'] for r in rows if r['latency_seconds'] is not None]
    summary['median_seconds']=statistics.median(lat) if lat else None
    # Independently accumulate group ranks directly from unmodified raw returns.
    check_hit=0;check_recall=0;denominator=0
    for item in items:
        expected=[g['available_keys'] for g in item['groups'] if g['available_keys']]
        if not expected:continue
        denominator+=1;r=byid[item['qid']]
        raw=[] if r.get('error') else r.get('cases',r.get('channels',{}).get('cases',[]))[:k]
        found=[any(e['canonical_case_key'] in group for e in raw) for group in expected]
        check_hit+=any(found);check_recall+=sum(found)/len(found)
    if denominator:
        assert summary['hit_at_2']==check_hit/denominator
        assert abs(summary['macro_group_recall_at_2']-check_recall/denominator)<1e-12
    summary['independent_recalculation_pass']=True
    return rows,summary
