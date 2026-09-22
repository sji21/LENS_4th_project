"""Report actual fresh k=2 baseline separately from historical Top20 results."""
import argparse,json
from pathlib import Path
from experiments.patch043_case_internal.scripts.case_internal_run import sha,write
from experiments.patch043_case_internal.metrics import score


def read_run(path):
    seals=json.loads((path/'RESULT_SEAL.json').read_text('utf-8'))
    assert all(sha(path/n)==v for n,v in seals.items())
    return [json.loads(l) for l in (path/'results.jsonl').read_text('utf-8').splitlines() if l]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--project',type=Path,required=True)
    ap.add_argument('--root',type=Path,required=True);args=ap.parse_args();root=args.root
    source=args.project/'outputs/retriever_completion_20260914'
    datasets={}
    gold=json.loads((source/'03_final_preparation/final_gold.json').read_text('utf-8'))['items']
    datasets['public_ho_k2']=[{'qid':g['qid'],'groups':[{'group_id':x['group_id'],'available_keys':x['available_canonical_case_keys']}
        for x in g['primary_case_groups'] if g['evaluation']['include_primary'] and x['group_id'] in g['evaluation']['primary_group_ids']]} for g in gold]
    dev=json.loads((source/'02_dev/gold_cases.v2.membership.json').read_text('utf-8'))['items']
    datasets['existing_dev_k2']=[{'qid':g['qid'],'groups':([{'group_id':'legacy_acceptable_set','available_keys':g['retrieval_evaluation']['acceptable_primary_case_keys']}]
        if g['retrieval_evaluation']['acceptable_primary_case_keys'] else [])} for g in dev]
    # Historical DEV's flat acceptable-set metric is not relabelled grouped Gold.
    fresh=json.loads((root/'01_gold/reviewer/dev12_membership.compact.v3.json').read_text('utf-8'))['items']
    prior=json.loads((root/'01_gold/reviewer/dev12_body_membership.before_condition_supplement.json').read_text('utf-8'))
    prior_by_group={(r['qid'],r['group_id']):r for r in prior['rows']}
    datasets['consultation_dev_k2']=[{'qid':item['qid'],'groups':[dict(g,
        available_keys=g['available_keys'] if prior_by_group[item['qid'],g['group_id']]['all_gold_proposition_and_additional_conditions_supported'] else [])
        for g in item['groups']]} for item in fresh]
    chunks={c['chunk_id']:c for c in [json.loads(l) for l in (root/'data_baseline/chunks/cases.jsonl').read_text('utf-8').splitlines() if l]}
    assert all(row['indexed_body_full'] == chunks[row['chunk_ids'][0]]['text'] for row in prior['rows'])
    reports={}
    for name,items in datasets.items():
        results=read_run(root/'00_baseline'/name);rows,summary=score(items,results)
        losses=[]
        for item,result in zip(items,results):
            assert item['qid']==result['qid']
            trace=result.get('trace',{});member=trace.get('member_hits',{})
            sets={m:{chunks[c]['metadata']['canonical_case_key'] for c in ids} for m,ids in member.items()}
            sets['fusion']={chunks[c]['metadata']['canonical_case_key'] for c,_ in trace.get('raw_rrf_candidates',[])}
            final={c['canonical_case_key'] for c in result.get('cases',[])}
            union=set().union(*[s for n,s in sets.items() if n!='fusion']) if member else set()
            for g in item['groups']:
                expected=set(g['available_keys'])
                category=('execution_error' if result.get('error') else 'corpus_body_absent' if not expected else
                    'recovered' if expected&final else 'candidate_missing' if not expected&union else
                    'fusion_missing' if not expected&sets['fusion'] else 'final_top2_selection_missing')
                losses.append({'qid':item['qid'],'group_id':g['group_id'],'stage':category,
                    'expected_available_keys':g['available_keys'],**{n:bool(s&expected) for n,s in sets.items()}})
        if name=='existing_dev_k2':summary['metric_kind']='Historical DEV flat acceptable-set Hit; not revised multi-group Gold'
        if name=='consultation_dev_k2':
            summary['metric_kind']='Source-derived DEV: groups without the required application-condition body are corpus gaps, not retrieval misses'
            _,identity=score(fresh,results)
            write(root/'00_baseline'/name/'identity_only_diagnostic.json',identity)
        write(root/'00_baseline'/name/'score_rows.json',rows);write(root/'00_baseline'/name/'score_summary.json',summary)
        write(root/'00_baseline'/name/'failure_stages.json',losses);reports[name]=summary
    write(root/'00_baseline/BASELINE_REPORT.json',reports)
    print(json.dumps(reports,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
