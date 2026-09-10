"""Replay PATCH-015 law-article diagnostics from a completed capture."""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from scripts.patch015_baseline import ROOT, norm, read, score, sha, write
from scripts.dev100_v2.report import diagnose, load_dataset, validate_data


def summarize(rows):
    result = {'n':len(rows)}
    for name in ('AnyHit@3','AnyHit@5','AllRequired@3','AllRequired@5'):
        values = [r['metrics'][name] for r in rows]
        if any(v is None for v in values):
            raise ValueError('Cannot aggregate targetless diagnostics as successes')
        result[name] = {'hits':sum(values), 'n':len(rows), 'rate':sum(values)/len(rows) if rows else None}
    result['data_missing_items'] = sum(bool(r['absent']) for r in rows)
    result['candidate_not_observed_items'] = sum(bool(r['candidate_not_observed']) for r in rows)
    result['candidate_present_not_top5_items'] = sum(bool(r['candidate_present_not_top5']) for r in rows)
    return result


def check_shared_bundle(run):
    manifest_path = run.parent/'bundle-manifest.json'
    if not manifest_path.is_file():
        return  # Fresh local captures have no published bundle yet.
    files = read(manifest_path)['files']
    required = {'capture/'+n for n in ('manifest.json','audit.json','inventory.json','results.json')}
    if not required <= set(files):
        raise ValueError('Bundle omits capture files')
    for name,digest in files.items():
        path = (run.parent/name).resolve()
        if not path.is_relative_to(run.parent.resolve()) or sha(path)!=digest:
            raise ValueError('Bundle hash/path mismatch: '+name)


def report(run, out):
    check_shared_bundle(run)
    manifest, audit = read(run/'manifest.json'), read(run/'audit.json')
    if not audit['operating_unchanged'] or not audit['dataset_unchanged'] or audit['queries'] != 235:
        raise ValueError('Incomplete or inconsistent run audit')
    if manifest['operating_hashes_before'] != audit['operating_hashes_after']:
        raise ValueError('Operating hashes changed')
    for rel, digest in manifest['dataset_hashes'].items():
        if sha(ROOT/rel) != digest: raise ValueError('Criteria hash mismatch: '+rel)
    devplan, old = load_dataset(ROOT/'data/eval/dev100-v2')
    civil = read(ROOT/'data/eval/civil-review2/retrieval-plan.json')
    results, inventory = read(run/'results.json'), read(run/'inventory.json')
    devqs = read(ROOT/'data/eval/dev100-v2/questions.json')
    inputs = {(q['qid'],mode):v['query'] for q in devqs for mode,v in q['modes'].items()}
    inputs.update({(q['id'],'question_context'):q['input_text'] for q in civil['items']})
    if len(results) != 235 or {(r['qid'],r['mode']) for r in results} != set(inputs):
        raise ValueError('Missing, duplicate or unexpected inputs')
    inv = {r['chunk_id']:r for r in inventory}
    if len(inv) != len(inventory): raise ValueError('Duplicate inventory')
    for r in results:
        if r['query'] != inputs[r['qid'],r['mode']]: raise ValueError('Input text changed')
        if r['query_sha256'] != hashlib.sha256(r['query'].encode()).hexdigest():
            raise ValueError('Query hash mismatch')
        if r['group'] != ('civil35' if r['qid'].startswith('CIV-') else 'dev200'):
            raise ValueError('Input group changed')
        for kind, limit in (('laws',5),('cases',5),('guides',2)):
            hits = r[kind]
            if len(hits)>limit or [h['rank'] for h in hits] != list(range(1,len(hits)+1)):
                raise ValueError('Invalid returned ranks')
            if len({h['chunk_id'] for h in hits}) != len(hits):
                raise ValueError('Duplicate returned chunk')
        for h in r['laws']:
            if h['body_sha256'] != inv[h['chunk_id']]['body_sha256'] or h['article_anchor'] != inv[h['chunk_id']]['article_anchor']:
                raise ValueError('Law evidence does not match inventory')
    reference = {'schema':'dev100-reference-run-1','inventory':inventory,
                 'results':[r for r in results if r['group']=='dev200']}
    validate_data(devqs,devplan,reference,read(ROOT/'data/eval/dev100-v2/source-registry.json'))
    devout = diagnose(devplan,reference,sha(ROOT/'data/eval/dev100-v2/diagnostic-plan.json'))
    devout['summary.json']['new_retrieval_run'] = True
    devout['summary.json']['old_scores_comparable'] = inventory == old['inventory']
    details = []
    lookup = {i['id']:i for i in civil['items']}
    devlookup = {i['qid']:i for i in devplan}
    available = {r['article_anchor'] for r in inventory}
    for r in results:
        item = lookup[r['qid']] if r['group']=='civil35' else devlookup[r['qid']]
        targets = ({t for g in item['required_groups_all_of'] for t in g['article_targets_all_of']} if r['group']=='civil35'
                   else {t['article_anchor'] for t in item['law_targets']})
        candidates = {inv[cid]['article_anchor'] for call in r['trace'] for hits in call['members'].values() for cid in hits if cid in inv}
        scored = score(targets,[h['article_anchor'] for h in r['laws']],available,candidates)
        details.append({'qid':r['qid'],'mode':r['mode'],'group':r['group'],
                        'track':item.get('track'),**scored})
    crows = [r for r in details if r['group']=='civil35']
    primary = [r for r in crows if r['track']=='required_law']
    if len(primary)!=29: raise ValueError('Primary denominator drift')
    civsummary = {'primary':summarize(primary),
                  'all_targets_held':summarize([r for r in primary if not r['absent']]),
                  'scope_provisional':summarize([r for r in crows if r['track']=='scope_provisional']),
                  'diagnostic_only_ids':[r['qid'] for r in crows if r['track']=='diagnostic_only'],
                  'unit':'article identifier proxy, not paragraph coverage or answer accuracy',
                  'failure_counts_overlap':True}
    oldruns = {(r['qid'],r['mode']):r for r in old['results']}
    changed = {kind:[{'qid':r['qid'],'mode':r['mode']} for r in reference['results']
                     if [h['chunk_id'] for h in r[kind]] != [h['chunk_id'] for h in oldruns[r['qid'],r['mode']][kind]]]
               for kind in ('laws','cases','guides')}
    out.mkdir(parents=True,exist_ok=False)
    for name,value in devout.items(): write(out/('dev200-'+name),value)
    write(out/'civil35-summary.json',civsummary)
    write(out/'target-diagnostics.json',details)
    write(out/'previous-ranking-changes.json',changed)
    write(out/'report-manifest.json',{'capture_hashes':{n:sha(run/n) for n in ('manifest.json','audit.json','inventory.json','results.json')},
                                    'reporter_sha256':sha(__file__),'metrics_module_sha256':sha(ROOT/'scripts/patch015_baseline.py')})
    print('DEV200',devout['summary.json']['counts'])
    print('CIV35',civsummary)
    print('Changed rankings', {k:len(v) for k,v in changed.items()})


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if not args.out.resolve().is_relative_to(ROOT/'tmp') or args.out.exists():
        parser.error('Use a new report directory under tmp/')
    report(args.run,args.out)
