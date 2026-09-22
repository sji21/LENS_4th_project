"""Record what moved in the case channel between two captures, rank by rank.

The law and civil channels are compared by required-evidence coverage elsewhere.
Cases need their own record: the operating budget returns at most two, the
corpus holds 8,379 judgments, and an approximate vector index does not promise
the same tail on every run. This file states the movement instead of implying
that an unchanged score means an unchanged ranking.
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def ranking(row, run):
    return [{'rank': item.get('rank'),
             'citation': item.get('citation', ''),
             'identity': str(item.get('canonical_case_key') or item.get('case_id') or '')}
            for item in row['runs'][run]['result'].get('cases', [])]


def moved(before, after):
    was = {item['identity']: item['rank'] for item in before}
    now = {item['identity']: item['rank'] for item in after}
    return {
        'entered': [item for item in after if item['identity'] not in was],
        'left': [item for item in before if item['identity'] not in now],
        'reordered': [{'identity': key, 'rank': was[key], 'to': now[key]}
                      for key in was if key in now and was[key] != now[key]],
    }


def compare(before, after, runs):
    detail, totals = [], Counter()
    for old, new in zip(before, after):
        job = new['job']
        if (old['job']['qid'], old['job']['mode']) != (job['qid'], job['mode']):
            raise ValueError('Capture rows are not aligned: ' + job['qid'])
        entry = {'qid': job['qid'], 'input_mode': job['mode'], 'scored': job['score'],
                 'required_cases': [t['label'] for t in job['targets']['cases']], 'runs': {}}
        for run in runs:
            change = moved(ranking(old, run), ranking(new, run))
            if not any(change.values()):
                continue
            entry['runs'][run] = change
            totals[run + ':inputs'] += 1
            for name in ('entered', 'left', 'reordered'):
                totals[run + ':' + name] += len(change[name])
        if entry['runs']:
            detail.append(entry)
    return detail, dict(sorted(totals.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--label', default='')
    parser.add_argument('--runs', nargs='+',
                        default=['raw3', 'raw5', 'staged_operating', 'staged_diagnostic'])
    args = parser.parse_args()
    before, after = rows(args.before), rows(args.after)
    if len(before) != len(after):
        raise ValueError('Captures have different input counts')
    detail, totals = compare(before, after, args.runs)
    payload = {
        'label': args.label,
        'capture_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in (args.before, args.after)},
        'runs': args.runs,
        'note': 'Case identity is the canonical case key. A changed ranking is not by '
                'itself a scoring change; the case metrics are reported separately.',
        'totals': totals,
        'inputs': detail,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(args.label or args.out.name, totals, '| changed inputs:', len(detail))


if __name__ == '__main__':
    main()
