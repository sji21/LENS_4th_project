"""Capture published development inputs without generation or gold rewriting."""
import os
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', ANONYMIZED_TELEMETRY='False', LANGSMITH_TRACING='false')
import sys, json, hashlib, shutil, sqlite3, subprocess, platform, argparse
from pathlib import Path
from dataclasses import asdict
from datetime import datetime, timezone
sys.stdout.reconfigure(encoding='utf-8'); repo=Path.cwd(); sys.path.insert(0,str(repo))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--dataset',type=Path,default=Path('data/eval/dev100'))
parser.add_argument('--out',type=Path,default=Path('tmp/dev100-run'))
args=parser.parse_args(); dataset=args.dataset; out=args.out
if out.resolve().is_relative_to((repo/'data').resolve()): parser.error('Output must be outside data/')
out.mkdir(parents=True,exist_ok=False)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(name,value):
    with (out/name).open('x',encoding='utf-8') as f: json.dump(value,f,ensure_ascii=False,indent=2)
for name,digest in json.loads((dataset/'manifest.json').read_text(encoding='utf-8'))['sha256'].items():
    if sha(dataset/name)!=digest: raise ValueError(f'Dataset hash mismatch: {name}')
rows=json.loads((dataset/'questions.json').read_text(encoding='utf-8'))
requirements=json.loads((dataset/'requirements.json').read_text(encoding='utf-8'))
rankings=json.loads((dataset/'pre_patch008_law_rankings.json').read_text(encoding='utf-8'))
assert [q['qid'] for q in rows]==[f'DEV-{n:03}' for n in range(1,101)]
assert [q['qid'] for q in requirements]==[q['qid'] for q in rows]
assert all(a['question']==b['question'] for a,b in zip(rows,requirements))
for p in [*dataset.glob('*.json'),dataset/'gold-review.md',Path(__file__)]:
    shutil.copy2(p,out/('dataset-manifest.json' if p.name=='manifest.json' else p.name))
sources=[repo/'data/database/knowledge.sqlite3',*[repo/'data/chunks'/f'{n}.jsonl' for n in ['chunks','cases','guides','civil']],*[p for n in ['chroma_kurev1_1024','chroma_civil_kurev1_1024'] for p in (repo/'data/index'/n).rglob('*') if p.is_file()]]
before={str(p.relative_to(repo)):sha(p) for p in sources}
snapshot=out/'snapshot'
for p in sources:
    dst=snapshot/p.relative_to(repo); dst.parent.mkdir(parents=True,exist_ok=True)
    if p.parent.name=='database':
        with sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(dst) as b:a.backup(b)
    else:shutil.copy2(p,dst)
from src.retrieval.service import RetrievalService,DEFAULT_MODEL
from src.retrieval.retriever import load_chunks
from src.retrieval.index import clean_metadata
from src.evaluation.baseline import settings
paths=tuple(snapshot/'data/chunks'/f'{n}.jsonl' for n in ['chunks','cases','guides'])
chunks=[c for p in paths for c in load_chunks(p)]; expected={c['chunk_id']:c for c in chunks}
service=RetrievalService.from_index(chunk_paths=paths,index_path=snapshot/'data/index/chroma_kurev1_1024',civil_index_path=snapshot/'data/index/chroma_civil_kurev1_1024')
seen=[]
for retriever in [service.dense,service.civil_dense]:
    data=retriever.collection.get(include=['documents','metadatas'])
    for cid,body,meta in zip(data['ids'],data['documents'],data['metadatas']):
        assert body==expected[cid]['text'] and meta==clean_metadata(expected[cid]['metadata'])
        seen.append(cid)
assert len(seen)==len(set(seen))==len(expected)==172
with sqlite3.connect(snapshot/'data/database/knowledge.sqlite3') as con:
    con.row_factory=sqlite3.Row
    inventory=[dict(r) for r in con.execute('SELECT l.law_name,a.article_number,a.content,v.effective_from FROM law_articles a JOIN law_versions v USING(law_version_id) JOIN laws l USING(law_id)')]
    assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not con.execute('PRAGMA foreign_key_check').fetchall()
save('db-inventory.json',inventory)
git=lambda *args:subprocess.check_output(['git',*args],encoding='utf-8').strip()
save('manifest.json',{'created_at':datetime.now(timezone.utc).isoformat(),'commit':git('rev-parse','HEAD'),'branch':git('branch','--show-current'),'status':git('status','--porcelain'),'python':platform.python_version(),'model':DEFAULT_MODEL,'settings':settings(service),'actual_search_k':{'law':5,'case':5,'guide':2},'input_hashes':before,'questions_sha256':sha(dataset/'questions.json'),'requirements_sha256':sha(dataset/'requirements.json'),'gold_sha256':sha(dataset/'gold-review.md'),'model_config_files':{str(p):sha(p) for p in (Path.home()/'.cache/huggingface/hub').glob('models--nlpai-lab--KURE-v1/snapshots/*/config.json')},'generation_performed':False,'purpose':'Published development retrieval diagnostics, not independent evaluation or final legal scoring','query_policy':'Exact queries from corrected earlier capture; context is diagnostic supplied text, not application conversation rewriting','code_hashes':{str(p.relative_to(repo)):sha(p) for p in (repo/'src/retrieval').glob('*.py')}})
results=[]
for q,gold in zip(rows,requirements):
    result={'qid':q['qid'],'question':q['question'],'gold':gold,'modes':{}}
    for mode,previous in q['modes'].items():
        query=previous['query']; raw=service.search(query,k_law=5,k_case=5,k_guide=2)
        result['modes'][mode]={'query':query,'result':asdict(raw),'article_ids':[expected[e.chunk_id]['metadata'].get('article_id','').replace(' ','') for e in raw.laws],'law_ranking_changed':[e.chunk_id for e in raw.laws]!=rankings[q['qid']][mode]}
    results.append(result)
    with (out/'progress.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
    if len(results)%10==0: print(f'{len(results)}/100 captured',flush=True)
save('results.json',results)
assert before=={str(p.relative_to(repo)):sha(p) for p in sources}
save('audit.json',{'items':len(results),'queries':sum(len(r['modes']) for r in results),'index_records_verified':172,'operating_files_unchanged':True,'exact_shared_queries':True,'gold_unchanged':sha(dataset/'gold-review.md')==sha(out/'gold-review.md')})
print('CAPTURE COMPLETE',flush=True)
