"""Full reviewed-law candidate ingestion and frozen 235-input retrieval capture."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from collections import Counter
from dataclasses import replace
from pathlib import Path

from scripts.patch015_baseline import ROOT, read, sha, norm
from scripts.patch026_full_sources import OUT, write, compile_records
from scripts.patch026_expand import score
from scripts.patch025_ranking import index_digest
from src.ingestion.load_laws import read_records, load_records, export_chunks
from src.retrieval.retriever import load_chunks


def rank_changes(before, after, details):
    """Same inputs/targets/denominators; never hide a TOP3 loss behind TOP5."""
    targets={(d['qid'],d['mode']):set(d['targets']) for d in details
             if d['category'] not in ('historical_review','no_fixed_target')}
    old={(r['qid'],r['mode']):r for r in before}
    changes={channel:{str(k):[] for k in ks} for channel,ks in
             [('laws',(1,3,5)),('civil_laws',(1,3))]}
    for row in after:
        key=row['qid'],row['mode']; gold=targets.get(key,set())
        for channel,ks in changes.items():
            for k,entries in ks.items():
                prev=set(old[key][channel][:int(k)]) & gold
                now=set(row[channel][:int(k)]) & gold
                if prev!=now:
                    entries.append({'qid':row['qid'],'mode':row['mode'],
                                    'lost':sorted(prev-now),'gained':sorted(now-prev)})
    return changes


def vector_map(data):
    return {cid:(data['documents'][i],data['metadatas'][i],data['embeddings'][i].tolist())
            for i,cid in enumerate(data['ids'])}


def source_hashes():
    files=[p for tree in ('src','scripts') for p in (ROOT/tree).rglob('*.py')]
    files += [ROOT/p for p in ('data/eval/patch026-scope/plan.json',
        'data/eval/dev100-v2/source-registry.json','data/eval/dev100-v2/requirements.json',
        'data/eval/dev100-v2/supplement-claims.json','data/eval/patch024-expansion/report.json',
        'data/eval/patch015-baseline/capture/results.json','data/eval/patch026-expansion/before.json')]
    files += [OUT/'specs.json',OUT/'records.jsonl']
    files += list((OUT/'sources').glob('*.html'))
    files += list((ROOT/'data/eval/patch026-expansion/sources').glob('*.html'))
    files += [ROOT/'data/eval/patch026-expansion'/n for n in ('records.jsonl','source-manifest.json')]
    return {p.relative_to(ROOT).as_posix():sha(p) for p in files}


def run(out):
    from src.retrieval.service import RetrievalService, CIVIL, route_law_corpus
    from src.retrieval.dense import ChromaRetriever
    from src.retrieval.index import clean_metadata
    from src.evaluation.baseline import SEARCH_K, settings
    from scripts.patch026_sources import SPECS, parse_page
    if out.exists() or not out.resolve().is_relative_to(ROOT/'tmp'):
        raise ValueError('Use a new candidate directory under tmp')
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip():
        raise ValueError('Commit source and provenance before capture')
    extra=compile_records()
    initial=read_records(ROOT/'data/eval/patch026-expansion/records.jsonl')
    initial_manifest=read(ROOT/'data/eval/patch026-expansion/source-manifest.json')
    assert len(initial)==len(SPECS)==len(initial_manifest)==5
    for record,spec,entry in zip(initial,SPECS,initial_manifest):
        path=ROOT/f'data/eval/patch026-expansion/sources/{spec[0]}.html'
        assert sha(path)==entry['sha256']
        assert record==parse_page(path.read_text(encoding='utf-8'),spec,entry['url'],path.relative_to(ROOT).as_posix())
    records=initial+extra
    frozen=source_hashes()
    out.mkdir(parents=True)
    for name in ('chunks','database','index'):
        shutil.copytree(ROOT/'data'/name,out/'candidate/data'/name)
    datafiles={p.relative_to(ROOT).as_posix():sha(p) for tree in ('chunks','database')
               for p in (ROOT/'data'/tree).rglob('*') if p.is_file()}
    snapshot=out/'candidate/data'
    db=sqlite3.connect(snapshot/'database/knowledge.sqlite3'); db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    oldcontent={r['article_id']:r['content'] for r in db.execute('SELECT article_id,content FROM law_articles')}
    assert len(oldcontent)==143
    loaded=load_records(records,db)
    assert not loaded.skipped,loaded.skipped
    newcontent={r['article_id']:r['content'] for r in db.execute('SELECT article_id,content FROM law_articles')}
    assert len(newcontent)==204 and all(newcontent[k]==v for k,v in oldcontent.items())
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    units=[]
    for row in db.execute('SELECT * FROM law_article_units ORDER BY article_id,ordinal'):
        unit=dict(row)
        if unit['article_id'] not in oldcontent:
            # Offsets are relative to original article content, never search-header text.
            units.append(unit)
    export_chunks(db,out/'export.jsonl'); db.close()
    newids={norm(r.law_name+'-'+r.article_number) for r in records}
    assert len(newids)==61
    new=[c for c in load_chunks(out/'export.jsonl') if norm(c['metadata']['article_id']) in newids]
    assert len(new)==61 and len({c['chunk_id'] for c in new})==61
    with (snapshot/'chunks/chunks.jsonl').open('ab') as stream:
        for chunk in new: stream.write((json.dumps(chunk,ensure_ascii=False)+'\n').encode('utf-8'))
    baseline=RetrievalService.from_index(); backend=baseline.dense.backend
    model_manifest=read(ROOT/'data/eval/patch015-baseline/capture/manifest.json')['model_files']
    modelroot=Path.home()/'.cache/huggingface/hub/models--nlpai-lab--KURE-v1'
    assert all(sha(modelroot/p)==v for p,v in model_manifest.items())
    original=backend.embed; cache={}
    def cached(texts):
        key=tuple(texts)
        if key not in cache: cache[key]=original(texts)
        return cache[key]
    backend.embed=cached
    index_before=[index_digest(r) for r in (baseline.dense,baseline.civil_dense)]
    dense=ChromaRetriever(backend,snapshot/'index/chroma_kurev1_1024')
    civil_dense=ChromaRetriever(backend,snapshot/'index/chroma_civil_kurev1_1024')
    for retriever,is_civil in ((dense,False),(civil_dense,True)):
        oldvectors=retriever.collection.get(include=['documents','metadatas','embeddings'])
        additions=[c for c in new if (c['metadata']['title']=='민법')==is_civil]
        print('Embedding new', 'civil' if is_civil else 'general',len(additions),flush=True)
        retriever.collection.upsert(ids=[c['chunk_id'] for c in additions],
            documents=[c['text'] for c in additions],metadatas=[clean_metadata(c['metadata']) for c in additions],
            embeddings=backend.embed([c['text'] for c in additions]))
        assert vector_map(oldvectors)==vector_map(retriever.collection.get(ids=oldvectors['ids'],include=['documents','metadatas','embeddings']))
    chunks=[c for name in ('chunks','cases','guides') for c in load_chunks(snapshot/f'chunks/{name}.jsonl')]
    civil_ids=tuple(sorted(c['metadata']['article_id'] for c in chunks if c['metadata']['title']=='민법'))
    assert len(civil_ids)==26
    candidate=RetrievalService(chunks,dense,civil=replace(CIVIL,include_ids=civil_ids),civil_dense=civil_dense)
    indexed=[]
    for retriever in (dense,civil_dense):
        content=retriever.collection.get(include=['documents','metadatas'])
        indexed+=content['ids']
        for cid,body,meta in zip(content['ids'],content['documents'],content['metadatas']):
            assert body==candidate._chunks[cid]['text'] and meta==clean_metadata(candidate._chunks[cid]['metadata'])
    assert len(indexed)==len(set(indexed)) and set(indexed)==set(candidate._chunks)
    available_before={norm(c['metadata']['article_id']) for c in baseline._chunks.values() if c['metadata']['doc_type'] in ('law','decree','rule')}
    available={norm(c['metadata']['article_id']) for c in chunks if c['metadata']['doc_type'] in ('law','decree','rule')}
    expected=read(ROOT/'data/eval/patch026-expansion/before.json')
    queries=read(ROOT/'data/eval/patch015-baseline/capture/results.json')
    assert len(queries)==len(expected)==235
    before=[];after=[];traces=[]
    for q in queries:
        assert hashlib.sha256(q['query'].encode()).hexdigest()==q['query_sha256']
        for service,rows in ((baseline,before),(candidate,after)):
            result=service.search(q['query'],**SEARCH_K)
            anchor=lambda cid:norm(service._chunks[cid]['metadata']['article_id'])
            rows.append({'qid':q['qid'],'mode':q['mode'],'laws':[anchor(e.chunk_id) for e in result.laws],
                'civil_laws':[anchor(e.chunk_id) for e in result.civil_laws],
                'cases':[e.chunk_id for e in result.cases],'guides':[e.chunk_id for e in result.guides]})
        assert before[-1]==expected[len(before)-1],f'Baseline drift: {q["qid"]}'
        assert all(before[-1][k]==after[-1][k] for k in ('cases','guides'))
        anchor=lambda cid:norm(candidate._chunks[cid]['metadata']['article_id'])
        trace={'qid':q['qid'],'mode':q['mode'],'query_sha256':q['query_sha256']}
        for name,corpus,k in [('general',route_law_corpus(q['query'],candidate.corpora[0]),5),('civil',candidate.civil,len(civil_ids))]:
            hits,members=candidate._search_one_with_member_hits(corpus,q['query'],k)
            trace[name]={'rrf':[anchor(e.chunk_id) for e in hits],
                         'members':{n:[anchor(cid) for cid in ids] for n,ids in members.items()}}
            if name=='general': assert trace[name]['rrf']==after[-1]['laws']
        traces.append(trace)
        if len(before)%25==0: print(f'{len(before)}/235 before/after and traces',flush=True)
    report=score(after,available,before)
    report['baseline']=score(before,available_before,before)['groups']
    report['rank_changes']=rank_changes(before,after,report['details'])
    assert all(sha(ROOT/p)==v for p,v in datafiles.items())
    assert index_before==[index_digest(r) for r in (baseline.dense,baseline.civil_dense)]
    assert source_hashes()==frozen
    assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip()
    for name,value in [('before',before),('after',after),('report',report),('traces',traces),('new-chunks',new),('new-units',units)]:
        write(out/f'{name}.json',value)
    write(out/'audit.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'clean':True,
        'source_hashes':frozen,'data_hashes':datafiles,'model_files':model_manifest,
        'baseline_settings':settings(baseline),'candidate_settings':settings(candidate),
        'available_before':sorted(available_before),'available_after':sorted(available),
        'baseline_matches':len(before),'cases_guides_preserved':True,'old_articles_and_vectors_preserved':True,
        'operating_index_hashes':index_before,'candidate_index_hashes':[index_digest(r) for r in (dense,civil_dense)],
        'candidate_files':{p.relative_to(snapshot).as_posix():sha(p) for d in ('chunks','database') for p in (snapshot/d).rglob('*') if p.is_file()},
        'new_structure_units':dict(Counter(u['unit_type'] for u in units)),
        'candidate_only':True,'algorithm':'existing partition for five procedure titles; expanded civil allowlist only; no new query tuning',
        'scope':'reviewed current-law article corpus; not all statutory cross-references, historical versions, appendices, cases or guides'})
    print('Completed',len(after),'inputs; prior TOP5/general+TOP3/civil losses',len(report['losses']),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',LANGSMITH_TRACING='false',ANONYMIZED_TELEMETRY='False')
    run(args.out)
