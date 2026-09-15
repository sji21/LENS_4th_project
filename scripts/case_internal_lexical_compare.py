"""Compare a minimum lexical-evidence gate on sealed DEV candidates."""
import argparse,json
from dataclasses import asdict,replace
from pathlib import Path
from case_internal_run import sha,write
from case_internal_compare import score_selection
from src.retrieval.case_internal import CaseInternalRetriever,CaseInternalPolicy


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--selection',type=Path,required=True)
    ap.add_argument('--results',type=Path,required=True);ap.add_argument('--inputs',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True);ap.add_argument('--membership',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    decision=json.loads(args.selection.read_text('utf-8'));policy=CaseInternalPolicy(**decision['policy'])
    assert policy.rerank!='cross_encoder','This comparison is for the chosen rule policy only.'
    base=json.loads(args.results.read_text('utf-8'));inputs=json.loads(args.inputs.read_text('utf-8'))
    items=json.loads(args.membership.read_text('utf-8'))['items']
    chunks={c['chunk_id']:c for c in [json.loads(l) for l in (args.data/'chunks/cases.jsonl').read_text('utf-8').splitlines()]}
    backend=CaseInternalRetriever.__new__(CaseInternalRetriever);backend.chunks=chunks
    backend.policy=replace(policy,require_lexical_support=True);rows=[]
    for source,old in zip(inputs,base):
        assert source['qid']==old['qid']
        chosen=backend.select(source['query'],source['hits'],2,member_hits=source['member_hits'])
        rows.append({'qid':source['qid'],'query':source['query'],'error':None,'latency_seconds':old['latency_seconds'],
            'trace':{'member_hits':source['member_hits'],'fusion_candidates':source['hits']},
            'cases':[{'chunk_id':cid,'canonical_case_key':chunks[cid]['metadata']['canonical_case_key'],
                'text':chunks[cid]['text'],'source_url':chunks[cid]['metadata']['source_url'],'score':value} for cid,value in chosen]})
    _,before=score_selection(items,base,chunks);_,after=score_selection(items,rows,chunks)
    adopted=(after['macro_group_recall_at_2']>=before['macro_group_recall_at_2'] and
        after['hit_at_2']>=before['hit_at_2'] and after['full_returned_body_group_support']>=before['full_returned_body_group_support'])
    report={'before':before,'after':after,'adopted':adopted,
        'criterion':'Retain the lexical gate only if DEV case recovery and body coverage do not decrease.',
        'semantics':'Require positive-score BM25 candidate membership. This is a minimum retrieval-evidence rule, not an oracle for legal relevance.',
        'input_sha256':sha(args.inputs),'selection_sha256':sha(args.selection),
        'ranking_unchanged':[[e['chunk_id'] for e in r['cases']] for r in base]==[[e['chunk_id'] for e in r['cases']] for r in rows]}
    write(args.output/'lexical_policy_comparison.json',report)
    write(args.output/'selected_policy.json',dict(decision,policy=asdict(backend.policy) if adopted else decision['policy'],
        lexical_comparison_sha256=sha(args.output/'lexical_policy_comparison.json')))
    write(args.output/'selected_dev_results.json',rows if adopted else base)
    write(args.output/'SEAL.json',{p.name:sha(p) for p in args.output.iterdir() if p.is_file()})
    print(json.dumps({k:v for k,v in report.items() if k not in ('before','after')}))


if __name__=='__main__':main()
