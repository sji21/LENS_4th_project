"""Verify live separated retrieval against the frozen PATCH-020 TOP3 experiment."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from scripts.patch015_baseline import ROOT, read, sha, write, norm
from scripts.patch015_report import check_shared_bundle
from scripts.patch018_separate import validate_capture
from scripts.patch020_budget import check_bundle


def check_settings(current, original):
    legacy = json.loads(json.dumps(current))
    civil_budget = legacy['search_k'].pop('k_civil', None)
    if civil_budget != 3 or legacy != original:
        raise ValueError('Unexpected search settings change')
    return {'legacy_settings_unchanged': True, 'added_search_k': {'k_civil': civil_budget}}


def committed_source(root=ROOT):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args])
    status = git('status', '--porcelain').decode().strip()
    if status:
        raise ValueError('Verification requires a clean committed tree')
    revision = git('rev-parse', 'HEAD').decode().strip()
    sources = {}
    for rel in ('scripts/patch021_verify.py', 'src/retrieval/service.py'):
        working = (root / rel).read_bytes()
        committed = git('show', f'{revision}:{rel}')
        normalized = working.replace(b'\r\n', b'\n')
        if normalized != committed.replace(b'\r\n', b'\n'):
            raise ValueError('Source differs from commit: ' + rel)
        sources[rel] = {
            'working_sha256': hashlib.sha256(working).hexdigest(),
            'git_sha256': hashlib.sha256(committed).hexdigest(),
            'lf_sha256': hashlib.sha256(normalized).hexdigest(),
        }
    return {'commit': revision, 'tree': git('rev-parse', 'HEAD^{tree}').decode().strip(),
            'status': status, 'normalization': 'CRLF to LF only', 'sources': sources}


def run(out):
    source = committed_source()
    check_bundle()
    check_shared_bundle(ROOT/'data/eval/patch015-baseline/capture')
    validate_capture(ROOT/'data/eval/patch018-separate/capture')
    original = read(ROOT/'data/eval/patch015-baseline/capture/manifest.json')
    before = {p:sha(ROOT/p) for p in original['operating_hashes_before']}
    if before != original['operating_hashes_before']:
        raise ValueError('Operating data changed since baseline')
    out.mkdir(parents=True,exist_ok=False)
    snapshot=out/'snapshot'
    for rel in before:
        if rel.startswith(('data/chunks/','data/index/')):
            target=snapshot/rel;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/rel,target)
    from src.retrieval.service import RetrievalService
    from src.evaluation.baseline import SEARCH_K, settings
    svc=RetrievalService.from_index(
        chunk_paths=tuple(snapshot/'data/chunks'/f'{n}.jsonl' for n in ('chunks','cases','guides')),
        index_path=snapshot/'data/index/chroma_kurev1_1024',
        civil_index_path=snapshot/'data/index/chroma_civil_kurev1_1024')
    settings_comparison = check_settings(settings(svc), original['settings'])
    cache=Path.home()/'.cache/huggingface/hub/models--nlpai-lab--KURE-v1'
    if any(sha(cache/p)!=digest for p,digest in original['model_files'].items()):
        raise ValueError('Model changed')
    expected_rows=read(ROOT/'data/eval/patch020-budget/results/details.json')
    expected={(r['qid'],r['mode']):r for r in expected_rows}
    queries=read(ROOT/'data/eval/patch015-baseline/capture/results.json')
    if len(queries)!=235 or len(expected)!=235:
        raise ValueError('Expected full input set')
    results=[]
    anchors=lambda hits:[norm(svc._chunks[e.chunk_id]['metadata']['article_id']) for e in hits]
    for q in queries:
        result=svc.search(q['query'], **SEARCH_K)
        target=expected[q['qid'],q['mode']]
        laws,civil=anchors(result.laws),anchors(result.civil_laws)
        if laws!=target['general'] or civil!=target['policies']['retain_3']['civil']:
            raise ValueError('Channel ranking differs: '+q['qid']+' '+q['mode'])
        for key in ('cases','guides'):
            if [e.chunk_id for e in getattr(result,key)] != [e['chunk_id'] for e in q[key]]:
                raise ValueError('Other channel changed: '+q['qid']+' '+key)
        results.append({'qid':q['qid'],'mode':q['mode'],'laws':laws,'civil_laws':civil,
            'cases':[e.chunk_id for e in result.cases],'guides':[e.chunk_id for e in result.guides]})
        if len(results)%25==0:print(f'{len(results)}/235 verified',flush=True)
    after={p:sha(ROOT/p) for p in before}
    if before!=after:raise ValueError('Operating files changed')
    if committed_source() != source:
        raise ValueError('Source changed during verification')
    write(out/'results.json',results)
    write(out/'audit.json',{'queries':len(results),'general_and_civil_match_patch020':True,
        'case_and_guide_match_patch015':True,'operating_before':before,'operating_after':after,
        'commit':source['commit'], 'status':source['status'], 'source_provenance':source,
        'runner_sha256':sha(__file__),'service_sha256':sha(ROOT/'src/retrieval/service.py'),
        'expected_bundle_sha256':sha(ROOT/'data/eval/patch020-budget/bundle-manifest.json'),
        'model_files':original['model_files'],'settings':settings(svc),
        'settings_comparison':settings_comparison,'generation':False})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if not args.out.resolve().is_relative_to(ROOT/'tmp') or args.out.exists():p.error('Use new tmp path')
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',LANGSMITH_TRACING='false',ANONYMIZED_TELEMETRY='False')
    run(args.out)
