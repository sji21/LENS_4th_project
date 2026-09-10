"""Observe civil BM25/dense/RRF candidate recall; do not change final retrieval."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

from scripts.patch015_baseline import ROOT, norm, read, sha, write

KS = (1, 2, 3, 5, 7)
REQUIRED_BUNDLE_FILES = {
    'capture/manifest.json', 'capture/results.json', 'capture/audit.json',
    'report/summary.json', 'report/details.json',
}


def validate_capture(run, *, local_capture=False):
    run = Path(run).resolve()
    bundle_path = run.parent/'bundle-manifest.json'
    if bundle_path.is_file():
        bundle = read(bundle_path)
        if bundle.get('schema') != 'patch016-bundle-v1':
            raise ValueError('Unsupported candidate bundle schema')
        files = bundle.get('files', {})
        if not REQUIRED_BUNDLE_FILES <= set(files):
            raise ValueError('Candidate bundle omits required files')
        if run != run.parent/'capture':
            raise ValueError('Bundle capture directory mismatch')
        for name, digest in files.items():
            path = (run.parent/name).resolve()
            if not path.is_relative_to(run.parent) or not path.is_file() or sha(path) != digest:
                raise ValueError('Candidate bundle hash/path mismatch: '+name)
    elif not local_capture:
        raise ValueError('Missing bundle manifest; use --local-capture only for unpublished captures')
    for name in ('manifest.json', 'results.json', 'audit.json'):
        if not (run/name).is_file():
            raise ValueError('Missing capture file: '+name)
    audit = read(run/'audit.json')
    if (type(audit.get('queries')) is not int or audit['queries'] != 235
            or audit.get('operating_hashes_unchanged') is not True
            or audit.get('policies_unchanged') is not True):
        raise ValueError('Incomplete or unsuccessful candidate capture audit')


def coverage(targets, ranked, k):
    targets = {norm(t) for t in targets}
    if not targets:
        return None
    return int(targets <= {norm(t) for t in ranked[:k]})


def collect(out):
    from scripts.patch015_report import check_shared_bundle
    baseline = ROOT/'data/eval/patch015-baseline/capture'
    check_shared_bundle(baseline)
    old = read(baseline/'manifest.json')
    for rel,digest in old['operating_hashes_before'].items():
        if sha(ROOT/rel) != digest:
            raise ValueError('Baseline operating data mismatch: '+rel)
    for rel,digest in old['code_hashes'].items():
        if sha(ROOT/rel) != digest:
            raise ValueError('Baseline product code mismatch: '+rel)
    out.mkdir(parents=True,exist_ok=False)
    snapshot=out/'snapshot'
    for rel in old['operating_hashes_before']:
        if rel.startswith(('data/chunks/','data/index/')):
            dest=snapshot/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/rel,dest)
    from src.retrieval.service import RetrievalService
    from src.evaluation.baseline import settings
    paths=tuple(snapshot/'data/chunks'/f'{n}.jsonl' for n in ('chunks','cases','guides'))
    svc=RetrievalService.from_index(chunk_paths=paths,index_path=snapshot/'data/index/chroma_kurev1_1024',civil_index_path=snapshot/'data/index/chroma_civil_kurev1_1024')
    # JSON stores tuple settings as lists; compare the same serialized form.
    if json.loads(json.dumps(settings(svc)))!=old['settings']:
        raise ValueError('Baseline retrieval settings changed')
    cache=Path.home()/'.cache/huggingface/hub/models--nlpai-lab--KURE-v1'
    if any(sha(cache/p)!=h for p,h in old['model_files'].items()):
        raise ValueError('Baseline model cache changed')
    queries=read(baseline/'results.json')
    if len(queries)!=235: raise ValueError('Expected full baseline inputs')
    civil_ids=set(svc.civil.include_ids)
    chunks={cid:c for cid,c in svc._chunks.items() if c['metadata'].get('article_id') in civil_ids}
    indexed=svc.civil_dense.collection.get(include=['documents','metadatas'])
    from src.retrieval.index import clean_metadata
    for cid,body,meta in zip(indexed['ids'],indexed['documents'],indexed['metadatas']):
        if body!=chunks[cid]['text'] or meta!=clean_metadata(chunks[cid]['metadata']):
            raise ValueError('Civil index mismatch')
    if set(indexed['ids'])!=set(chunks) or len(chunks)!=7:
        raise ValueError('Expected seven civil articles')
    policy_files=['data/eval/civil-review2/retrieval-plan.json','data/eval/dev100-v2/diagnostic-plan.json',
                  'data/eval/patch015-baseline/capture/results.json']
    manifest={'schema':'patch016-candidates-v1','commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'status':subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip(),
              'runner_sha256':sha(__file__), 'baseline_manifest_sha256':sha(baseline/'manifest.json'),
              'input_policy_hashes':{p:sha(ROOT/p) for p in policy_files},
              'settings':settings(svc),'candidate_ks':KS,'candidate_articles':sorted(civil_ids),
              'final_ranking_modified':False,'generation_performed':False,
              'experiment':'Bypass topic gating only for a separate seven-article candidate search; retain original expansion, weights, RRF and model.'}
    write(out/'manifest.json',manifest)
    results=[]
    for q in queries:
        start=time.perf_counter()
        ranked=svc._search_one(svc.civil,q['query'],len(chunks))
        members=svc._retrievers[svc.civil.name].last_member_hits()
        convert=lambda ids:[chunks[cid]['metadata']['article_id'] for cid in ids]
        methods={('bm25' if name.endswith('-bm25') else 'dense'):convert(ids) for name,ids in members.items()}
        if set(methods)!={'bm25','dense'}: raise ValueError('Unexpected member methods')
        methods['rrf']=convert([h.chunk_id for h in ranked])
        results.append({'qid':q['qid'],'mode':q['mode'],'group':q['group'],'query_sha256':q['query_sha256'],
                        'seconds':time.perf_counter()-start,'rankings':methods})
        if len(results)%25==0: print(f'{len(results)}/235 candidates',flush=True)
    for rel,digest in old['operating_hashes_before'].items():
        if sha(ROOT/rel)!=digest: raise ValueError('Operating files changed')
    for rel,digest in manifest['input_policy_hashes'].items():
        if sha(ROOT/rel)!=digest: raise ValueError('Input policy changed')
    write(out/'results.json',results)
    write(out/'audit.json',{'queries':len(results),'operating_hashes_unchanged':True,'policies_unchanged':True,
                           'latency_median_seconds':statistics.median(r['seconds'] for r in results),
                           'latency_p95_seconds':sorted(r['seconds'] for r in results)[int(.95*(len(results)-1))],
                           'latency_scope':'additional candidate search only; includes first query; not end-to-end latency'})
    print('CANDIDATE CAPTURE COMPLETE',flush=True)


def replay(run,out, *, local_capture=False):
    validate_capture(run, local_capture=local_capture)
    manifest=read(run/'manifest.json')
    for p,h in manifest['input_policy_hashes'].items():
        if sha(ROOT/p)!=h: raise ValueError('Changed evaluation inputs')
    old=read(ROOT/'data/eval/patch015-baseline/capture/results.json')
    new=read(run/'results.json')
    byid={(r['qid'],r['mode']):r for r in old}
    if len(new)!=len(byid) or {(r['qid'],r['mode']) for r in new}!=set(byid):
        raise ValueError('Incomplete results')
    civil=read(ROOT/'data/eval/civil-review2/retrieval-plan.json')
    cp={r['id']:r for r in civil['items']}
    dp={r['qid']:r for r in read(ROOT/'data/eval/dev100-v2/diagnostic-plan.json')}
    allowed={norm(t) for t in manifest['candidate_articles']}
    rows=[]
    for r in new:
        prior=byid[r['qid'],r['mode']]
        if r['query_sha256']!=prior['query_sha256']: raise ValueError('Query mismatch')
        if r['qid'] in cp:
            p=cp[r['qid']]
            targets={norm(t) for g in p['required_groups_all_of'] for t in g['article_targets_all_of']} & allowed
            cohort='civil_'+p['track']
        else:
            p=dp[r['qid']]
            targets={norm(t['article_anchor']) for t in p['law_targets']} & allowed
            cohort='dev_'+r['mode'] if not p['historical_review_required'] else 'historical_observation'
        for ranked in r['rankings'].values():
            if len(set(ranked))!=len(ranked) or not {norm(t) for t in ranked}<=allowed:
                raise ValueError('Invalid civil candidate identifiers')
        baseline={h['article_anchor'] for h in prior['laws']} & allowed
        rows.append({'qid':r['qid'],'mode':r['mode'],'cohort':cohort,'civil_targets':sorted(targets),
                     'baseline_final_civil_coverage':coverage(targets,list(baseline),7),
                     'all_civil_targets_covered':{method:{str(k):coverage(targets,ranked,k) for k in KS} for method,ranked in r['rankings'].items()}})
    summary={}
    for cohort in sorted({r['cohort'] for r in rows}):
        selected=[r for r in rows if r['cohort']==cohort and r['civil_targets']]
        summary[cohort]={'scorable_n':len(selected),'targetless_observation_n':sum(r['cohort']==cohort and not r['civil_targets'] for r in rows),
                         'baseline_final_civil_coverage':sum(r['baseline_final_civil_coverage'] for r in selected) if selected else None,
                         'candidate_coverage':{m:{str(k):sum(r['all_civil_targets_covered'][m][str(k)] for r in selected) if selected else None for k in KS} for m in ('bm25','dense','rrf')}}
    out.mkdir(parents=True,exist_ok=False)
    write(out/'summary.json',summary)
    write(out/'details.json',rows)
    print(summary)


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--replay',type=Path)
    parser.add_argument('--local-capture',action='store_true',help='Allow an unpublished capture without a bundle manifest; audit remains mandatory')
    args=parser.parse_args()
    if not args.out.resolve().is_relative_to(ROOT/'tmp') or args.out.exists(): parser.error('Use a new directory under tmp/')
    if args.local_capture and not args.replay: parser.error('--local-capture requires --replay')
    if args.replay: replay(args.replay,args.out,local_capture=args.local_capture)
    else:
        os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',ANONYMIZED_TELEMETRY='False',LANGSMITH_TRACING='false')
        collect(args.out)
