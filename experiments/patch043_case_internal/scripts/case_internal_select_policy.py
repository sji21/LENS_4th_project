"""Choose a DEV policy after separately sealed local model executions."""
import argparse,json,itertools
from pathlib import Path
from dataclasses import asdict,replace
from experiments.patch043_case_internal.scripts.case_internal_run import sha,write
from experiments.patch043_case_internal.scripts.case_internal_compare import score_selection,selection_record,objective
from experiments.patch043_case_internal.retrieval import CaseInternalRetriever,CaseInternalPolicy


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--comparison',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True);ap.add_argument('--membership',type=Path,required=True)
    ap.add_argument('--models',nargs='+',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    items=json.loads(args.membership.read_text('utf-8'))['items']
    inputs=json.loads((args.comparison/'selected_candidate_inputs.json').read_text('utf-8'))
    selected=json.loads((args.comparison/'selected_rule.json').read_text('utf-8'))
    base=CaseInternalPolicy(**selected['policy'])
    chunks={c['chunk_id']:c for c in [json.loads(l) for l in (args.data/'chunks/cases.jsonl').read_text('utf-8').splitlines() if l]}
    backend=CaseInternalRetriever.__new__(CaseInternalRetriever);backend.chunks=chunks
    table=[dict(selected,model=None)];best=table[0];best_rows=json.loads((args.comparison/'selected_rule_results.json').read_text('utf-8'))
    for folder in args.models:
        seal=json.loads((folder/'RESULT_SEAL.json').read_text('utf-8'))
        assert all(sha(folder/n)==v for n,v in seal.items())
        if (folder/'EXECUTION_FAILURE.json').exists():
            table.append({'model_run':str(folder),'status':'excluded_execution_failure',
                'failure':json.loads((folder/'EXECUTION_FAILURE.json').read_text('utf-8'))})
            continue
        scores=[json.loads(l) for l in (folder/'scores.jsonl').read_text('utf-8').splitlines()]
        assert len(scores)==len(inputs)
        for threshold,complement in itertools.product((0,.05,.2,.5),(0,.1,.2)):
            policy=replace(base,rerank='cross_encoder',min_relevance=threshold,complement_weight=complement)
            backend.policy=policy;rows=[]
            for source,r in zip(inputs,scores):
                assert source['qid']==r['qid']
                if r['error']:
                    rows.append({'qid':r['qid'],'error':r['error'],'cases':[]});continue
                rows.append(selection_record(r['qid'],r['query'],source['hits'],backend,r['scores'],
                    source['candidate_seconds']+r['latency_seconds']))
            _,summary=score_selection(items,rows,chunks)
            entry={'policy':asdict(policy),'metrics':summary,'model_run':str(folder),
                   'model_input_seal_sha256':sha(folder/'INPUT_SEAL.json'),
                   'truncated_pairs':sum(r.get('truncated_pairs',0) for r in scores)}
            table.append(entry)
            if objective(summary,policy)>objective(best['metrics'],CaseInternalPolicy(**best['policy'])):
                best=entry;best_rows=rows
    write(args.output/'rerank_comparison.json',table);write(args.output/'selected_policy.json',best)
    write(args.output/'selected_dev_results.json',best_rows)
    write(args.output/'SEAL.json',{p.name:sha(p) for p in args.output.iterdir() if p.is_file()})
    print(json.dumps(best,ensure_ascii=False))


if __name__=='__main__':main()
