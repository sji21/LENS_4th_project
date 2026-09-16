"""Direct baseline batches and new case index embedding, one KURE process."""
import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

from case_internal_run import sha, write, baseline


def capture(service, questions, out, k):
    out.mkdir(parents=True, exist_ok=False)
    write(out/'INPUT_SEAL.json', {'questions':str(questions), 'questions_sha256':sha(questions),
        'k':k, 'gold_loaded':False, 'entrypoint':'CaseCorpusRetrievalService._search_one'})
    with (out/'results.jsonl').open('x',encoding='utf-8') as stream:
        for i,item in enumerate(json.loads(questions.read_text('utf-8-sig')),1):
            query=item.get('query',item.get('question',''))
            start=time.perf_counter(); service._request_trace.case={}
            row={'qid':item['qid'],'query':query,'k':k,'error':None}
            try:
                evidence=service._search_one(service.corpora[1],query,k) if query.strip() else []
                row['cases']=[dict(asdict(e),canonical_case_key=service._chunks[e.chunk_id]['metadata'].get('canonical_case_key')) for e in evidence]
                row['trace']=service._request_trace.case
            except Exception as error:
                row.update(cases=[],error={'type':type(error).__name__,'message':str(error)})
            row['latency_seconds']=time.perf_counter()-start
            stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
            if i%10==0:print(json.dumps({'phase':out.name,'processed':i}),flush=True)
    write(out/'RESULT_SEAL.json',{p.name:sha(p) for p in out.iterdir() if p.is_file()})


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--jobs',type=Path,required=True);ap.add_argument('--data',type=Path)
    args=ap.parse_args();root=args.root
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',ANONYMIZED_TELEMETRY='False',TOKENIZERS_PARALLELISM='false')
    import torch
    from src.retrieval.dense import SentenceTransformerEmbedding,ChromaRetriever
    from src.retrieval.retriever import load_chunks
    from src.retrieval.case_profile import index_content_hash
    torch.set_num_threads(8)
    profile=json.loads((root/'00_inputs/baseline_profile.json').read_text('utf-8-sig'))
    model=SentenceTransformerEmbedding(profile['model_id'],revision=profile['model_revision'])
    dense=ChromaRetriever(model,root/'data_baseline/index/chroma_kurev1_1024')
    service=baseline(load_chunks(root/'data_baseline/chunks/cases.jsonl'),dense,profile)
    for job in json.loads(args.jobs.read_text('utf-8')):
        capture(service,Path(job['questions']),root/'00_baseline'/job['name'],job['k'])
    if args.data:
        target=args.data/'index/chroma_kurev1_1024'
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(root/'data_baseline/index/chroma_kurev1_1024',target)
        updated=ChromaRetriever(model,target)
        collection=updated.collection
        before_hash=index_content_hash(collection)
        if before_hash!=profile['index_content_sha256']:
            raise ValueError('baseline logical index hash mismatch')
        # Only this new index is changed; existing source collection is untouched.
        noncases=collection.get(where={'doc_type':{'$ne':'case'}},include=[])['ids']
        if noncases:collection.delete(ids=noncases)
        additions=load_chunks(args.data/'chunks/additions.jsonl')
        lengths=[];start=time.perf_counter()
        for offset in range(0,len(additions),8):
            batch=additions[offset:offset+8]
            sizes=[len(model._model.tokenizer.encode(c['text'])) for c in batch]
            lengths.extend(sizes)
            if max(sizes)>model._model.max_seq_length:raise ValueError('embedding would truncate official text')
            vectors=model.embed([c['text'] for c in batch])
            collection.add(ids=[c['chunk_id'] for c in batch],documents=[c['text'] for c in batch],
                metadatas=[dict(c['metadata'],embedding_input_hash=__import__('hashlib').sha256(c['text'].encode()).hexdigest(),
                    embedding_fingerprint=profile['model_id']+'@'+profile['model_revision']) for c in batch],embeddings=vectors)
            print(json.dumps({'phase':'embedding','processed':min(offset+8,len(additions)),'total':len(additions)}),flush=True)
        expected=load_chunks(args.data/'chunks/cases.jsonl')
        actual=collection.get(include=['documents','metadatas'])
        mapping=dict(zip(actual['ids'],actual['documents']))
        assert mapping=={c['chunk_id']:c['text'] for c in expected}
        write(args.data/'INDEX_MANIFEST.json',{'pass':True,'model_id':profile['model_id'],
            'model_revision':profile['model_revision'],'dimension':1024,'count':collection.count(),
            'logical_sha256':index_content_hash(collection),'added_chunks':len(additions),
            'gold_indexed':False,'text_exact_match_all_chunks':True,'max_added_token_length':max(lengths),
            'model_max_seq_length':model._model.max_seq_length,'embedding_seconds':time.perf_counter()-start,
            'baseline_logical_sha256':before_hash,'noncase_records_excluded_from_new_case_index':len(noncases)})


if __name__=='__main__':main()
