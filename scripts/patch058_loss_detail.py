"""List the before/after evidence of every changed input, without running retrieval.

The comparison file says which required label was lost or gained. This file adds
what was actually returned in each channel, so a loss can be traced to a rank
change instead of being restated as a score.
"""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.patch057_comprehensive_eval import evidence_identity


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def returned(row, run, channel):
    return [{'rank': item.get('rank'),
             'citation': item.get('citation', ''),
             'identity': evidence_identity(item, channel),
             'url': item.get('source_url', '')}
            for item in row['runs'][run]['result'].get(channel, [])]


def changed(before, after, run, targets):
    for channel, labels in targets.items():
        if not labels:
            continue
        wanted = {label['identity'] for label in labels}
        was = {evidence_identity(e, channel) for e in before['runs'][run]['result'].get(channel, [])}
        now = {evidence_identity(e, channel) for e in after['runs'][run]['result'].get(channel, [])}
        if (was & wanted) != (now & wanted):
            return True
    return False


def detail(before, after, runs):
    result = []
    for old, new in zip(before, after):
        job = new['job']
        if (old['job']['qid'], old['job']['mode'], old['job']['query']) != (
                job['qid'], job['mode'], job['query']):
            raise ValueError('Capture rows are not aligned: ' + job['qid'])
        if not job['score']:
            continue
        holdout = job['suite'] == 'holdout30'
        if not holdout and not any(changed(old, new, run, job['targets']) for run in runs):
            continue
        result.append({
            'qid': job['qid'], 'input_mode': job['mode'], 'group': job['group'],
            'question': job['query'],
            'required_labels': {channel: [t['label'] for t in labels]
                                for channel, labels in job['targets'].items()},
            'runs': {run: {'before': {c: returned(old, run, c) for c in job['targets']},
                           'after': {c: returned(new, run, c) for c in job['targets']}}
                     for run in runs},
        })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--runs', nargs='+', default=['raw3', 'raw5'])
    args = parser.parse_args()
    before, after = rows(args.before), rows(args.after)
    if len(before) != len(after):
        raise ValueError('Captures have different input counts')
    payload = {
        'capture_sha256': {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in (args.before, args.after)},
        'runs': args.runs,
        'note': 'Changed scored inputs and every HO30 input. HO30 is the regression '
                'set used for improvement, not an independent holdout.',
        'inputs': detail(before, after, args.runs),
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('inputs:', len(payload['inputs']))


if __name__ == '__main__':
    main()
