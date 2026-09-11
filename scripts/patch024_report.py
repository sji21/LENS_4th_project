"""Replay PATCH-024 without a model or an operating database."""
import hashlib
from scripts.patch015_baseline import ROOT, read, sha
from scripts.patch023_report import diagnose, summarize

BUNDLE = ROOT/'data/eval/patch024-expansion'


def verify(bundle=BUNDLE):
    manifest = read(bundle/'manifest.json')
    required = {'audit.json', 'results.json', 'report.json', 'adoption.json',
                'operating-verification.json', 'provenance.json', 'new-chunks.jsonl',
                'operating-verifier.py'}
    if set(manifest['files']) != required:
        raise ValueError('Incomplete evidence bundle')
    baseline = {'data/eval/patch023-baseline/report.json',
                'data/eval/patch023-baseline/capture/results.json',
                'data/eval/patch015-baseline/capture/inventory.json',
                'data/eval/patch015-baseline/capture/results.json'}
    if set(manifest['baseline_files']) != baseline:
        raise ValueError('Incomplete baseline evidence')
    for rel, digest in manifest['files'].items():
        if sha(bundle/rel) != digest:
            raise ValueError(f'Evidence changed: {rel}')
    for rel, digest in manifest['baseline_files'].items():
        if hashlib.sha256((ROOT/rel).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != digest:
            raise ValueError(f'Baseline changed: {rel}')
    prior = read(ROOT/'data/eval/patch023-baseline/report.json')['details']
    inventory = read(ROOT/'data/eval/patch015-baseline/capture/inventory.json')
    available = {r['article_anchor'] for r in inventory} | {'민법-제105조', '민법-제114조', '민법-제357조'}
    rows = read(bundle/'results.json')
    expected_keys = {(r['qid'], r['mode']) for r in prior}
    if len(rows) != 235 or {(r['qid'], r['mode']) for r in rows} != expected_keys:
        raise ValueError('Incomplete or duplicate inputs')
    old = {(r['qid'], r['mode']): r for r in prior}
    previous = {(r['qid'], r['mode']):r for r in read(ROOT/'data/eval/patch023-baseline/capture/results.json')}
    details = []
    for r in rows:
        if any(r[k] != previous[r['qid'], r['mode']][k] for k in ('laws', 'cases', 'guides')):
            raise ValueError('Other channel changed')
        target = old[r['qid'], r['mode']]
        d = diagnose(target['targets'], available, r['laws'], r['civil_laws'], target['category']=='historical_review')
        oldhit = set(target['targets']) & set(target['channels']['general'] + target['channels']['civil'])
        d.update(qid=r['qid'], mode=r['mode'], track=target['track'],
                 lost_prior_required=sorted(oldhit-set(r['laws']+r['civil_laws'])))
        details.append(d)
    groups = {m:summarize([r for r in details if r['track']=='dev100' and r['mode']==m])
              for m in ('question_only', 'context_diagnostic')}
    groups.update({t:summarize([r for r in details if r['track']==t])
                   for t in ('required_law','scope_provisional','diagnostic_only')})
    losses = [{'qid':r['qid'],'mode':r['mode'],'lost':r['lost_prior_required']} for r in details if r['lost_prior_required']]
    result = {'groups':groups,'losses':losses,'details':details}
    if result != read(bundle/'report.json'):
        raise ValueError('Report does not match frozen targets and results')
    return result


if __name__ == '__main__':
    result = verify()
    print('235 inputs replayed; prior required losses:', len(result['losses']))
