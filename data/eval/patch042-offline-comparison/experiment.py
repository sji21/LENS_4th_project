"""Two offline candidates, fixed source trace and original target scoring only."""
from scripts.patch041_retrieval_eval import build_jobs, read, norm, sha, write
from src.retrieval.retriever import load_chunks, BM25Retriever
from src.retrieval.multi_evidence import TaxLookupSelector, requested_tax_scopes
from collections import defaultdict
from pathlib import Path
chunks=load_chunks('../patch041-worktree/data/chunks/chunks.jsonl')
selector=TaxLookupSelector(chunks)
lexical=BM25Retriever(chunks, query_expander=lambda _: [])
by_article={norm(c['metadata']['article_id']):c['chunk_id'] for c in chunks}
anchors={c['chunk_id']:norm(c['metadata']['article_id']) for c in chunks}
tracepath=Path('data/eval/patch042-candidate-trace/traces.json')
rowpath=Path('data/eval/patch042-candidate-trace/capture/rows.json')
traces=read(tracepath); rows=read(rowpath)
results={}
for supplement in (False,True):
    scores=defaultdict(lambda:dict(n=0,before_complete=0,after_complete=0,before_3plus_complete=0,after_3plus_complete=0,n_3plus=0));changes=[]
    for job,trace,row in zip(build_jobs(),traces,rows):
        before=[norm(e['article_id']) for e in row['result']['laws']]
        civil=[norm(e['article_id']) for e in row['result']['civil_laws']]
        hits=[(by_article[a],1/(5+i)) for i,a in enumerate(trace['general']['fused'][:20],1)]
        scopes=requested_tax_scopes(job['query'])
        if supplement and scopes:
            terms=' '.join('미납국세 열람' if s=='national' else '미납지방세 열람' for s in scopes)
            known={cid for cid,_ in hits}
            hits.extend((cid,0.0) for cid,_ in lexical.search(terms,20,trace['general']['where']) if cid not in known)
        after=[anchors[cid] for cid,_ in selector.select(job['query'],hits,5,trace['general']['where'])]
        targets={norm(a) for a in job['targets']}
        if targets and not job['historical']:
            group=job['mode'] if job['track']=='dev100' else job['track']
            c=scores[group];c['n']+=1;c['before_complete']+=targets<=set(before+civil);c['after_complete']+=targets<=set(after+civil)
            if len(targets)>=3:
                c['n_3plus']+=1;c['before_3plus_complete']+=targets<=set(before+civil);c['after_3plus_complete']+=targets<=set(after+civil)
        if after!=before:changes.append(dict(qid=job['qid'],mode=job['mode'],scopes=scopes,before=before,after=after,gain=sorted(targets&set(after)-set(before)),loss=sorted(targets&set(before)-set(after))))
    key='candidate2_lexical_supplement' if supplement else 'candidate1_existing_fused20'
    results[key]=dict(metrics=dict(scores),changes=changes)
output=dict(trace_sha256=sha(tracepath),rows_sha256=sha(rowpath),policy_sha256=sha(Path('src/retrieval/multi_evidence.py')),results=results,limitations='Offline recombination of actual observed member ranks and body matching after seeing DEV. No fresh KURE, no new independent holdout, no legal eligibility judgement. Auxiliary lexical search only for candidate2; candidate1 retained because outputs identical.')
write(Path('tmp/patch042-improve-offline.json'),output)
for key,value in results.items():print(key,value['metrics'],'changed',len(value['changes']),'losses',sum(len(r['loss']) for r in value['changes']))
