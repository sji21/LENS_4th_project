"""Capture civil candidate ranks and compare a fixed set of TOP3 policies."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from scripts.patch015_baseline import ROOT, read, write, sha, norm
from scripts.patch023_report import diagnose, summarize

# Fixed before inspecting the new candidate ranks; no question-specific rules.
POLICIES = {'baseline':(1,1,True), 'retain_dense2':(1,2,True),
            'retain_bm252':(2,1,True), 'retain_dense':(0,1,True),
            'retain_bm25':(1,0,True), 'rrf_only':(1,1,False),
            'dense2_only':(1,2,False)}


def select(row, policy):
    bw,dw,retain=POLICIES[policy]
    scores={}
    for name,weight in (('bm25',bw),('dense',dw)):
        if not weight: continue
        for rank,cid in enumerate(row[name],1):
            scores[cid]=scores.get(cid,0)+weight/(5+rank)
    ranked=sorted(scores,key=lambda c:(-scores[c],c))
    return list(dict.fromkeys((row['seed'] if retain else [])+ranked))[:3]


def capture(out):
    from src.retrieval.service import RetrievalService, detect_civil_topics
    from src.evaluation.baseline import SEARCH_K, settings
    from src.retrieval.index import clean_metadata
    out.mkdir(parents=True,exist_ok=False)
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip():
        raise ValueError('Commit the capture source first')
    expected=read(ROOT/'data/eval/patch024-expansion/adoption.json')
    files=expected['before'] | expected['adopted']
    if any(sha(ROOT/p)!=v for p,v in files.items()): raise ValueError('Data differs from PATCH-024')
    svc=RetrievalService.from_index()
    original=svc.dense.backend.embed
    cache={}
    def cached(texts):
        key=tuple(texts)
        if key not in cache: cache[key]=original(texts)
        return cache[key]
    svc.dense.backend.embed=cached
    assert svc.civil_dense.backend is svc.dense.backend
    indexed=svc.civil_dense.collection.get(include=['documents','metadatas'])
    for cid,body,meta in zip(indexed['ids'],indexed['documents'],indexed['metadatas']):
        assert body==svc._chunks[cid]['text'] and meta==clean_metadata(svc._chunks[cid]['metadata'])
    assert len(indexed['ids'])==10
    anchor=lambda cid:norm(svc._chunks[cid]['metadata']['article_id'])
    previous={(r['qid'],r['mode']):r for r in read(ROOT/'data/eval/patch024-expansion/results.json')}
    rows=[]
    for q in read(ROOT/'data/eval/patch015-baseline/capture/results.json'):
        result=svc.search(q['query'],**SEARCH_K)
        actual={'qid':q['qid'],'mode':q['mode'],'laws':[anchor(e.chunk_id) for e in result.laws],
                'civil_laws':[anchor(e.chunk_id) for e in result.civil_laws],
                'cases':[e.chunk_id for e in result.cases],'guides':[e.chunk_id for e in result.guides]}
        if actual!=previous[q['qid'],q['mode']]: raise ValueError('Baseline drift')
        seed=svc._search_civil(q['query'],detect_civil_topics(q['query']),2)
        rrf=svc._search_one(svc.civil,q['query'],10)
        members=svc._retrievers[svc.civil.name].last_member_hits()
        row={'qid':q['qid'],'mode':q['mode'],'query_sha256':q['query_sha256'],
             'seed':[e.chunk_id for e in seed],'rrf':[e.chunk_id for e in rrf],
             **{('bm25' if k.endswith('-bm25') else 'dense'):v for k,v in members.items()}}
        assert select(row,'baseline')==[e.chunk_id for e in result.civil_laws]
        rows.append(row)
        if len(rows)%25==0: print(f'{len(rows)}/235 candidate traces',flush=True)
    if any(sha(ROOT/p)!=v for p,v in files.items()): raise ValueError('Data changed during capture')
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip(): raise ValueError('Source changed')
    original_manifest=read(ROOT/'data/eval/patch015-baseline/capture/manifest.json')
    modelroot=Path.home()/'.cache/huggingface/hub/models--nlpai-lab--KURE-v1'
    assert all(sha(modelroot/p)==v for p,v in original_manifest['model_files'].items())
    write(out/'traces.json',rows)
    dependencies=['data/eval/patch024-expansion/results.json','data/eval/patch024-expansion/report.json',
                  'data/eval/patch015-baseline/capture/results.json','data/eval/patch015-baseline/capture/inventory.json']
    write(out/'audit.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'clean':True,'data_hashes':files,'model_files':original_manifest['model_files'],
        'settings':settings(svc),'policies':POLICIES,'baseline_matches':len(rows),
        'candidate_ids':{cid:anchor(cid) for cid in indexed['ids']},
        'dependencies':{p:hashlib.sha256((ROOT/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in dependencies},
        'traces_sha256':sha(out/'traces.json'),'embedding_cache':'identical input tuples within this run'})


def report(run):
    audit=read(run/'audit.json'); rows=read(run/'traces.json')
    if sha(run/'traces.json')!=audit['traces_sha256']: raise ValueError('Trace changed')
    for p,v in audit['dependencies'].items():
        if hashlib.sha256((ROOT/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=v: raise ValueError('Baseline changed')
    prior=read(ROOT/'data/eval/patch024-expansion/report.json')['details']
    old={(r['qid'],r['mode']):r for r in prior}
    if len(rows)!=235 or {(r['qid'],r['mode']) for r in rows}!=set(old): raise ValueError('Incomplete capture')
    anchors=audit['candidate_ids']
    available={r['article_anchor'] for r in read(ROOT/'data/eval/patch015-baseline/capture/inventory.json')} | set(anchors.values())
    results={}; misses=[]
    for policy in POLICIES:
        details=[]
        for r in rows:
            for name in ('seed','rrf','bm25','dense'):
                if len(set(r[name]))!=len(r[name]) or not set(r[name])<=set(anchors): raise ValueError('Invalid candidates')
            p=old[r['qid'],r['mode']]
            chosen=[anchors[c] for c in select(r,policy)]
            d=diagnose(p['targets'],available,p['channels']['general'],chosen,p['category']=='historical_review')
            oldhit=set(p['targets']) & set(p['channels']['general']+p['channels']['civil'])
            d.update(qid=r['qid'],mode=r['mode'],track=p['track'],lost=sorted(oldhit-set(p['channels']['general']+chosen)))
            details.append(d)
            if policy=='baseline':
                assert chosen==p['channels']['civil']
                for target in set(p['held_not_returned']) & set(anchors.values()):
                    cid=next(k for k,v in anchors.items() if v==target)
                    ranks={k:(r[k].index(cid)+1 if cid in r[k] else None) for k in ('bm25','dense','rrf')}
                    misses.append({'qid':r['qid'],'mode':r['mode'],'target':target,**ranks,
                                   'cause':'selection_displacement' if ranks['rrf'] and ranks['rrf']<=3 else 'fusion_rank' if ranks['rrf'] else 'candidate_absent'})
        groups={m:summarize([d for d in details if d['track']=='dev100' and d['mode']==m]) for m in ('question_only','context_diagnostic')}
        groups.update({t:summarize([d for d in details if d['track']==t]) for t in ('required_law','scope_provisional','diagnostic_only')})
        results[policy]={'groups':groups,'lost_required':[{'qid':d['qid'],'mode':d['mode'],'lost':d['lost']} for d in details if d['lost']], 'details':details}
    return {'policies':results,'miss_causes':misses}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--capture',type=Path);p.add_argument('--report',type=Path);args=p.parse_args()
    if args.capture:
        os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',LANGSMITH_TRACING='false',ANONYMIZED_TELEMETRY='False')
        capture(args.capture)
    if args.report: write(args.report/'comparison.json',report(args.report))
