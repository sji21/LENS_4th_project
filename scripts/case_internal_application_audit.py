"""Audit sealed real-entrypoint results without modifying raw records."""
import argparse,json,statistics
from pathlib import Path
from case_internal_run import sha,write
from case_internal_compare import score_selection


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True);ap.add_argument('--membership',type=Path,required=True)
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    seal=json.loads((args.run/'RESULT_SEAL.json').read_text('utf-8'))
    assert all(sha(args.run/n)==v for n,v in seal.items()),'raw result seal mismatch'
    inputs=json.loads((args.run/'INPUT_SEAL.json').read_text('utf-8'))
    checks=[];cld={};all_results={}
    gold=json.loads((args.root/'01_gold/author/originals/case_law_dev100_v1/gold_cases.v1.json').read_text('utf-8-sig'))['items']
    keys={g['qid']:{c['canonical_case_key'] for c in g['gold_cases']} for g in gold}
    assert len(keys)==100
    for name,job in inputs['questions'].items():
        rows=[json.loads(l) for l in (args.run/name/'results.jsonl').read_text('utf-8').splitlines()]
        questions=json.loads(Path(job['path']).read_text('utf-8-sig'))
        assert [q['qid'] for q in questions]==[r['qid'] for r in rows]
        all_results[name]=rows
        for r in rows:
            c=r.get('connection_checks',{});payload=r.get('evidence_payload',{})
            checks.append({'job':name,'qid':r['qid'],'error':r.get('error'),
                'pass':not r.get('error') and bool(c) and c['within_requested_k'] and c['distinct_cases'] and c['ordered_ranks']
                    and all(all(v for n,v in e.items() if n!='chunk_id') for e in c['evidence'])
                    and payload.get('schema')=='lens-retrieval-evidence-v1'
                    and set(payload.get('channels',{}))=={'cases','laws','civil_laws','guides'},
                'returned':len(r['cases']),'k':r['k']})
        if name.startswith('cld_'):
            assert len(rows)==100 and set(keys)=={r['qid'] for r in rows}
            hits=[bool(keys[r['qid']].intersection(e['canonical_case_key'] for e in r['cases'])) and not r['error'] for r in rows]
            independent=sum(any(e['canonical_case_key'] in keys[r['qid']] for e in r['cases']) for r in rows if not r['error'])/100
            assert sum(hits)/100==independent
            cld[name]={'actual_requested_k':job['k'],'count':100,'hit':sum(hits)/100,
                'independent_recalculation_pass':True,'failed_qids':[r['qid'] for r,h in zip(rows,hits) if not h],
                'kind':'Original source-derived CLD case-retrieval regression; not consultation accuracy'}
    dev=json.loads(args.membership.read_text('utf-8'))['items']
    chunks={c['chunk_id']:c for c in [json.loads(l) for l in (args.data/'chunks/cases.jsonl').read_text('utf-8').splitlines()]}
    dev_rows,dev_metrics=score_selection(dev,all_results['dev32_k2'],chunks)
    dev_metrics['metric_interpretation']='Hit/Recall here measure identity retrieval of corpus-supported Gold cases. Semantic support of actual returned bodies is separately reviewed and required for final primary metrics.'
    write(args.output/'dev32_diagnostic_rows.json',dev_rows);write(args.output/'dev32_diagnostic_metrics.json',dev_metrics)
    write(args.output/'connection_checks.json',checks)
    runtime=json.loads((args.run/'runtime.json').read_text('utf-8'))
    flat=[r for rows in all_results.values() for r in rows]
    report={'raw_result_seal_sha256':sha(args.run/'RESULT_SEAL.json'),
        'profile_sha256':inputs['profile_sha256'],'entrypoint':runtime['entrypoint'],
        'actual_connection_pass':all(c['pass'] for c in checks),
        'checked_questions':len(checks),'returned_evidence':sum(c['returned'] for c in checks),
        'errors':sum(bool(c['error']) for c in checks),'cld_regression':cld,
        'cld_hit20_pass':cld['cld_k20']['hit']==1,
        'maximum_observed_rss_bytes':max([r['rss_bytes'] for r in flat]+[runtime['rss_bytes']]),
        'median_seconds':statistics.median(r['latency_seconds'] for r in flat),
        'generation_executed':False,'other_channel_operating_indexes_required':False,
        'empty_query_returns_zero':all(not r['cases'] for r in all_results['empty_k2']),
        'explicit_k0_returns_zero':all(not r['cases'] for r in all_results['explicit_k0']),
        'no_lexical_evidence_returns_zero':all(not r['cases'] for r in all_results['no_lexical_k2']),
        'explicit_k5_not_globally_capped_to_2':any(len(r['cases'])>2 for r in all_results['explicit_k5']),
        'runtime':runtime}
    write(args.output/'APPLICATION_CONNECTION_REPORT.json',report)
    write(args.output/'SEAL.json',{p.name:sha(p) for p in args.output.iterdir() if p.is_file()})
    print(json.dumps({k:v for k,v in report.items() if k!='runtime'},ensure_ascii=False))


if __name__=='__main__':main()
