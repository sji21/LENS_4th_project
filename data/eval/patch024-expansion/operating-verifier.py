import os
import subprocess
from scripts.patch015_baseline import ROOT, read, write, sha, norm
from src.retrieval.service import RetrievalService
from src.evaluation.baseline import SEARCH_K, settings
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',LANGSMITH_TRACING='false',ANONYMIZED_TELEMETRY='False')
out=ROOT/'tmp/patch024-expansion'
svc=RetrievalService.from_index()
import json
assert json.loads(json.dumps(settings(svc))) == read(out/'audit.json')['settings']
expected={(r['qid'],r['mode']):r for r in read(out/'results.json')}
for i,q in enumerate(read(ROOT/'data/eval/patch015-baseline/capture/results.json'),1):
    result=svc.search(q['query'],**SEARCH_K)
    row={'qid':q['qid'],'mode':q['mode'],
         'laws':[norm(svc._chunks[e.chunk_id]['metadata']['article_id']) for e in result.laws],
         'civil_laws':[norm(svc._chunks[e.chunk_id]['metadata']['article_id']) for e in result.civil_laws],
         'cases':[e.chunk_id for e in result.cases],'guides':[e.chunk_id for e in result.guides]}
    assert row==expected[q['qid'],q['mode']], (q['qid'],q['mode'])
    if i%25==0: print(f'{i}/235 operating match',flush=True)
write(out/'operating-verification.json',{'matches':i,'settings':settings(svc),'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'dirty':bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip()),'service_lf_sha256':__import__('hashlib').sha256((ROOT/'src/retrieval/service.py').read_bytes().replace(b'\r\n',b'\n')).hexdigest(),'verifier_lf_sha256':__import__('hashlib').sha256(__import__('pathlib').Path(__file__).read_bytes().replace(b'\r\n',b'\n')).hexdigest()})
print('All operating results match candidate')
