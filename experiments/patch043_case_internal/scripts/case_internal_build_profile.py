"""Pin the selected case data, model, and DEV policy for application checks."""
import argparse,json
from pathlib import Path
from experiments.patch043_case_internal.scripts.case_internal_run import sha,write


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--selection',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();data=args.data.resolve();root=args.root.resolve()
    selection=json.loads(args.selection.read_text('utf-8'))
    index=json.loads((data/'INDEX_MANIFEST.json').read_text('utf-8'))
    profile={'schema':'lens-case-internal-v1','version':'case-internal-8395-applied-v1',
        'data_root':str(data),'policy':selection['policy'],'fallback_allowed':False,
        'model_id':index['model_id'],'model_revision':index['model_revision'],'dimension':1024,
        'index_content_sha256':index['logical_sha256'], 'candidate_selection_sha256':sha(args.selection),
        'files':{name:sha(data/name) for name in ('database/cases.sqlite3','chunks/cases.jsonl',
            'CORPUS_MANIFEST.json','DB_CONNECTION_AUDIT.json','INDEX_MANIFEST.json')},
        'application_mode':'case-only profile load/check; base_service attachment preserves an existing service for other channels',
        'case_evaluation_k':2,'public_default_case_k':20,
        'default_k_basis':'Preserved baseline case profile return_k=20; evaluation/application top2 explicitly requests k_case=2.'}
    local_embedding=json.loads((root/'02_models/KURE_LOCAL_COPY.json').read_text('utf-8'))
    assert local_embedding['revision']==profile['model_revision']
    profile['embedding_local']={'path':str(root/'02_models/kure-v1'),'files':local_embedding['files']}
    if profile['policy']['rerank']=='cross_encoder':
        run=Path(selection['model_run']);seal=json.loads((run/'INPUT_SEAL.json').read_text('utf-8'))
        if 'minilm' in run.name:
            name='mmarco-minilm';meta='minilm_model_metadata.json'
        else:name='bge-reranker-v2-m3';meta='bge_model_metadata.json'
        metadata=json.loads((root/'02_models'/meta).read_text('utf-8'))
        model_path=root/'02_models'/name
        profile['cross_encoder']={'id':metadata['id'],'revision':metadata['sha'],'path':str(model_path),
            'license':metadata['cardData']['license'],'device':'cpu','max_length':seal['max_length'],
            'batch_size':seal['batch_size'],'files':seal['model_files']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.output.exists():raise FileExistsError(args.output)
    write(args.output,profile);print(json.dumps({'profile':str(args.output),'policy':profile['policy']}))


if __name__=='__main__':main()
