"""Actual application retrieval entrypoint, question-only isolated execution."""
import argparse,json,multiprocessing as mp,os,time
from pathlib import Path
from case_internal_run import sha,write


def worker(pipe,profile):
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',ANONYMIZED_TELEMETRY='False',
        TOKENIZERS_PARALLELISM='false',LENS_CASE_RETRIEVAL_PROFILE=str(profile),
        LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
    try:
        import torch,psutil
        from src.retrieval.service import RetrievalService, _to_evidence
        from src.generation.chain import get_default_service, DEFAULT_K_CASE
        torch.set_num_threads(4)
        start=time.perf_counter();service=get_default_service()
        pipe.send({'event':'ready','entrypoint':'src.generation.chain.get_default_service -> _build_service -> RetrievalService.from_index -> load_case_profile -> load_internal_case_profile -> AppliedCaseInternalService.search',
            'application_default_k_case':DEFAULT_K_CASE,'factory_reuses_same_instance':get_default_service() is service,
            'initialization_seconds':time.perf_counter()-start,'rss_bytes':psutil.Process().memory_info().rss,
            'torch_threads':torch.get_num_threads(),'device':'cpu',
            'case_profile':service.case_profile,'generation_enabled':False,'other_channel_resources_required':True})
        while True:
            item=pipe.recv()
            if item is None:break
            start=time.perf_counter();query=item.get('query',item.get('question',''));k=item.get('k',2)
            row={'qid':item['qid'],'query':query,'k':k,'error':None}
            try:
                result,trace=service.search_with_trace(query,k_law=0,k_case=k,k_guide=0,k_civil=0)
                payload=service.evidence_payload(result)
                row.update(cases=payload['channels']['cases'],trace=trace,evidence_payload=payload)
                checks=[]
                for e in result.cases:
                    c=service.case_backend.chunks[e.chunk_id]
                    original_format=_to_evidence(e.rank,c,e.score)
                    rendered,body_source=service.case_backend.render(e.chunk_id,e.rank,e.score)
                    body_exact=e.text==rendered.text
                    if body_source['policy']=='continuous_official_case':
                        data=Path(service.case_profile['data_root'])
                        source_file=data/body_source['source_relative_path']
                        official=source_file.read_text('utf-8')
                        body_exact=body_exact and sha(source_file)==body_source['source_sha256'] and e.text.split('\n',1)[1]==official[body_source['source_start']:body_source['source_end']]
                    checks.append({'chunk_id':e.chunk_id,'body_exact':body_exact,
                        'source_exact':e.source_url==c['metadata']['source_url'],
                        'citation_exact':e.citation==original_format.citation,
                        'existing_evidence_format_exact':type(e)==type(original_format) and set(e.__dataclass_fields__)==set(original_format.__dataclass_fields__),
                        'has_body':bool(e.text.strip()),'has_source':bool(e.source_url),'has_citation':bool(e.citation)})
                keys=[e['canonical_case_key'] for e in row['cases']]
                row['connection_checks']={'case_count':len(keys),'within_requested_k':len(keys)<=k,
                    'distinct_cases':len(keys)==len(set(keys)), 'ordered_ranks':[e.rank for e in result.cases]==list(range(1,len(keys)+1)),
                    'evidence':checks, 'noncase_search_disabled_for_this_check':not any(payload['channels'][n] for n in ('laws','civil_laws','guides'))}
            except Exception as error:row.update(cases=[],error={'type':type(error).__name__,'message':str(error)})
            row.update(latency_seconds=time.perf_counter()-start,rss_bytes=psutil.Process().memory_info().rss)
            pipe.send(row)
    except BaseException as error:
        pipe.send({'event':'initialization_error','type':type(error).__name__,'message':str(error)})
    finally:pipe.close()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--profile',type=Path,required=True)
    ap.add_argument('--jobs',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    jobs=json.loads(args.jobs.read_text('utf-8'));parent,child=mp.Pipe()
    source=Path(__file__).resolve().parents[1]
    write(args.output/'INPUT_SEAL.json',{'profile_sha256':sha(args.profile),'jobs_sha256':sha(args.jobs),
        'questions':{j['name']:{'path':j['questions'],'sha256':sha(j['questions']),'k':j['k']} for j in jobs},
        'code':{str(p.relative_to(source)):sha(p) for p in (source/'src/retrieval').glob('*.py')},
        'application_factory_sha256':sha(source/'src/generation/chain.py'),
        'runner_sha256':sha(__file__),'question_timeout_seconds':120,'initialization_timeout_seconds':240,
        'timeout_basis':'Existing preserved case evaluation runner uses 120s per question and 240s initialization.',
        'gold_loaded':False,'retries':0,'generation_enabled':False})
    process=mp.Process(target=worker,args=(child,args.profile));process.start()
    def receive(seconds):
        if not parent.poll(seconds):raise TimeoutError('case application worker timeout')
        return parent.recv()
    try:
        runtime=receive(240);write(args.output/'runtime.json',runtime)
        if runtime.get('event')!='ready':raise RuntimeError(str(runtime))
        for job in jobs:
            out=args.output/job['name'];out.mkdir()
            questions=json.loads(Path(job['questions']).read_text('utf-8-sig'))
            with (out/'results.jsonl').open('x',encoding='utf-8') as stream:
                for i,q in enumerate(questions,1):
                    parent.send(dict(q,k=job['k']))
                    try:row=receive(120)
                    except TimeoutError as error:
                        row={'qid':q['qid'],'query':q.get('query',q.get('question','')),'cases':[],
                             'k':job['k'],'error':{'type':'TimeoutError','message':str(error)}}
                        stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
                        raise
                    stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
                    if i%10==0:print(json.dumps({'phase':job['name'],'processed':i,'error':row['error']}),flush=True)
            write(out/'RESULT_SEAL.json',{p.name:sha(p) for p in out.iterdir() if p.is_file()})
        parent.send(None);process.join(10)
    except BaseException as error:
        write(args.output/'EXECUTION_FAILURE.json',{'type':type(error).__name__,'message':str(error)})
        raise
    finally:
        if process.is_alive():process.terminate();process.join(5)
        write(args.output/'RESULT_SEAL.json',{str(p.relative_to(args.output)):sha(p) for p in args.output.rglob('*') if p.is_file() and p!=args.output/'RESULT_SEAL.json'})


if __name__=='__main__':mp.freeze_support();main()
