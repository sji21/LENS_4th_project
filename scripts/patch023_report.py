"""Replay separated-channel diagnostics on unchanged public development targets."""
import argparse
import hashlib
from collections import Counter
from pathlib import Path

from scripts.patch015_baseline import ROOT, read, sha, write, norm
from scripts.patch015_report import check_shared_bundle
from scripts.dev100_v2.report import load_dataset


def diagnose(targets, available, laws, civil, historical=False):
    targets, available = set(map(norm, targets)), set(map(norm, available))
    laws, civil = list(map(norm, laws)), list(map(norm, civil))
    if len(laws) > 5 or len(civil) > 3 or len(set(laws)) != len(laws) or len(set(civil)) != len(civil):
        raise ValueError('Invalid channel budget or duplicates')
    if any(x.startswith('민법-') for x in laws) or any(not x.startswith('민법-') for x in civil):
        raise ValueError('Wrong law channel')
    returned = set(laws + civil)
    if not returned <= available:
        raise ValueError('Returned article absent from inventory')
    absent = targets - available
    missed = (targets & available) - returned
    category = ('historical_review' if historical else 'no_fixed_target' if not targets else
                'data_missing_all' if targets == absent else 'data_missing_some' if absent else
                'retrieval_miss' if missed else 'all_required_returned')
    metrics = {}
    for channel, ranked, is_civil, ks in [('general', laws, False, (1,2,3,5)), ('civil', civil, True, (1,2,3))]:
        gold = {x for x in targets if x.startswith('민법-') == is_civil}
        metrics[channel] = None if not gold or historical else {
            'gold': sorted(gold), 'all_targets_held': gold <= available,
            'mrr': next((1 / i for i, x in enumerate(ranked, 1) if x in gold), 0),
            **{f'hit@{k}': int(bool(gold & set(ranked[:k]))) for k in ks},
            **{f'recall@{k}': len(gold & set(ranked[:k])) / len(gold) for k in ks}}
    return {'category': category, 'targets': sorted(targets), 'absent': sorted(absent),
            'held_not_returned': sorted(missed), 'channels': {'general': laws, 'civil': civil},
            'channel_metrics': metrics,
            'all_required_law5_civil3': None if historical or not targets else not absent and not missed,
            'civil_outside_fixed_gold': sorted(set(civil) - targets)}


def summarize(rows):
    counts = dict(Counter(r['category'] for r in rows))
    metrics = {}
    for channel in ('general', 'civil'):
        values = [r['channel_metrics'][channel] for r in rows if r['channel_metrics'][channel] is not None]
        metrics[channel] = {}
        for subset, selected in [('all_target_items', values), ('all_targets_held', [v for v in values if v['all_targets_held']])]:
            keys = [k for k in selected[0] if k not in ('gold', 'all_targets_held')] if selected else []
            metrics[channel][subset] = {'n': len(selected), **{k: sum(v[k] for v in selected)/len(selected) for k in keys}}
    scored = [r for r in rows if r['all_required_law5_civil3'] is not None]
    return {'n': len(rows), 'categories': counts, 'channel_metrics': metrics,
            'union_all_required': {'hits': sum(r['all_required_law5_civil3'] for r in scored), 'n': len(scored)}}


def report(run):
    run = run.resolve()
    if run.is_relative_to(ROOT/'data/eval'):
        bundle = read(run.parent/'bundle-manifest.json')
        required = {'capture/audit.json', 'capture/results.json', 'report.json'}
        if not required <= bundle['files'].keys():
            raise ValueError('Incomplete published bundle')
        for rel, digest in bundle['files'].items():
            path = (run.parent/rel).resolve()
            if not path.is_relative_to(run.parent) or sha(path) != digest:
                raise ValueError('Published bundle hash/path mismatch')
    check_shared_bundle(ROOT/'data/eval/patch015-baseline/capture')
    oldmanifest = read(ROOT/'data/eval/patch015-baseline/capture/manifest.json')
    for rel, digest in oldmanifest['dataset_hashes'].items():
        raw = (ROOT/rel).read_bytes()
        lf = raw.replace(b'\r\n', b'\n')
        hashes = {hashlib.sha256(b).hexdigest() for b in (raw, lf, lf.replace(b'\n', b'\r\n'))}
        if digest not in hashes:
            raise ValueError('Evaluation criteria changed: ' + rel)
    plan, _ = load_dataset(ROOT/'data/eval/dev100-v2')
    civilplan = read(ROOT/'data/eval/civil-review2/retrieval-plan.json')['items']
    audit, rows = read(run/'audit.json'), read(run/'results.json')
    if audit['status'] or audit['operating_before'] != audit['operating_after'] or audit['operating_before'] != oldmanifest['operating_hashes_before']:
        raise ValueError('Invalid capture provenance/data')
    if audit['settings']['search_k'] != {'k_law':5,'k_case':5,'k_guide':2,'k_civil':3}:
        raise ValueError('Unexpected budgets')
    if audit['queries'] != 235 or not audit['general_and_civil_match_patch020'] or not audit['case_and_guide_match_patch015']:
        raise ValueError('Incomplete capture')
    source = read(ROOT/'data/eval/patch015-baseline/capture/results.json')
    previous = {(r['qid'],r['mode']):r for r in source}
    keys = [(r['qid'],r['mode']) for r in rows]
    if len(keys)!=235 or len(set(keys))!=235 or set(keys)!=set(previous):
        raise ValueError('Input identity mismatch')
    inventory = read(ROOT/'data/eval/patch015-baseline/capture/inventory.json')
    available = {r['article_anchor'] for r in inventory}
    dev = {q['qid']:q for q in plan}; civ = {q['id']:q for q in civilplan}
    details=[]
    for row in rows:
        is_civil = row['qid'] in civ
        item = civ[row['qid']] if is_civil else dev[row['qid']]
        targets = {a for g in item['required_groups_all_of'] for a in g['article_targets_all_of']} if is_civil else {t['article_anchor'] for t in item['law_targets']}
        track = item['track'] if is_civil else 'dev100'
        d = diagnose(targets, available, row['laws'], row['civil_laws'], item.get('historical_review_required',False))
        old = previous[row['qid'],row['mode']]
        if any(row[k] != [e['chunk_id'] for e in old[k]] for k in ('cases','guides')):
            raise ValueError('Case/guide ranks changed')
        old_returned = {norm(e['article_anchor']) for e in old['laws']}
        d.update(qid=row['qid'], mode=row['mode'], track=track,
                 lost_prior_required=sorted((old_returned & set(d['targets'])) - set(row['laws']+row['civil_laws'])))
        details.append(d)
    groups = {mode:summarize([r for r in details if r['track']=='dev100' and r['mode']==mode]) for mode in ('question_only','context_diagnostic')}
    for track in ('required_law','scope_provisional','diagnostic_only'):
        groups[track] = summarize([r for r in details if r['track']==track])
    if [groups[k]['n'] for k in groups]!=[100,100,29,1,5]:
        raise ValueError('Denominator drift')
    missing = {}
    for r in details:
        for a in r['absent']:
            missing.setdefault(a,set()).add(r['qid'])
    return {'summary': {'unit':'fixed required articles, not paragraph/answer accuracy',
        'capture_commit':audit['commit'], 'settings':audit['settings']['search_k'], 'groups':groups,
        'prior_required_loss_inputs':sum(bool(r['lost_prior_required']) for r in details),
        'cases_guides_preserved':True, 'civil_exposure_inputs':sum(bool(r['channels']['civil']) for r in details),
        'outside_gold_is_not_irrelevance':True}, 'details':details,
        'missing_articles':[{'article':a,'qids':sorted(q)} for a,q in sorted(missing.items())],
        'inputs':{'capture':{n:sha(run/n) for n in ('audit.json','results.json')},
                  'criteria_original':oldmanifest['dataset_hashes'],
                  'criteria_current':{rel:sha(ROOT/rel) for rel in oldmanifest['dataset_hashes']},
                  'criteria_comparison':'raw/LF/CRLF equivalence; no other normalization'}}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True); p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists(): p.error('Use a new output file')
    write(a.out,report(a.run))
