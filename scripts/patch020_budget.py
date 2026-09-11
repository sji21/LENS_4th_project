"""Measure recall versus civil candidate budget while retaining existing picks."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from scripts.patch015_baseline import ROOT, read, write, norm, sha
from scripts.patch018_separate import report as replay_separation

BASE = ROOT/'data/eval/patch020-budget'
BUDGETS = (2, 3, 4, 5, 6, 7)


def fill_candidates(existing, ranking, budget):
    existing, ranking = [norm(a) for a in existing], [norm(a) for a in ranking]
    if budget not in BUDGETS or len(existing) > budget:
        raise ValueError('Invalid candidate budget')
    if len(set(existing)) != len(existing) or len(set(ranking)) != len(ranking):
        raise ValueError('Duplicate candidate')
    if not set(existing) <= set(ranking):
        raise ValueError('Existing pick absent from candidate pool')
    return (existing + [a for a in ranking if a not in existing])[:budget]


def evaluate(targets, baseline, general, civil, civil_pool):
    targets = set(targets)
    civil_targets = targets & civil_pool
    joined = set(general + civil)
    return {'all_required': int(targets <= joined) if targets else None,
            'held_civil_all': int(civil_targets <= set(civil)) if civil_targets else None,
            'lost_required': sorted((targets & set(baseline)) - joined),
            'outside_required_gold': sorted(set(civil)-targets),
            'law_count':len(joined)}


def run(out):
    plan = read(BASE/'plan.json')
    if plan['budgets'] != list(BUDGETS):
        raise ValueError('Unexpected budget plan')
    out.mkdir(parents=True, exist_ok=False)
    replay_separation(ROOT/'data/eval/patch018-separate/capture', out/'validated-inputs')
    previous = read(out/'validated-inputs/details.json')
    candidates = {(r['qid'],r['mode']):r for r in read(ROOT/'data/eval/patch016-candidates/capture/results.json')}
    inventory = read(ROOT/'data/eval/patch015-baseline/capture/inventory.json')
    pool = {norm(r['article_anchor']) for r in inventory if r['title']=='민법'}
    if len(pool) != 7 or len(previous) != 235:
        raise ValueError('Unexpected input dimensions')
    rows = []
    for old in previous:
        ranking = [norm(a) for a in candidates[old['qid'],old['mode']]['rankings']['rrf']]
        if len(ranking)!=7 or set(ranking)!=pool:
            raise ValueError('Incomplete civil candidates')
        existing = old['civil']['split_original']
        # All former general hits are a prefix of this validated TOP5 list.
        baseline = old['general'] + existing
        policies = {}
        for budget in BUDGETS:
            for mode in ('retain','rrf'):
                selected = fill_candidates(existing,ranking,budget) if mode=='retain' else ranking[:budget]
                policies[f'{mode}_{budget}'] = {'civil':selected,
                    **evaluate(old['targets'],baseline,old['general'],selected,pool)}
        rows.append({'qid':old['qid'],'mode':old['mode'],'cohort':old['cohort'],
            'targets':old['targets'],'existing_civil':existing,'general':old['general'],'policies':policies})
    summary = {}
    for cohort in sorted({r['cohort'] for r in rows}):
        rs = [r for r in rows if r['cohort']==cohort]
        summary[cohort] = {'n':len(rs),'policies':{}}
        for policy in rows[0]['policies']:
            values = [r['policies'][policy] for r in rs]
            full = [v['all_required'] for v in values if v['all_required'] is not None]
            civil = [v['held_civil_all'] for v in values if v['held_civil_all'] is not None]
            summary[cohort]['policies'][policy] = {
                'all_required':sum(full) if full else None, 'all_required_n':len(full),
                'held_civil_all':sum(civil) if civil else None,'held_civil_n':len(civil),
                'lost_required_inputs':sum(bool(v['lost_required']) for v in values),
                'mean_law_count':sum(v['law_count'] for v in values)/len(values),
                'outside_required_gold_articles':sum(len(v['outside_required_gold']) for v in values)}
    decision = {}
    scored_cohorts = ('civil_required_law','dev_question_only','dev_context_diagnostic')
    for mode in ('retain','rrf'):
        qualifying = [b for b in BUDGETS if all(
            summary[c]['policies'][f'{mode}_{b}']['held_civil_all']==summary[c]['policies'][f'{mode}_{b}']['held_civil_n']
            and summary[c]['policies'][f'{mode}_{b}']['lost_required_inputs']==0 for c in scored_cohorts)]
        decision[mode] = {'smallest_observed_budget':min(qualifying) if qualifying else None,
                          'deployment_approved':False}
    write(out/'summary.json', {'cohorts':summary,'decision':decision})
    write(out/'details.json', rows)
    paths = [BASE/'plan.json', Path(__file__),
        ROOT/'data/eval/patch018-separate/bundle-manifest.json',
        ROOT/'data/eval/patch016-candidates/bundle-manifest.json']
    write(out/'manifest.json', {'schema':'patch020-run-v1',
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'status':subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip(),
        'hash_format':'SHA256 of bytes with CRLF replaced by LF; no other normalization',
        'source_hashes':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in paths},
        'new_search':False,'generation':False,'operating_changes':False})
    print(json.dumps(decision))


def check_bundle():
    required = {'plan.json','results/summary.json','results/details.json','results/manifest.json'}
    manifest = read(BASE/'bundle-manifest.json')
    if manifest.get('schema')!='patch020-bundle-v1' or not required <= set(manifest.get('files',{})):
        raise ValueError('Incomplete published bundle')
    for rel,digest in manifest['files'].items():
        path = (BASE/rel).resolve()
        if not path.is_relative_to(BASE) or not path.is_file() or sha(path)!=digest:
            raise ValueError('Bundle hash/path mismatch')


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    args = parser.parse_args()
    if not args.out.resolve().is_relative_to(ROOT/'tmp') or args.out.exists():
        parser.error('Use a new directory under tmp/')
    if args.check:
        check_bundle()
    run(args.out)
    if args.check:
        for name in ('summary.json','details.json'):
            if sha(args.out/name)!=sha(BASE/'results'/name):
                raise ValueError('Replay differs from published results: '+name)
