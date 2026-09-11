"""Prepare a three-article Civil Act candidate and measure it without changing operating data."""
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

from scripts.patch015_baseline import ROOT, read, write, sha, norm
from scripts.patch023_report import diagnose, summarize
from src.ingestion.fetch_law_mock import parse_articles, parse_law_header, ENDPOINT
from src.ingestion.load_laws import LawArticleRecord, load_records, export_chunks, write_records
from src.retrieval.service import CIVIL, RetrievalService, DEFAULT_MODEL
from src.retrieval.retriever import load_chunks
from src.retrieval.index import clean_metadata
from src.evaluation.baseline import SEARCH_K, settings

ADDED = ('제105조', '제114조', '제357조')


def records_from_source(text):
    header = parse_law_header(text)
    if header['effective_from'] != '2026-03-17' or '21454' not in header['proclamation_number']:
        raise ValueError('Unexpected Civil Act version')
    parsed = {n:(title,body) for n,title,body in parse_articles(text)}
    numbers = tuple(a.split('-')[1] for a in CIVIL.include_ids) + ADDED
    if len(set(numbers)) != 10 or any(n not in parsed for n in numbers):
        raise ValueError('Unexpected article selection')
    return [LawArticleRecord(law_name='민법',law_type='법률',ministry=header['ministry'] or '법무부',
        law_code='284415',proclamation_number=header['proclamation_number'],
        proclaimed_at=header['proclaimed_at'],effective_from=header['effective_from'],
        content=parsed[n][1],article_number=n,article_title=parsed[n][0],
        source_url=('https://www.law.go.kr/LSW/lsSideInfoP.do?docCls=jo&joBrNo=00&joNo='
                    + n[1:-1].zfill(4) + '&lsiSeq=284415&urlMode=lsScJoRltInfoR'),
        collected_at='2026-09-11',file_path='data/raw/law/민법-20260317.txt',
        source_text=text if i==0 else '',source_document_url=ENDPOINT.format(seq='284415',eff='20260317'),
        source_version_id='284415') for i,n in enumerate(numbers)]


def run(out):
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip():
        raise ValueError('Run from a clean committed tree')
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',LANGSMITH_TRACING='false',ANONYMIZED_TELEMETRY='False')
    baseline=ROOT/'data/eval/patch023-baseline'
    for rel,digest in read(baseline/'bundle-manifest.json')['files'].items():
        if sha(baseline/rel)!=digest: raise ValueError('Baseline bundle changed')
    oldaudit=read(baseline/'capture/audit.json')
    before={p:sha(ROOT/p) for p in oldaudit['operating_after']}
    if before != oldaudit['operating_after']: raise ValueError('Operating data changed since PATCH-023')
    out.mkdir(parents=True,exist_ok=False)
    snapshot=out/'candidate'
    for rel in before:
        target=snapshot/rel; target.parent.mkdir(parents=True,exist_ok=True)
        if rel.endswith('knowledge.sqlite3'):
            with sqlite3.connect((ROOT/rel).as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(target) as dst:
                src.backup(dst)
        else: shutil.copy2(ROOT/rel,target)
    text=(ROOT/'data/raw/law/민법-20260317.txt').read_text(encoding='utf-8')
    records=records_from_source(text)
    write_records(records,out/'records.jsonl')
    db=sqlite3.connect(snapshot/'data/database/knowledge.sqlite3'); db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    oldrows={r['article_id']:r['content'] for r in db.execute('SELECT article_id,content FROM law_articles')}
    loaded=load_records(records,db)
    if loaded.skipped: raise ValueError(loaded.skipped)
    nowrows={r['article_id']:r['content'] for r in db.execute('SELECT article_id,content FROM law_articles')}
    if len(nowrows)!=len(oldrows)+3 or any(nowrows.get(k)!=v for k,v in oldrows.items()):
        raise ValueError('Existing DB article bodies changed')
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Candidate DB integrity failure')
    export_chunks(db,out/'export.jsonl'); db.close()
    new=[c for c in load_chunks(out/'export.jsonl') if c['metadata']['article_id'] in {'민법-'+n for n in ADDED}]
    if len(new)!=3: raise ValueError('Expected three new chunks')
    basefile=snapshot/'data/chunks/chunks.jsonl'
    with basefile.open('ab') as f:
        if not basefile.read_bytes().endswith(b'\n'): f.write(b'\n')
        for c in new: f.write((json.dumps(c,ensure_ascii=False)+'\n').encode())
    civilfile=snapshot/'data/chunks/civil.jsonl'
    with civilfile.open('ab') as f:
        if not civilfile.read_bytes().endswith(b'\n'): f.write(b'\n')
        for c in new: f.write((json.dumps(c,ensure_ascii=False)+'\n').encode())
    from src.retrieval.dense import SentenceTransformerEmbedding, ChromaRetriever
    backend=SentenceTransformerEmbedding(DEFAULT_MODEL)
    civil_dense=ChromaRetriever(backend,snapshot/'data/index/chroma_civil_kurev1_1024')
    oldvectors=civil_dense.collection.get(include=['embeddings','documents','metadatas'])
    civil_dense.collection.upsert(ids=[c['chunk_id'] for c in new],documents=[c['text'] for c in new],
        metadatas=[clean_metadata(c['metadata']) for c in new],embeddings=backend.embed([c['text'] for c in new]))
    retained=civil_dense.collection.get(ids=oldvectors['ids'],include=['embeddings','documents','metadatas'])
    oldmap={cid:(oldvectors['documents'][i],oldvectors['metadatas'][i],oldvectors['embeddings'][i].tolist()) for i,cid in enumerate(oldvectors['ids'])}
    for i,cid in enumerate(retained['ids']):
        if oldmap[cid]!=(retained['documents'][i],retained['metadatas'][i],retained['embeddings'][i].tolist()):
            raise ValueError('Existing civil vectors changed')
    paths=tuple(snapshot/'data/chunks'/f'{n}.jsonl' for n in ('chunks','cases','guides'))
    chunks=[c for p in paths for c in load_chunks(p)]; byid={c['chunk_id']:c for c in chunks}
    dense=ChromaRetriever(backend,snapshot/'data/index/chroma_kurev1_1024')
    for retriever in (dense,civil_dense):
        found=retriever.collection.get(include=['documents','metadatas'])
        for cid,body,meta in zip(found['ids'],found['documents'],found['metadatas']):
            if body!=byid[cid]['text'] or meta!=clean_metadata(byid[cid]['metadata']): raise ValueError('Index/chunk mismatch')
    if dense.collection.count()+civil_dense.collection.count()!=len(byid): raise ValueError('Index coverage mismatch')
    candidate=replace(CIVIL,include_ids=CIVIL.include_ids+tuple('민법-'+n for n in ADDED))
    svc=RetrievalService(chunks,dense,civil=candidate,civil_dense=civil_dense)
    actual=json.loads(json.dumps(settings(svc)))
    expected=json.loads(json.dumps(oldaudit['settings']))
    expected['corpora']['civil']['include_ids'] += ['민법-'+n for n in ADDED]
    if actual != expected: raise ValueError('Non-membership settings changed')
    queries=read(ROOT/'data/eval/patch015-baseline/capture/results.json')
    previous={(r['qid'],r['mode']):r for r in read(baseline/'capture/results.json')}
    prior_details={(r['qid'],r['mode']):r for r in read(baseline/'report.json')['details']}
    available={norm(c['metadata']['article_id']) for c in chunks if c['metadata'].get('doc_type') in ('law','decree','rule')}
    rows=[]; details=[]
    for q in queries:
        result=svc.search(q['query'],**SEARCH_K)
        row={'qid':q['qid'],'mode':q['mode'],
             'laws':[norm(svc._chunks[e.chunk_id]['metadata']['article_id']) for e in result.laws],
             'civil_laws':[norm(svc._chunks[e.chunk_id]['metadata']['article_id']) for e in result.civil_laws],
             'cases':[e.chunk_id for e in result.cases],'guides':[e.chunk_id for e in result.guides]}
        old=previous[q['qid'],q['mode']]; target=prior_details[q['qid'],q['mode']]
        if any(row[k]!=old[k] for k in ('laws','cases','guides')): raise ValueError('Other channel regression')
        d=diagnose(target['targets'],available,row['laws'],row['civil_laws'],target['category']=='historical_review')
        oldhit=set(old['laws']+old['civil_laws']) & set(target['targets'])
        d.update(qid=q['qid'],mode=q['mode'],track=target['track'],
                 lost_prior_required=sorted(oldhit-set(row['laws']+row['civil_laws'])))
        rows.append(row); details.append(d)
        if len(rows)%25==0: print(f'{len(rows)}/235 compared',flush=True)
    if len(rows)!=235: raise ValueError('Incomplete run')
    after={p:sha(ROOT/p) for p in before}
    if before!=after: raise ValueError('Operating data mutated')
    groups={mode:summarize([r for r in details if r['track']=='dev100' and r['mode']==mode]) for mode in ('question_only','context_diagnostic')}
    for track in ('required_law','scope_provisional','diagnostic_only'):
        groups[track]=summarize([r for r in details if r['track']==track])
    losses=[{'qid':d['qid'],'mode':d['mode'],'lost':d['lost_prior_required']} for d in details if d['lost_prior_required']]
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip():
        raise ValueError('Source changed during run')
    write(out/'results.json',rows); write(out/'report.json',{'groups':groups,'losses':losses,'details':details})
    write(out/'audit.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'dirty':bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip()),
        'runner_lf_sha256':hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
        'source_sha256':sha(ROOT/'data/raw/law/민법-20260317.txt'),
        'before':before,'after':after,'settings':settings(svc),'old_civil_vectors_preserved':True,
        'other_channels_preserved':True,'added_articles':list(ADDED),'candidate_only':True,'queries':len(rows)})
    print('LOSSES',losses,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    if a.out.exists() or not a.out.resolve().is_relative_to(ROOT/'tmp'): p.error('Use a new tmp directory')
    run(a.out)
