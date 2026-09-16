"""Version a metadata-only repair; source text, identities and vectors are fixed."""
import argparse,json,shutil,sqlite3,struct
from pathlib import Path
from case_internal_run import sha,write
from src.retrieval.case_profile import index_content_hash
from src.retrieval.service import CASE
from src.retrieval.retriever import matches


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    shutil.copytree(args.source,args.output)
    old=[json.loads(l) for l in (args.source/'chunks/cases.jsonl').read_text('utf-8').splitlines()]
    chunks=json.loads(json.dumps(old));changed=[]
    for c in chunks:
        m=c['metadata'];before=dict(m)
        if 'status' not in m:
            assert 'source_relative_path' in m
            m['status']='current'
        if 'source_relative_path' in m and 'scourt.go.kr' in m['source_url']:
            m['source_name']='대한민국 법원'
        if before!=m:changed.append({'chunk_id':c['chunk_id'],'before':before,'after':m})
    prefix=(args.source/'chunks/cases.jsonl').read_bytes().splitlines(keepends=True)[:8377]
    assert all(a==b for a,b in zip(old[:8377],chunks[:8377]))
    with (args.output/'chunks/cases.jsonl').open('wb') as f:
        f.write(b''.join(prefix));f.write(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in chunks[8377:]).encode())
    (args.output/'chunks/additions.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in chunks[8377:]),'utf-8')
    db=sqlite3.connect(args.output/'database/cases.sqlite3')
    for c in chunks:
        db.execute('UPDATE case_chunks SET metadata_json=? WHERE chunk_id=?',(json.dumps(c['metadata'],ensure_ascii=False),c['chunk_id']))
    db.commit();db.close()
    import chromadb
    col=chromadb.PersistentClient(path=str(args.output/'index/chroma_kurev1_1024')).get_collection('knowledge_chunks')
    ids=[r['chunk_id'] for r in changed]
    before=col.get(ids=ids,include=['embeddings','documents'])
    for start in range(0,len(changed),64):
        part=changed[start:start+64];col.update(ids=[r['chunk_id'] for r in part],metadatas=[r['after'] for r in part])
    after=col.get(ids=ids,include=['embeddings','documents'])
    pack=lambda r:{cid:(doc,struct.pack('<1024f',*v)) for cid,doc,v in zip(r['ids'],r['documents'],r['embeddings'])}
    assert pack(before)==pack(after)
    assert all(matches(c['metadata'],CASE.where()) for c in chunks)
    report={'previous_version':str(args.source),'changed_chunks':len(changed),'changes':changed,
        'reason':'New official case chunks lacked status required by existing CASE.where; correct provider names for scourt URLs.',
        'status_semantics':'Existing retrieval metadata active-record marker, not a claim that historical legal provisions remain in force.',
        'text_identity_and_vectors_unchanged':True,'original_8377_prefix_unchanged':True,
        'all_8525_case_chunks_pass_existing_case_filter':True}
    write(args.output/'METADATA_CONNECTION_REPAIR.json',report)
    manifest=json.loads((args.output/'CORPUS_MANIFEST.json').read_text('utf-8'))
    manifest['version']='case-internal-v4';manifest['previous_version']=str(args.source)
    for n in ('database/cases.sqlite3','chunks/cases.jsonl','chunks/additions.jsonl'):
        manifest['files'][n]=sha(args.output/n)
    manifest['metadata_repair_sha256']=sha(args.output/'METADATA_CONNECTION_REPAIR.json')
    write(args.output/'CORPUS_MANIFEST.json',manifest)
    index=json.loads((args.output/'INDEX_MANIFEST.json').read_text('utf-8'))
    index.update(logical_sha256=index_content_hash(col),chunks_sha256=sha(args.output/'chunks/cases.jsonl'),
        metadata_repair_sha256=sha(args.output/'METADATA_CONNECTION_REPAIR.json'),previous_index_manifest_sha256=sha(args.source/'INDEX_MANIFEST.json'))
    write(args.output/'INDEX_MANIFEST.json',index)
    print(json.dumps({k:v for k,v in report.items() if k!='changes'}))


if __name__=='__main__':main()
