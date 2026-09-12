"""Ingest reviewed procedure laws into copies and compare all 235 retrieval inputs."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from scripts.patch015_baseline import ROOT, read, write, sha, norm
from scripts.patch023_report import diagnose, summarize
from scripts.patch025_ranking import index_digest
from src.ingestion.load_laws import read_records, load_records, export_chunks
from src.retrieval.retriever import load_chunks
from src.retrieval.service import RetrievalService
from src.retrieval.dense import ChromaRetriever
from src.retrieval.index import clean_metadata
from src.evaluation.baseline import SEARCH_K, settings


def groups(details):
    result={m:summarize([d for d in details if d['track']=='dev100' and d['mode']==m]) for m in ('question_only','context_diagnostic')}
    result.update({t:summarize([d for d in details if d['track']==t]) for t in ('required_law','scope_provisional','diagnostic_only')})
    return result


def score(rows, available, old_rows):
    targets={(r['qid'],r['mode']):r for r in read(ROOT/'data/eval/patch024-expansion/report.json')['details']}
    prior={(r['qid'],r['mode']):r for r in old_rows}
    details=[]; losses=[]
    for row in rows:
        key=row['qid'],row['mode']; target=targets[key]; old=prior[key]
        d=diagnose(target['targets'],available,row['laws'],row['civil_laws'],target['category']=='historical_review')
        lost=sorted((set(target['targets']) & set(old['laws']+old['civil_laws']))-set(row['laws']+row['civil_laws']))
        d.update(qid=row['qid'],mode=row['mode'],track=target['track'],lost_prior_required=lost)
        details.append(d)
        if lost: losses.append({'qid':row['qid'],'mode':row['mode'],'lost':lost})
    return {'groups':groups(details),'losses':losses,'details':details}


def run(out):
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip(): raise ValueError('Commit source first')
    bundle=ROOT/'data/eval/patch026-expansion'
    records=read_records(bundle/'records.jsonl')
    from scripts.patch026_sources import SPECS, parse_page
    manifest=read(bundle/'source-manifest.json')
    if len(records)!=5 or len(manifest)!=5: raise ValueError('Expected five sources')
    for record,spec,source in zip(records,SPECS,manifest):
        path=bundle/'sources'/f'{spec[0]}.html'
        if sha(path)!=source['sha256']: raise ValueError('Source hash mismatch')
        if record!=parse_page(path.read_text(encoding='utf-8'),spec,source['url'],path.relative_to(ROOT).as_posix()):
            raise ValueError('Record differs from source')
    out.mkdir(parents=True,exist_ok=False)
    for name in ('database','chunks','index'): shutil.copytree(ROOT/'data'/name,out/'candidate/data'/name)
    snapshot=out/'candidate'
    datafiles={p.relative_to(ROOT).as_posix():sha(p) for name in ('database','chunks') for p in (ROOT/'data'/name).rglob('*') if p.is_file()}
    db=sqlite3.connect(snapshot/'data/database/knowledge.sqlite3'); db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    oldcontent={r['article_id']:r['content'] for r in db.execute('SELECT article_id,content FROM law_articles')}
    added=load_records(records,db)
    if added.skipped: raise ValueError(added.skipped)
    newcontent={r['article_id']:r['content'] for r in db.execute('SELECT article_id,content FROM law_articles')}
    if len(newcontent)!=len(oldcontent)+5 or any(newcontent[k]!=v for k,v in oldcontent.items()): raise ValueError('Existing articles changed')
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall()
    export_chunks(db,out/'export.jsonl'); db.close()
    newids={norm(r.law_name+'-'+r.article_number) for r in records}
    new=[c for c in load_chunks(out/'export.jsonl') if norm(c['metadata']['article_id']) in newids]
    assert len(new)==5
    with (snapshot/'data/chunks/chunks.jsonl').open('ab') as f:
        for c in new: f.write((json.dumps(c,ensure_ascii=False)+'\n').encode())
    svc=RetrievalService.from_index(); backend=svc.dense.backend
    original=backend.embed; cache={}
    def cached(texts):
        key=tuple(texts)
        if key not in cache: cache[key]=original(texts)
        return cache[key]
    backend.embed=cached
    before_digests=[index_digest(r) for r in (svc.dense,svc.civil_dense)]
    dense=ChromaRetriever(backend,snapshot/'data/index/chroma_kurev1_1024')
    oldvectors=dense.collection.get(include=['documents','metadatas','embeddings'])
    dense.collection.upsert(ids=[c['chunk_id'] for c in new],documents=[c['text'] for c in new],
        metadatas=[clean_metadata(c['metadata']) for c in new],embeddings=backend.embed([c['text'] for c in new]))
    kept=dense.collection.get(ids=oldvectors['ids'],include=['documents','metadatas','embeddings'])
    def vector_map(data):
        return {cid:(data['documents'][i],data['metadatas'][i],data['embeddings'][i].tolist()) for i,cid in enumerate(data['ids'])}
    assert vector_map(kept)==vector_map(oldvectors)
    chunks=[c for n in ('chunks','cases','guides') for c in load_chunks(snapshot/f'data/chunks/{n}.jsonl')]
    civil=ChromaRetriever(backend,snapshot/'data/index/chroma_civil_kurev1_1024')
    candidate=RetrievalService(chunks,dense,civil_dense=civil)
    assert settings(svc)==settings(candidate)
    index_ids=[]
    for r in (dense,civil):
        content=r.collection.get(include=['documents','metadatas']); index_ids+=content['ids']
        for cid,body,meta in zip(content['ids'],content['documents'],content['metadatas']):
            assert body==candidate._chunks[cid]['text'] and meta==clean_metadata(candidate._chunks[cid]['metadata'])
    assert len(index_ids)==len(set(index_ids)) and set(index_ids)==set(candidate._chunks)
    previous={(r['qid'],r['mode']):r for r in read(ROOT/'data/eval/patch024-expansion/results.json')}
    for r in read(ROOT/'data/eval/patch025-ranking/live-verification.json')['rows']:
        previous[r['qid'],r['mode']]['civil_laws']=r['civil_laws']
    before=[]; after=[]
    for q in read(ROOT/'data/eval/patch015-baseline/capture/results.json'):
        for service,rows in ((svc,before),(candidate,after)):
            result=service.search(q['query'],**SEARCH_K)
            anchor=lambda e:norm(service._chunks[e.chunk_id]['metadata']['article_id'])
            rows.append({'qid':q['qid'],'mode':q['mode'],'laws':[anchor(e) for e in result.laws],
                'civil_laws':[anchor(e) for e in result.civil_laws], 'cases':[e.chunk_id for e in result.cases],
                'guides':[e.chunk_id for e in result.guides]})
        assert before[-1]==previous[q['qid'],q['mode']], 'Baseline drift'
        assert all(before[-1][k]==after[-1][k] for k in ('civil_laws','cases','guides')), 'Other channel changed'
        if len(before)%25==0: print(f'{len(before)}/235 before/after',flush=True)
    available={norm(c['metadata']['article_id']) for c in chunks if c['metadata'].get('doc_type') in ('law','decree','rule')}
    report=score(after,available,before)
    assert all(sha(ROOT/p)==v for p,v in datafiles.items())
    assert before_digests==[index_digest(r) for r in (svc.dense,svc.civil_dense)]
    assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip()
    write(out/'before.json',before);write(out/'after.json',after);write(out/'report.json',report)
    write(out/'audit.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'clean':True,
        'settings':settings(svc),'baseline_matches':len(before),'source_files':{p.relative_to(bundle).as_posix():sha(p) for p in bundle.rglob('*') if p.is_file()},
        'data_hashes':datafiles,'index_semantic_before_after':before_digests,'old_vectors_preserved':True,
        'available_before':sorted(available-newids),'available_after':sorted(available),'other_channels_preserved':True,
        'adoption_gate_passed':not report['losses'],'candidate_only':True})
    print('Prior required losses:',len(report['losses']),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    if a.out.exists() or not a.out.resolve().is_relative_to(ROOT/'tmp'): p.error('Use a new tmp directory')
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',LANGSMITH_TRACING='false',ANONYMIZED_TELEMETRY='False')
    run(a.out)
