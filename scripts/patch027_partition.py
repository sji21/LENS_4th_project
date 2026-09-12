"""Separate corpus-statistic drift from candidate competition; compare fixed TOP5 policies."""
import argparse
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

from scripts.patch026_expand import ROOT, read, write, sha, norm, score
from scripts.patch026_sources import SPECS
from scripts.patch025_ranking import index_digest, close
from src.retrieval.service import RetrievalService, LAW, route_law_corpus, CIVIL_TITLE
from src.retrieval.hybrid import HybridRetriever
from src.retrieval.retriever import BM25Retriever
from src.evaluation.baseline import settings

POLICIES=('split_half','split_three_quarters','split_equal','keep2_split_equal',
          'pool_half','pool_equal','pool_double')
DEPENDENCIES=('data/eval/patch026-expansion/before.json','data/eval/patch026-expansion/full/after.json',
              'data/eval/patch026-expansion/full/audit.json','data/eval/patch024-expansion/report.json',
              'data/eval/patch015-baseline/capture/results.json')


def fuse(bm25,dense):
    values={}
    for ranked in (bm25,dense):
        for rank,cid in enumerate(ranked[:20],1): values[cid]=values.get(cid,0)+1/(5+rank)
    return sorted(values,key=lambda c:(-values[c],c))


def select(row, policy):
    if policy.startswith('pool_'):
        weight={'pool_half':.5,'pool_equal':1.,'pool_double':2.}[policy]
        bm=row['core_bm25']+[(cid,s*weight) for cid,s in row['procedure_bm25']]
        bm=sorted(bm,key=lambda x:(-x[1],x[0]))
        return fuse([cid for cid,_ in bm],[cid for cid,_ in row['global_dense']])[:5]
    weight={'split_half':.5,'split_three_quarters':.75,'split_equal':1.,'keep2_split_equal':1.}[policy]
    combined=row['core']+[(cid,s*weight) for cid,s in row['procedure']]
    ranked=[cid for cid,_ in sorted(combined,key=lambda x:(-x[1],x[0]))]
    kept=[cid for cid,_ in row['core'][:2]] if policy.startswith('keep2') else []
    return list(dict.fromkeys(kept+ranked))[:5]


def capture(out):
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip(): raise ValueError('Commit source first')
    svc=RetrievalService.from_index(); backend=svc.dense.backend
    if not isinstance(svc._retrievers[LAW.name].members[0].retriever, BM25Retriever):
        raise ValueError('Historical capture requires the pre-partition service (6085564); use --report to replay')
    out.mkdir(parents=True,exist_ok=False)
    original=backend.embed; cache={}
    def cached(texts):
        key=tuple(texts)
        if key not in cache: cache[key]=original(texts)
        return cache[key]
    backend.embed=cached
    semantic=[index_digest(r) for r in (svc.dense,svc.civil_dense)]
    anchors={cid:norm(c['metadata']['article_id']) for cid,c in svc._chunks.items() if c['metadata'].get('doc_type') in ('law','decree','rule')}
    proc_ids={name.replace(' ','')+'-'+article for _,name,article,*_ in SPECS}
    laws=[c for c in svc._chunks.values() if c['metadata'].get('doc_type') in ('law','decree','rule') and c['metadata'].get('title')!=CIVIL_TITLE]
    core_chunks=[c for c in laws if anchors[c['chunk_id']] not in proc_ids]
    proc_chunks=[c for c in laws if anchors[c['chunk_id']] in proc_ids]
    assert len(core_chunks)==133 and len(proc_chunks)==5
    raw_proc_ids=[c['metadata']['article_id'] for c in proc_chunks]
    core=svc._build(replace(LAW,name='core'),core_chunks)
    procedure=svc._build(replace(LAW,name='procedure'),proc_chunks)
    mixed=svc._retrievers[LAW.name]
    previous={(r['qid'],r['mode']):r for r in read(ROOT/'data/eval/patch026-expansion/before.json')}
    old_mixed={(r['qid'],r['mode']):r for r in read(ROOT/'data/eval/patch026-expansion/full/after.json')}
    rows=[]
    for q in read(ROOT/'data/eval/patch015-baseline/capture/results.json'):
        where=route_law_corpus(q['query']).where()
        core_where={'$and':[where,{'article_id':{'$nin':raw_proc_ids}}]}
        proc_where={'$and':[where,{'article_id':{'$in':raw_proc_ids}}]}
        ranks={}
        for name,retriever,filtered in (('core',core,core_where),('procedure',procedure,proc_where),
                                        ('statistics_only',mixed,core_where),('mixed',mixed,where)):
            ranks[name]=retriever.search(q['query'],20,filtered)
        for name,member,filtered in (('core_bm25',core.members[0],core_where),
                                    ('procedure_bm25',procedure.members[0],proc_where),
                                    ('global_dense',mixed.members[1],where)):
            ranks[name]=HybridRetriever._ask(member,q['query'],20,filtered)
        assert [anchors[cid] for cid,_ in ranks['core'][:5]]==previous[q['qid'],q['mode']]['laws'], ('Core baseline drift',q['qid'],q['mode'])
        assert [anchors[cid] for cid,_ in ranks['mixed'][:5]]==old_mixed[q['qid'],q['mode']]['laws'], 'Mixed baseline drift'
        rows.append({'qid':q['qid'],'mode':q['mode'],'query_sha256':q['query_sha256'],**ranks})
        if len(rows)%25==0: print(f'{len(rows)}/235 partition traces',flush=True)
    assert semantic==[index_digest(r) for r in (svc.dense,svc.civil_dense)]
    assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip()
    write(out/'traces.json',rows)
    write(out/'audit.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'clean':True,
        'settings':settings(svc),'candidate_anchors':anchors,'procedures':sorted(proc_ids),'policies':POLICIES,
        'index_semantic_hashes':semantic,'traces_sha256':sha(out/'traces.json'),
        'dependencies':{p:hashlib.sha256((ROOT/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in DEPENDENCIES},
        'baseline_matches':len(rows),'mixed_matches':len(rows),'production_changed':False})


def report(run):
    audit=read(run/'audit.json'); traces=read(run/'traces.json')
    if sha(run/'traces.json')!=audit['traces_sha256']: raise ValueError('Trace hash mismatch')
    if set(audit['dependencies'])!=set(DEPENDENCIES): raise ValueError('Incomplete dependencies')
    if tuple(audit['policies'])!=POLICIES: raise ValueError('Policy list changed')
    for p,v in audit['dependencies'].items():
        if hashlib.sha256((ROOT/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=v: raise ValueError('Dependency changed')
    before=read(ROOT/'data/eval/patch026-expansion/before.json'); old={(r['qid'],r['mode']):r for r in before}
    queries={(r['qid'],r['mode']):r['query_sha256'] for r in read(ROOT/'data/eval/patch015-baseline/capture/results.json')}
    if len(traces)!=235 or {(r['qid'],r['mode']) for r in traces}!=set(old): raise ValueError('Incomplete traces')
    anchors=audit['candidate_anchors']; available=read(ROOT/'data/eval/patch026-expansion/full/audit.json')['available_after']
    if len(anchors)!=len(set(anchors.values())) or set(anchors.values())!=set(available):
        raise ValueError('Candidate inventory differs')
    procedures={name.replace(' ','')+'-'+article for _,name,article,*_ in SPECS}
    if set(audit['procedures'])!=procedures: raise ValueError('Procedure selection changed')
    mixed={(r['qid'],r['mode']):r for r in read(ROOT/'data/eval/patch026-expansion/full/after.json')}
    for r in traces:
        for name in ('core','procedure','statistics_only','mixed','core_bm25','procedure_bm25','global_dense'):
            hits=r[name]
            if len(hits)>20 or len({cid for cid,_ in hits})!=len(hits): raise ValueError('Invalid rank trace')
            if any(cid not in anchors or not math.isfinite(s) for cid,s in hits): raise ValueError('Invalid trace value')
            if name in ('core','core_bm25','statistics_only') and any(anchors[cid] in procedures for cid,_ in hits):
                raise ValueError('Procedure leaked into core candidates')
            if name in ('procedure','procedure_bm25') and any(anchors[cid] not in procedures for cid,_ in hits):
                raise ValueError('Core leaked into procedure candidates')
        key=r['qid'],r['mode']
        if [anchors[c] for c,_ in r['core'][:5]]!=old[key]['laws']: raise ValueError('Core baseline changed')
        if [anchors[c] for c,_ in r['mixed'][:5]]!=mixed[key]['laws']: raise ValueError('Mixed baseline changed')
    results={}
    for policy in ('statistics_only','mixed',*POLICIES):
        rows=[]
        for r in traces:
            if r['query_sha256']!=queries[r['qid'],r['mode']]: raise ValueError('Query changed')
            ids=[cid for cid,_ in r[policy][:5]] if policy in ('statistics_only','mixed') else select(r,policy)
            if len(ids)>5 or len(set(ids))!=len(ids) or not set(ids)<=set(anchors): raise ValueError('Invalid candidates')
            rows.append({**old[r['qid'],r['mode']],'laws':[anchors[cid] for cid in ids]})
        results[policy]=score(rows,available,before)
    return results


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--capture',type=Path);p.add_argument('--report',type=Path);args=p.parse_args()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',LANGSMITH_TRACING='false',ANONYMIZED_TELEMETRY='False')
    if args.capture: capture(args.capture)
    if args.report:
        results=report(args.report); write(args.report/'comparison.json',results)
        for name,r in results.items(): print(name,len(r['losses']),{g:r['groups'][g]['union_all_required']['hits'] for g in ('question_only','context_diagnostic')})
