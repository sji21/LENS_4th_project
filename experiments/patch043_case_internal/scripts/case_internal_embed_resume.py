"""Resume only missing reviewed case vectors, without retaining baseline BM25."""
import argparse,json,os,time
from pathlib import Path
from experiments.patch043_case_internal.scripts.case_internal_run import sha,write


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--profile',type=Path,required=True);args=ap.parse_args()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',ANONYMIZED_TELEMETRY='False',TOKENIZERS_PARALLELISM='false')
    import torch,psutil
    from src.retrieval.dense import SentenceTransformerEmbedding,ChromaRetriever
    from src.retrieval.retriever import load_chunks
    from src.retrieval.case_profile import index_content_hash
    torch.set_num_threads(4)
    profile=json.loads(args.profile.read_text('utf-8-sig'))
    start=time.perf_counter();model=SentenceTransformerEmbedding(profile['model_id'],revision=profile['model_revision'],batch=1)
    index=ChromaRetriever(model,args.data/'index/chroma_kurev1_1024').collection
    known=set(index.get(include=[])['ids']);additions=load_chunks(args.data/'chunks/additions.jsonl')
    lengths=[];latencies=[]
    with (args.data/'embedding_resume.jsonl').open('a',encoding='utf-8') as stream:
        for n,c in enumerate(additions,1):
            tokens=len(model._model.tokenizer.encode(c['text']));lengths.append(tokens)
            if tokens>model._model.max_seq_length:raise ValueError('official text would be truncated')
            if c['chunk_id'] in known:continue
            begin=time.perf_counter();vector=model.embed([c['text']])[0]
            index.add(ids=[c['chunk_id']],documents=[c['text']],embeddings=[vector],
                metadatas=[dict(c['metadata'],embedding_input_hash=__import__('hashlib').sha256(c['text'].encode()).hexdigest(),
                    embedding_fingerprint=profile['model_id']+'@'+profile['model_revision'])])
            elapsed=time.perf_counter()-begin;latencies.append(elapsed)
            row={'chunk_id':c['chunk_id'],'tokens':tokens,'seconds':elapsed,'rss_bytes':psutil.Process().memory_info().rss}
            stream.write(json.dumps(row)+'\n');stream.flush()
            if n%4==0:print(json.dumps({'phase':'embedding_resume','processed':n,'total':len(additions),'last_seconds':elapsed}),flush=True)
    expected=load_chunks(args.data/'chunks/cases.jsonl');got=index.get(include=['documents'])
    assert dict(zip(got['ids'],got['documents']))=={c['chunk_id']:c['text'] for c in expected}
    write(args.data/'INDEX_MANIFEST.json',{'pass':True,'model_id':profile['model_id'],'model_revision':profile['model_revision'],
        'dimension':1024,'count':index.count(),'logical_sha256':index_content_hash(index),'added_chunks':len(additions),
        'gold_indexed':False,'text_exact_match_all_chunks':True,'max_added_token_length':max(lengths),
        'model_max_seq_length':model._model.max_seq_length,'resume_seconds':time.perf_counter()-start,
        'already_embedded_supplement_chunks':sum(c['chunk_id'] in known for c in additions),
        'resume_batch_size':1,'resume_torch_threads':4,'embedding_model_unchanged':True,
        'preparation_note':'Stopped the task-owned embedding process to release baseline BM25 memory; resumed only missing IDs. This is corpus preparation, not a final HO rerun.'})


if __name__=='__main__':main()
