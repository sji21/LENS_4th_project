"""Compare the two coherent subsets of the reviewed five-article bundle."""
import os
import shutil
import sqlite3
import subprocess
import json
from pathlib import Path

from scripts.patch026_expand import ROOT, read, write, sha, norm, score, settings, SEARCH_K
from src.ingestion.load_laws import read_records, load_records
from src.retrieval.retriever import load_chunks
from src.retrieval.service import RetrievalService, DEFAULT_MODEL
from src.retrieval.dense import SentenceTransformerEmbedding, ChromaRetriever
from scripts.patch025_ranking import index_digest


def run():
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip(): raise ValueError('Commit first')
    source=ROOT/'tmp/patch026-evaluation'
    out=ROOT/'tmp/patch026-subsets-v2'; out.mkdir(exist_ok=False)
    before=read(source/'before.json'); records=read_records(ROOT/'data/eval/patch026-expansion/records.jsonl')
    allnew={norm(r.law_name+'-'+r.article_number) for r in records}
    newchunks=[c for c in load_chunks(source/'export.jsonl') if norm(c['metadata']['article_id']) in allnew]
    backend=SentenceTransformerEmbedding(DEFAULT_MODEL); original=backend.embed; cache={}
    def cached(texts):
        key=tuple(texts)
        if key not in cache: cache[key]=original(texts)
        return cache[key]
    backend.embed=cached
    configs={}; basechunks=[c for name in ('chunks','cases','guides') for c in load_chunks(ROOT/f'data/chunks/{name}.jsonl')]
    for name in ('tax','residence'):
        selected=[r for r in records if (r.law_name=='주민등록법')==(name=='residence')]
        ids={norm(r.law_name+'-'+r.article_number) for r in selected}
        new=[c for c in newchunks if norm(c['metadata']['article_id']) in ids]
        folder=out/name; folder.mkdir()
        shutil.copy2(ROOT/'data/database/knowledge.sqlite3',folder/'knowledge.sqlite3')
        db=sqlite3.connect(folder/'knowledge.sqlite3'); db.row_factory=sqlite3.Row; db.execute('PRAGMA foreign_keys=ON')
        assert not load_records(selected,db).skipped
        assert db.execute('SELECT count(*) FROM law_articles').fetchone()[0]==143+len(new)
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall()
        db.close()
        shutil.copytree(source/'candidate/data/index/chroma_kurev1_1024',folder/'general-index')
        shutil.copytree(ROOT/'data/index/chroma_civil_kurev1_1024',folder/'civil-index')
        dense=ChromaRetriever(backend,folder/'general-index')
        dense.collection.delete(ids=[c['chunk_id'] for c in newchunks if c not in new])
        civil=ChromaRetriever(backend,folder/'civil-index')
        svc=RetrievalService(basechunks+new,dense,civil_dense=civil)
        assert dense.collection.count()+civil.collection.count()==len(svc._chunks)
        configs[name]=(svc,new,[])
    for q,old in zip(read(ROOT/'data/eval/patch015-baseline/capture/results.json'),before):
        assert (q['qid'],q['mode'])==(old['qid'],old['mode'])
        for name,(svc,new,rows) in configs.items():
            r=svc.search(q['query'],**SEARCH_K)
            anchor=lambda e:norm(svc._chunks[e.chunk_id]['metadata']['article_id'])
            row={'qid':q['qid'],'mode':q['mode'],'laws':[anchor(e) for e in r.laws],
                 'civil_laws':[anchor(e) for e in r.civil_laws],'cases':[e.chunk_id for e in r.cases],'guides':[e.chunk_id for e in r.guides]}
            assert all(row[k]==old[k] for k in ('civil_laws','cases','guides'))
            rows.append(row)
        if len(rows)%25==0: print(f'{len(rows)}/235 subset comparisons',flush=True)
    assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip()
    for name,(svc,new,rows) in configs.items():
        available={norm(c['metadata']['article_id']) for c in svc._chunks.values() if c['metadata'].get('doc_type') in ('law','decree','rule')}
        report=score(rows,available,before)
        write(out/name/'after.json',rows);write(out/name/'report.json',report)
        write(out/name/'audit.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'clean':True,
            'added':sorted(allnew & available),'available':sorted(available),'settings':settings(svc),
            'baseline_file_sha256':sha(source/'before.json'),'candidate_only':True,'queries':len(rows),
            'general_and_civil_index_semantic_hashes':[index_digest(r) for r in (svc.dense,svc.civil_dense)]})
        print(name,'losses',len(report['losses']),flush=True)


if __name__=='__main__':
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',LANGSMITH_TRACING='false',ANONYMIZED_TELEMETRY='False')
    run()
