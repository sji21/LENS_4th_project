import os,sys,json,argparse
from pathlib import Path
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',ANONYMIZED_TELEMETRY='False',LANGSMITH_TRACING='false')
sys.stdout.reconfigure(encoding='utf-8');sys.path.insert(0,str(Path.cwd()))
from src.retrieval.service import RetrievalService
from src.retrieval.retriever import load_chunks
from src.evaluation.baseline import load_dataset,prepare_questions,evaluate
parser=argparse.ArgumentParser(description='Compare public law metrics and rankings with a saved PATCH-008 report.')
parser.add_argument('--run-dir',required=True,type=Path)
parser.add_argument('--baseline-report',required=True,type=Path)
args=parser.parse_args();out=args.run_dir;snapshot=out/'snapshot'
paths=tuple(snapshot/'data/chunks'/f'{n}.jsonl' for n in ['chunks','cases','guides'])
service=RetrievalService.from_index(chunk_paths=paths,index_path=snapshot/'data/index/chroma_kurev1_1024',civil_index_path=snapshot/'data/index/chroma_civil_kurev1_1024')
chunks=[c for p in paths for c in load_chunks(p)]
baseline=json.loads(args.baseline_report.read_text(encoding='utf-8'))['results']
results={}
for split in ['dev','holdout','civil_published_regression']:
    path=Path('data/eval')/(f'{split}.jsonl' if split!='civil_published_regression' else 'minbeop_review_holdout_20260901.jsonl')
    rows=load_dataset(path,'law')
    rows=[{**r,'answer_type':'unanswerable'} if r.get('answer_type')=='abstain' else r for r in rows]
    selected,excluded=prepare_questions(rows,chunks,'law')
    result=evaluate(service,selected,chunks,'law')
    old=baseline[split]['after'] if split!='civil_published_regression' else baseline[split]
    assert result['metrics']==old['metrics'],split
    assert [(r['qid'],r['retrieved_ids']) for r in result['questions']]==[(r['qid'],r['retrieved_ids']) for r in old['questions']],split
    result['excluded']=excluded;results[split]=result
    print(split,result['metrics'],flush=True)
with (out/'public-regression.json').open('x',encoding='utf-8') as f:json.dump(results,f,ensure_ascii=False,indent=2)
