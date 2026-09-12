"""Replay partition comparisons and expose TOP3 regressions separately from TOP5."""
import argparse
from pathlib import Path

from scripts.patch026_expand import ROOT, read, write, sha, score
from scripts.patch026_report import summarize as previous_summary
from scripts.patch025_ranking import close
from scripts.patch027_partition import report, select

BUNDLE = ROOT/'data/eval/patch027-partition'
REQUIRED = {'traces.json', 'audit.json', 'live.json', 'summary.json'}
SELECTED = 'pool_equal'


def summarize(bundle=BUNDLE, verify=True):
    if verify:
        manifest = read(bundle/'manifest.json')
        if set(manifest) != REQUIRED:
            raise ValueError('Incomplete bundle')
        if any(sha(bundle/p) != digest for p, digest in manifest.items()):
            raise ValueError('Bundle hash mismatch')
    baseline = previous_summary()['baseline']
    reports = report(bundle)
    audit = read(bundle/'audit.json')
    traces = read(bundle/'traces.json')
    before = {(r['qid'], r['mode']): r for r in read(ROOT/'data/eval/patch026-expansion/before.json')}
    live = read(bundle/'live.json')
    if len(live['rows']) != 235 or {(r['qid'], r['mode']) for r in live['rows']} != set(before):
        raise ValueError('Incomplete product verification')
    if live['trace_sha256'] != sha(bundle/'traces.json') or live['index_semantic_hashes'] != audit['index_semantic_hashes']:
        raise ValueError('Product verification provenance mismatch')
    if not live['clean'] or not live['candidate_only'] or live['matches'] != 235:
        raise ValueError('Invalid product verification status')
    live_rows = {(r['qid'], r['mode']): r for r in live['rows']}
    variants = {}
    for policy, measured in reports.items():
        details = {(r['qid'], r['mode']): r for r in measured['details']}
        rank_losses = {str(k): [] for k in (1, 3, 5)}
        changed = 0
        procedure_counts = {str(n): 0 for n in range(6)}
        for row in traces:
            key = row['qid'], row['mode']
            ids = [cid for cid, _ in row[policy][:5]] if policy in ('statistics_only', 'mixed') else select(row, policy)
            laws = [audit['candidate_anchors'][cid] for cid in ids]
            old = before[key]
            changed += laws != old['laws']
            n = sum(law in audit['procedures'] for law in laws)
            procedure_counts[str(n)] += 1
            for k in (1, 3, 5):
                lost = sorted((set(old['laws'][:k]) & set(details[key]['targets'])) - set(laws[:k]))
                if lost:
                    rank_losses[str(k)].append({'qid': key[0], 'mode': key[1], 'lost': lost})
            if policy == SELECTED:
                expected = {**old, 'laws': laws}
                if live_rows[key] != expected:
                    raise ValueError('Product output differs from replay or changes another channel')
        variants[policy] = {'groups': measured['groups'], 'losses': measured['losses'],
                            'prior_required_rank_losses': rank_losses, 'changed_inputs': changed,
                            'procedure_count_distribution': procedure_counts}
    measured_live = score(live['rows'], read(ROOT/'data/eval/patch026-expansion/full/audit.json')['available_after'], list(before.values()))
    if not close(measured_live, reports[SELECTED]) or not close(live['report'], measured_live):
        raise ValueError('Product metrics mismatch')
    selected = variants[SELECTED]
    result = {'baseline': baseline, 'variants': variants, 'implemented_candidate': SELECTED,
              'top5_preservation_passed': not selected['losses'],
              'top3_preservation_passed': not selected['prior_required_rank_losses']['3'],
              'operating_data_adopted': False, 'product_matches': len(live_rows),
              'other_channels_preserved': True}
    if verify and not close(result, read(bundle/'summary.json')):
        raise ValueError('Summary mismatch')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=BUNDLE)
    parser.add_argument('--write-summary', action='store_true')
    args = parser.parse_args()
    result = summarize(args.bundle, verify=not args.write_summary)
    if args.write_summary:
        write(args.bundle/'summary.json', result)
        write(args.bundle/'manifest.json', {p: sha(args.bundle/p) for p in sorted(REQUIRED)})
    print('235 product outputs match; TOP5 preservation:', result['top5_preservation_passed'],
          '; TOP3 preservation:', result['top3_preservation_passed'], '; operating data adopted: False')
