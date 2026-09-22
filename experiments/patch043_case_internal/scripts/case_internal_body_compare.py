"""DEV-only comparison of selected chunks and continuous same-case source context."""
import argparse,json
from dataclasses import asdict,replace
from pathlib import Path
from experiments.patch043_case_internal.scripts.case_internal_run import sha,write
from experiments.patch043_case_internal.scripts.case_internal_compare import score_selection
from experiments.patch043_case_internal.retrieval import CaseInternalPolicy,CaseInternalRetriever,continuous_case_bodies


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--selection',type=Path,required=True)
    ap.add_argument('--results',type=Path,required=True);ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--membership',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    selected=json.loads(args.selection.read_text('utf-8'));original=json.loads(args.results.read_text('utf-8'))
    items=json.loads(args.membership.read_text('utf-8'))['items']
    chunks={c['chunk_id']:c for c in [json.loads(l) for l in (args.data/'chunks/cases.jsonl').read_text('utf-8').splitlines()]}
    backend=CaseInternalRetriever.__new__(CaseInternalRetriever);backend.chunks=chunks
    backend.body_contexts=continuous_case_bodies(list(chunks.values()))
    backend.policy=replace(CaseInternalPolicy(**selected['policy']),body_policy='continuous_official_case')
    rows=json.loads(json.dumps(original))
    for row in rows:
        row['trace']=dict(row.get('trace',{}),body_sources=[])
        for rank,e in enumerate(row['cases'],1):
            rendered,source=backend.render(e['chunk_id'],rank,e['score'])
            e['text']=rendered.text;e['source_url']=rendered.source_url
            row['trace']['body_sources'].append(source)
    # Candidate ranks and case choices do not change; only returned body is compared.
    assert [[e['canonical_case_key'] for e in r['cases']] for r in original]==[[e['canonical_case_key'] for e in r['cases']] for r in rows]
    _,before=score_selection(items,original,chunks);_,after=score_selection(items,rows,chunks)
    adopted=after['full_returned_body_group_support']>before['full_returned_body_group_support']
    policy=asdict(backend.policy) if adopted else selected['policy']
    decision=dict(selected,policy=policy,body_comparison_sha256=None)
    report={'before':before,'after':after,'adopted':adopted,
        'decision_basis':'Compare source-backed returned-body coverage on DEV while fixing case rankings; semantic review is recorded separately.',
        'query_or_gold_used_in_context_construction':False,'same_case_contiguous_source_only':True,
        'before_returned_characters':sum(len(e['text']) for r in original for e in r['cases']),
        'after_returned_characters':sum(len(e['text']) for r in rows for e in r['cases']),
        'maximum_returned_case_characters':max(len(e['text']) for r in rows for e in r['cases']),
        'retrieval_candidate_selection_sha256':sha(args.selection),'source_results_sha256':sha(args.results)}
    write(args.output/'body_policy_comparison.json',report)
    decision['body_comparison_sha256']=sha(args.output/'body_policy_comparison.json')
    write(args.output/'selected_body_policy.json',decision)
    write(args.output/'selected_dev_results.json',rows if adopted else original)
    write(args.output/'SEAL.json',{p.name:sha(p) for p in args.output.iterdir() if p.is_file()})
    print(json.dumps({k:v for k,v in report.items() if k not in ('before','after')},ensure_ascii=False))


if __name__=='__main__':main()
