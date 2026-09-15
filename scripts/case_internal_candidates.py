"""Question-only candidate ablations; no Gold input accepted."""
import argparse,json,os,time
from pathlib import Path
from dataclasses import asdict,replace
from case_internal_run import sha,write


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--questions',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--baseline-data',type=Path);ap.add_argument('--baseline-profile',type=Path)
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',ANONYMIZED_TELEMETRY='False',TOKENIZERS_PARALLELISM='false')
    write(args.output/'INPUT_SEAL.json',{'questions_sha256':sha(args.questions),'chunks_sha256':sha(args.data/'chunks/cases.jsonl'),
        'index_manifest_sha256':sha(args.data/'INDEX_MANIFEST.json'),'runner_sha256':sha(__file__),
        'gold_loaded':False,'actual_k':2,'depths':[80,160,240],
        'baseline_profile_sha256':sha(args.baseline_profile) if args.baseline_profile else None,
        'baseline_chunks_sha256':sha(args.baseline_data/'chunks/cases.jsonl') if args.baseline_data else None,
        'retrieval_code':{p.name:sha(p) for p in (Path(__file__).resolve().parents[1]/'src/retrieval').glob('*.py')},
        'single_factor_sequence':['depth','query_expansion','field_filter','RRF weighting and fusion offline','reranking offline']})
    import torch,psutil
    from src.retrieval.dense import SentenceTransformerEmbedding,ChromaRetriever
    from src.retrieval.retriever import load_chunks
    from src.retrieval.case_internal import CaseInternalRetriever,CaseInternalPolicy
    from src.retrieval.terms import expand,expand_civil
    torch.set_num_threads(8)
    index=json.loads((args.data/'INDEX_MANIFEST.json').read_text('utf-8'))
    start=time.perf_counter();model=SentenceTransformerEmbedding(index['model_id'],revision=index['model_revision'])
    if args.baseline_data:
        from case_internal_run import baseline
        from case_internal_prepare_run import capture
        original_profile=json.loads(args.baseline_profile.read_text('utf-8-sig'))
        original_dense=ChromaRetriever(model,args.baseline_data/'index/chroma_kurev1_1024')
        original_service=baseline(load_chunks(args.baseline_data/'chunks/cases.jsonl'),original_dense,original_profile)
        capture(original_service,args.questions,args.output/'baseline_dev32_k2',2)
        del original_service,original_dense
        __import__('gc').collect()
    dense=ChromaRetriever(model,args.data/'index/chroma_kurev1_1024')
    retriever=CaseInternalRetriever(load_chunks(args.data/'chunks/cases.jsonl'),dense)
    init=time.perf_counter()-start
    # Only single-factor candidate changes; subsequent combinations use these
    # recorded member ranks and are labelled cached development comparisons.
    policies=[]
    for depth in (80,160,240):
        policies.append(CaseInternalPolicy(candidate_depth=depth,fusion_depth=depth))
    policies += [replace(p,query_expansion='standard') for p in list(policies)]
    policies += [replace(p,field_policy='all') for p in list(policies)]
    write(args.output/'runtime.json',{'device':str(model._model.device),'torch':torch.__version__,
        'init_seconds':init,'threads':torch.get_num_threads(),'policies':[asdict(p) for p in policies]})
    try:
        with (args.output/'results.jsonl').open('x',encoding='utf-8') as stream:
            for i,q in enumerate(json.loads(args.questions.read_text('utf-8')),1):
                query=q.get('query',q['question'])
                for n,p in enumerate(policies):
                    retriever.policy=p
                    retriever.hybrid.members[0].retriever.query_expander=expand_civil if p.query_expansion=='civil_terms' else expand
                    start=time.perf_counter();row={'qid':q['qid'],'query':query,'policy':asdict(p),'error':None}
                    try:
                        result,trace=retriever.search_with_trace(query,2)
                        row.update(cases=[dict(asdict(e),canonical_case_key=retriever.chunks[e.chunk_id]['metadata']['canonical_case_key']) for e in result],trace=trace)
                    except Exception as error:row.update(cases=[],error={'type':type(error).__name__,'message':str(error)})
                    row.update(latency_seconds=time.perf_counter()-start,rss_bytes=psutil.Process().memory_info().rss)
                    stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
                print(json.dumps({'phase':'candidate_ablations','processed_questions':i,'policies':len(policies)}),flush=True)
    finally:
        write(args.output/'RESULT_SEAL.json',{str(p.relative_to(args.output)):sha(p) for p in args.output.rglob('*') if p.is_file() and p!=args.output/'RESULT_SEAL.json'})


if __name__=='__main__':main()
