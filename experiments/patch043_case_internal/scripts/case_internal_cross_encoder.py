"""Local DEV cross-encoder experiment, one model loaded once per process."""
import argparse,json,os,time,multiprocessing as mp
from pathlib import Path
from experiments.patch043_case_internal.scripts.case_internal_run import sha,write


def execute_worker(pipe,args):
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    import torch,psutil
    from experiments.patch043_case_internal.retrieval import LocalCaseCrossEncoder
    torch.set_num_threads(8)
    start=time.perf_counter();model=LocalCaseCrossEncoder(str(args.model),max_length=args.max_length,batch_size=4)
    chunks={c['chunk_id']:c for c in [json.loads(l) for l in (args.data/'chunks/cases.jsonl').read_text('utf-8').splitlines() if l]}
    korean='임대차 계약이 끝났는데 보증금을 돌려받지 못했습니다.'
    tokens=model.tokenizer(korean)['input_ids']
    unknown=tokens.count(model.tokenizer.unk_token_id)
    pipe.send({'event':'ready','initialization_seconds':time.perf_counter()-start,'torch':torch.__version__,
        'device':'cpu','rss_bytes':psutil.Process().memory_info().rss,'korean_sample':korean,
        'korean_tokens':len(tokens),'korean_unknown_tokens':unknown,'unknown_ratio':unknown/max(1,len(tokens)),
        'korean_processing_note':'Tokenization observation only; retrieval quality is measured separately on Korean DEV.'})
    while True:
        r=pipe.recv()
        if r is None:break
        hits=r['hits'];row={'qid':r['qid'],'query':r['query'],'error':None}
        start=time.perf_counter()
        try:
            values=model.score(r['query'],[chunks[cid]['text'] for cid,_ in hits])
            row.update(scores=values,truncated_pairs=model.last_truncated_pairs)
        except Exception as error:row.update(scores=[],error={'type':type(error).__name__,'message':str(error)})
        row.update(latency_seconds=time.perf_counter()-start,rss_bytes=psutil.Process().memory_info().rss)
        pipe.send(row)


def worker(pipe,args):
    try:execute_worker(pipe,args)
    except BaseException as error:
        pipe.send({'event':'worker_error','type':type(error).__name__,'message':str(error)})
    finally:pipe.close()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--inputs',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True);ap.add_argument('--model',type=Path,required=True)
    ap.add_argument('--max-length',type=int,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    write(args.output/'INPUT_SEAL.json',{'candidate_inputs_sha256':sha(args.inputs),'chunks_sha256':sha(args.data/'chunks/cases.jsonl'),
        'model_files':{p.name:sha(p) for p in args.model.iterdir() if p.is_file()},'gold_loaded':False,
        'max_length':args.max_length,'batch_size':4,'device':'cpu','runner_sha256':sha(__file__),
        'torch_threads':8,'cross_encoder_code_sha256':sha(Path(__file__).resolve().parents[1]/'retrieval.py'),
        'question_timeout_seconds':120,'initialization_timeout_seconds':240,
        'timeout_basis':'Same bounds as the preserved application evaluation runner.'})
    parent,child=mp.Pipe();process=mp.Process(target=worker,args=(child,args));process.start()
    current=None
    def receive(seconds):
        if not parent.poll(seconds):raise TimeoutError('local cross-encoder execution timeout')
        return parent.recv()
    try:
        runtime=receive(240);write(args.output/'runtime.json',runtime)
        if runtime.get('event')!='ready':raise RuntimeError(str(runtime))
        with (args.output/'scores.jsonl').open('x',encoding='utf-8') as stream:
            for i,r in enumerate(json.loads(args.inputs.read_text('utf-8')),1):
                current=r['qid'];parent.send(r);row=receive(120)
                if row.get('event')=='worker_error':raise RuntimeError(str(row))
                stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
                print(json.dumps({'phase':'cross_encoder','model':args.model.name,'processed':i,'latency':row['latency_seconds'],'error':row['error']}),flush=True)
                if row['error']:raise RuntimeError(str(row['error']))
        parent.send(None);process.join(10)
    except BaseException as error:
        write(args.output/'EXECUTION_FAILURE.json',{'type':type(error).__name__,'message':str(error),
            'qid':current,'decision':'exclude this model execution candidate; no silent fallback or fabricated remaining scores'})
        raise
    finally:
        if process.is_alive():process.terminate();process.join(5)
        write(args.output/'RESULT_SEAL.json',{p.name:sha(p) for p in args.output.iterdir() if p.is_file() and p.name!='RESULT_SEAL.json'})


if __name__=='__main__':mp.freeze_support();main()
