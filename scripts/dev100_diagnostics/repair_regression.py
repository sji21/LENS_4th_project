import os,sys,json,shutil,hashlib
from pathlib import Path
from dataclasses import asdict
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',ANONYMIZED_TELEMETRY='False',LANGSMITH_TRACING='false')
sys.stdout.reconfigure(encoding='utf-8');sys.path.insert(0,str(Path.cwd()))
from src.retrieval.service import RetrievalService
root=Path.cwd().parent/'질문제작평가/3. 구현 담당 AI — 범위 점검과 데이터 부족 분석부터/patch009-dev100-20260909'
out=Path('tmp/patch010');snapshot=out/'snapshot'
shutil.copytree(root/'snapshot',snapshot)
rows=json.loads((root/'results.json').read_text(encoding='utf-8'))
paths=tuple(snapshot/'data/chunks'/f'{n}.jsonl' for n in ['chunks','cases','guides'])
service=RetrievalService.from_index(chunk_paths=paths,index_path=snapshot/'data/index/chroma_kurev1_1024',civil_index_path=snapshot/'data/index/chroma_civil_kurev1_1024')
all_results=[]; changed=[]
for row in rows:
    for mode,old in row['modes'].items():
        new=asdict(service.search(old['query'],k_law=5,k_case=5,k_guide=2))
        differences={kind:[e['chunk_id'] for e in new[kind]]!=[e['chunk_id'] for e in old['result'][kind]] for kind in ['laws','cases','guides']}
        record={'qid':row['qid'],'mode':mode,'query':old['query'],'result':new,'changes':differences}
        all_results.append(record)
        assert not differences['cases'] and not differences['guides']
        if any(differences.values()):changed.append({'qid':row['qid'],'mode':mode,'before':[e['citation'] for e in old['result']['laws']],'after':[e['citation'] for e in new['laws']]})
    if len(all_results)%20==0:print(len(all_results),'of 200',flush=True)
assert len(all_results)==200
assert {(r['qid'],r['mode']) for r in changed}=={('DEV-006','question_only'),('DEV-006','context_diagnostic')}
assert all('제626조' in r['result']['laws'][0]['citation'] for r in all_results if r['qid']=='DEV-006')
payload={'baseline_sha256':hashlib.sha256((root/'results.json').read_bytes()).hexdigest(),'input_count':200,'changed':changed,'results':all_results,'cases_and_guides_unchanged':True,'purpose':'published development regression; not independent evaluation'}
(out/'dev100-after.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(changed,ensure_ascii=False),flush=True)
