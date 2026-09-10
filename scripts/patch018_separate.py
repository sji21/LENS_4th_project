"""Separate general TOP5 and civil TOP2 without changing production retrieval."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from scripts.patch015_baseline import ROOT, norm, read, sha, write
from scripts.patch017_selection import run as validate_and_score_inputs

BUNDLE = ROOT/'data/eval/patch018-separate'


def same_text(path, captured):
    return (Path(path).read_bytes().replace(b'\r\n', b'\n')
            == Path(captured).read_bytes().replace(b'\r\n', b'\n'))


def join_channels(general, civil):
    if len(general) > 5 or len(civil) > 2:
        raise ValueError('Channel limit exceeded')
    if any(a.startswith('민법-') for a in general) or any(not a.startswith('민법-') for a in civil):
        raise ValueError('Wrong channel')
    joined = general + civil
    if len(set(joined)) != len(joined):
        raise ValueError('Duplicate article')
    return joined


def item_score(targets, baseline, general, civil):
    joined = join_channels(general, civil)
    targets = set(targets)
    return {'all_required': int(targets <= set(joined)) if targets else None,
            'lost_required': sorted((targets & set(baseline)) - set(joined)),
            'total_articles': len(joined), 'civil_count': len(civil),
            'civil_outside_required_gold': sorted(set(civil)-targets)}


def collect(out):
    out.mkdir(parents=True, exist_ok=False)
    validate_and_score_inputs(out/'input-check')
    baseline = ROOT/'data/eval/patch015-baseline/capture'
    manifest = read(baseline/'manifest.json')
    before = {rel: sha(ROOT/rel) for rel in manifest['operating_hashes_before']}
    if before != manifest['operating_hashes_before']:
        raise ValueError('Operating data differs from baseline')
    snapshot = out/'snapshot'
    for rel in before:
        if rel.startswith(('data/chunks/', 'data/index/')):
            dest = snapshot/rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT/rel, dest)
    from src.retrieval.service import RetrievalService, route_law_corpus, detect_civil_topics
    from src.evaluation.baseline import settings
    paths = tuple(snapshot/'data/chunks'/f'{n}.jsonl' for n in ('chunks','cases','guides'))
    svc = RetrievalService.from_index(chunk_paths=paths,
        index_path=snapshot/'data/index/chroma_kurev1_1024',
        civil_index_path=snapshot/'data/index/chroma_civil_kurev1_1024')
    if json.loads(json.dumps(settings(svc))) != manifest['settings']:
        raise ValueError('Settings differ from baseline')
    cache = Path.home()/'.cache/huggingface/hub/models--nlpai-lab--KURE-v1'
    if any(sha(cache/p) != digest for p,digest in manifest['model_files'].items()):
        raise ValueError('Model files differ from baseline')
    sources = ['scripts/patch018_separate.py', 'scripts/patch017_selection.py',
               'data/eval/patch018-separate/plan.json',
               'data/eval/patch015-baseline/bundle-manifest.json',
               'data/eval/patch016-candidates/bundle-manifest.json']
    source_hashes = {p: sha(ROOT/p) for p in sources}
    capture = out/'capture'
    capture.mkdir()
    shutil.copyfile(__file__, capture/'runner.py')
    for rel in sources:
        if rel != 'scripts/patch018_separate.py':
            dest = capture/'source-bytes'/(rel+'.bin')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/rel, dest)
    write(capture/'manifest.json', {'schema':'patch018-capture-v1',
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'status':subprocess.check_output(['git','status','--porcelain'],text=True).strip(),
        'source_hashes':source_hashes, 'settings':settings(svc),
        'model_files':manifest['model_files'], 'operating_before':before,
        'fresh_search':'general law TOP5 only; civil candidates reused from PATCH-016',
        'generation_performed':False})
    rows = []
    for q in read(baseline/'results.json'):
        start = time.perf_counter()
        corpus = route_law_corpus(q['query'], svc.corpora[0])
        hits = svc._search_one(corpus, q['query'], 5)
        general = [norm(svc._chunks[h.chunk_id]['metadata']['article_id']) for h in hits]
        old_general = [norm(h['article_anchor']) for h in q['laws']
                       if not norm(h['article_anchor']).startswith('민법-')]
        if general[:len(old_general)] != old_general:
            raise ValueError('General ranking prefix changed: '+q['qid']+' '+q['mode'])
        topics = detect_civil_topics(q['query'])
        if [t.name for t in topics] != q['civil_topics']:
            raise ValueError('Civil gate changed')
        rows.append({'qid':q['qid'], 'mode':q['mode'], 'query_sha256':q['query_sha256'],
                     'general':general, 'gate':bool(topics), 'where':corpus.where(),
                     'general_seconds':time.perf_counter()-start})
        if len(rows)%25 == 0:
            print(f'{len(rows)}/235 general searches', flush=True)
    after = {rel:sha(ROOT/rel) for rel in before}
    if before != after or any(sha(ROOT/p) != digest for p,digest in source_hashes.items()):
        raise ValueError('Inputs changed during capture')
    write(capture/'results.json', rows)
    write(capture/'audit.json', {'queries':len(rows), 'operating_after':after,
          'operating_unchanged':True, 'sources_unchanged':True,
          'general_prefix_preserved':True, 'gate_preserved':True})


def validate_capture(capture, *, local=False):
    capture = capture.resolve()
    bundle = capture.parent/'bundle-manifest.json'
    required = {'capture/manifest.json','capture/results.json','capture/audit.json','capture/runner.py',
                'plan.json','report/summary.json','report/details.json'}
    if bundle.is_file():
        index = read(bundle)
        if index.get('schema') != 'patch018-bundle-v1' or not required <= set(index.get('files',{})):
            raise ValueError('Invalid bundle manifest')
        for rel,digest in index['files'].items():
            path = (capture.parent/rel).resolve()
            if not path.is_relative_to(capture.parent) or not path.is_file() or sha(path) != digest:
                raise ValueError('Bundle hash/path mismatch: '+rel)
    elif not local:
        raise ValueError('Published replay requires bundle manifest')
    audit = read(capture/'audit.json')
    manifest = read(capture/'manifest.json')
    if (type(audit.get('queries')) is not int or audit['queries'] != 235
        or any(audit.get(k) is not True for k in ('operating_unchanged','sources_unchanged',
                                                'general_prefix_preserved','gate_preserved'))
        or audit['operating_after'] != manifest['operating_before']):
        raise ValueError('Invalid capture audit')
    for rel,digest in manifest['source_hashes'].items():
        # The captured runner is historical provenance, not the replay implementation.
        runner = rel == 'scripts/patch018_separate.py'
        source = capture/'runner.py' if runner else capture/'source-bytes'/(rel+'.bin')
        if sha(source) != digest or (not runner and not same_text(ROOT/rel, source)):
            raise ValueError('Capture source changed: '+rel)


def report(capture, out, *, local=False):
    validate_capture(capture, local=local)
    out.mkdir(parents=True, exist_ok=False)
    validate_and_score_inputs(out/'input-check')
    baseline = read(out/'input-check/details.json')
    captured = read(capture/'results.json')
    byid = {(r['qid'],r['mode']):r for r in captured}
    candidates = {(r['qid'],r['mode']):r for r in read(ROOT/'data/eval/patch016-candidates/capture/results.json')}
    if len(captured) != 235 or len(byid) != 235 or set(byid) != set(candidates):
        raise ValueError('Expected same 235 unique inputs')
    variants = ('split_original', 'split_gated_top2', 'split_all_top2')
    rows = []
    for b in baseline:
        key = b['qid'],b['mode']
        c, candidate = byid[key],candidates[key]
        if c['query_sha256'] != candidate['query_sha256'] or type(c['gate']) is not bool:
            raise ValueError('Query/gate mismatch')
        original = b['rankings']['baseline']
        original_civil = [a for a in original if a.startswith('민법-')]
        general = c['general']
        old_general = [a for a in original if not a.startswith('민법-')]
        if len(general) != 5 or general[:len(old_general)] != old_general:
            raise ValueError('Invalid general ranking')
        top2 = [norm(a) for a in candidate['rankings']['rrf'][:2]]
        civil = {'split_original':original_civil,
                 'split_gated_top2':top2 if c['gate'] else [], 'split_all_top2':top2}
        targets = b['targets']
        rows.append({'qid':b['qid'],'mode':b['mode'],'cohort':b['cohort'],
                     'targets':targets,'general':general,'civil':civil,
                     'baseline_all_required':int(set(targets)<=set(original)) if targets else None,
                     'baseline_count':len(original),
                     'scores':{v:item_score(targets,original,general,civil[v]) for v in variants}})
    summaries = {}
    for cohort in sorted({r['cohort'] for r in rows}):
        rs = [r for r in rows if r['cohort']==cohort]
        scored = [r for r in rs if r['targets']]
        summaries[cohort] = {'n':len(rs),'scorable_n':len(scored),
            'baseline_all_required':sum(r['baseline_all_required'] for r in scored) if scored else None,
            'variants':{v:{
                'all_required':sum(r['scores'][v]['all_required'] for r in scored) if scored else None,
                'lost_required_inputs':sum(bool(r['scores'][v]['lost_required']) for r in scored),
                'zero_civil_inputs':sum(not r['civil'][v] for r in rs),
                'mean_total_law_articles':sum(r['scores'][v]['total_articles'] for r in rs)/len(rs),
                'no_required_civil_gold_n':sum(not any(t.startswith('민법-') for t in r['targets']) for r in rs),
                'civil_returned_without_required_civil_gold_n':sum(bool(r['civil'][v]) and not any(t.startswith('민법-') for t in r['targets']) for r in rs),
                'outside_required_gold_articles':sum(len(r['scores'][v]['civil_outside_required_gold']) for r in rs),
            } for v in variants}}
    write(out/'summary.json', summaries)
    write(out/'details.json', rows)
    print(json.dumps(summaries,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--replay',type=Path)
    parser.add_argument('--local-capture',action='store_true')
    args = parser.parse_args()
    if not args.out.resolve().is_relative_to(ROOT/'tmp') or args.out.exists():
        parser.error('Use a new directory under tmp/')
    if args.local_capture and not args.replay:
        parser.error('--local-capture requires --replay')
    if args.replay:
        report(args.replay,args.out,local=args.local_capture)
    else:
        os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                          ANONYMIZED_TELEMETRY='False',LANGSMITH_TRACING='false')
        collect(args.out)
