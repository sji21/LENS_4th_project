"""Audit case DB/chunks/index and official source spans without loading a model."""
import argparse,json,struct,sqlite3,re
from pathlib import Path
from case_internal_run import sha,write


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--baseline',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    import chromadb
    chunks=[json.loads(l) for l in (args.data/'chunks/cases.jsonl').read_text('utf-8').splitlines() if l]
    originals=[json.loads(l) for l in (args.baseline/'chunks/cases.jsonl').read_text('utf-8').splitlines() if l]
    target=chromadb.PersistentClient(path=str(args.data/'index/chroma_kurev1_1024')).get_collection('knowledge_chunks')
    baseline=chromadb.PersistentClient(path=str(args.baseline/'index/chroma_kurev1_1024')).get_collection('knowledge_chunks')
    vector_mismatches=[]
    for offset in range(0,len(originals),128):
        ids=[c['chunk_id'] for c in originals[offset:offset+128]]
        old=baseline.get(ids=ids,include=['embeddings']);new=target.get(ids=ids,include=['embeddings'])
        ov={cid:struct.pack('<1024f',*vector) for cid,vector in zip(old['ids'],old['embeddings'])}
        nv={cid:struct.pack('<1024f',*vector) for cid,vector in zip(new['ids'],new['embeddings'])}
        vector_mismatches.extend(cid for cid in ids if ov.get(cid)!=nv.get(cid))
    got=target.get(include=['documents','metadatas']);docs=dict(zip(got['ids'],got['documents']));metas=dict(zip(got['ids'],got['metadatas']))
    assert docs=={c['chunk_id']:c['text'] for c in chunks}
    from src.retrieval.service import CASE
    from src.retrieval.retriever import matches
    metadata_mismatches=[];span_mismatches=[];gold_markers=[];filter_excluded=[]
    for c in chunks:
        cid=c['chunk_id'];m=c['metadata']
        if not matches(m,CASE.where()):filter_excluded.append(cid)
        for name in ('case_id','canonical_case_key','source_url','status','source_name'):
            if metas[cid].get(name)!=m.get(name):metadata_mismatches.append([cid,name])
        if 'source_relative_path' in m:
            path=args.data/m['source_relative_path'];source=path.read_text('utf-8')
            if sha(path)!=m['source_sha256'] or c['text'].split('\n',1)[1]!=source[m['source_start']:m['source_end']]:
                span_mismatches.append(cid)
        if re.search(r'\b(?:CDEV|CIHO|HO-RENT|CLD)-\d{3}\b',c['text']):gold_markers.append(cid)
    db=sqlite3.connect(args.data/'database/cases.sqlite3')
    stored={r[0]:(r[1],r[2],r[3]) for r in db.execute('SELECT chunk_id,case_id,canonical_case_key,text FROM case_chunks')}
    db_metadata={r[0]:json.loads(r[1]) for r in db.execute('SELECT chunk_id,metadata_json FROM case_chunks')}
    exported={c['chunk_id']:(str(c['metadata']['case_id']),c['metadata']['canonical_case_key'],c['text']) for c in chunks}
    source_count=db.execute('SELECT count(*) FROM case_sources').fetchone()[0];db.close()
    db_metadata_exact=db_metadata=={c['chunk_id']:c['metadata'] for c in chunks}
    result={'pass':not (vector_mismatches or metadata_mismatches or span_mismatches or gold_markers or filter_excluded) and stored==exported and db_metadata_exact,
        'db_chunk_metadata_exact':db_metadata_exact,
        'existing_case_filter_excluded_ids':filter_excluded,
        'case_count':source_count,'chunk_index_count':len(chunks),'original_case_vectors_compared':len(originals),
        'original_vector_mismatches':vector_mismatches,'metadata_mismatches':metadata_mismatches,
        'source_span_mismatches':span_mismatches,'gold_question_id_markers':gold_markers,
        'db_chunk_exact':stored==exported,'index_chunk_exact':True,
        'new_official_source_spans_checked':sum('source_relative_path' in c['metadata'] for c in chunks),
        'original_chunk_prefix_preserved':(args.data/'chunks/cases.jsonl').read_bytes().startswith((args.baseline/'chunks/cases.jsonl').read_bytes()),
        'contamination_audit_basis':'Original sealed corpus preserved; each new text is exactly an approved official-source slice plus its source header. Gold questions/answers are not builder text inputs. Marker scan is supplementary, not sole evidence.',
        'input_hashes':{'db':sha(args.data/'database/cases.sqlite3'),'chunks':sha(args.data/'chunks/cases.jsonl'),
            'corpus_manifest':sha(args.data/'CORPUS_MANIFEST.json'),'index_manifest':sha(args.data/'INDEX_MANIFEST.json')}}
    write(args.output,result);assert result['pass'];print(json.dumps({k:v for k,v in result.items() if not isinstance(v,(dict,list))},ensure_ascii=False))


if __name__=='__main__':main()
