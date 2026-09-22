"""Compare immutable baseline and regression captures without running retrieval."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.patch057_comprehensive_eval import evidence_identity


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def found_labels(row, mode, channel):
    found = {evidence_identity(e, channel) for e in row['runs'][mode]['result'].get(channel, [])}
    return {t['label'] for t in row['job']['targets'][channel] if t['identity'] in found}


def compare(before, after):
    assert len(before) == len(after) == 368
    changes, summary, ho = [], {}, []
    for old, new in zip(before, after):
        job = new['job']
        assert (old['job']['qid'], old['job']['mode'], old['job']['query']) == (job['qid'], job['mode'], job['query'])
        for channel in job['targets']:
            assert [t['label'] for t in old['job']['targets'][channel]] == [t['label'] for t in job['targets'][channel]]
        if not job['score']:
            continue
        detail = {'qid': job['qid'], 'group': job['group'], 'mode': job['mode'], 'question': job['query'], 'runs': {}}
        for mode in ('raw3', 'raw5', 'staged_operating', 'staged_diagnostic'):
            wanted, old_found, new_found = set(), set(), set()
            channels = {}
            for channel, targets in job['targets'].items():
                target = {t['label'] for t in targets}
                was, now = found_labels(old, mode, channel), found_labels(new, mode, channel)
                wanted |= target; old_found |= was; new_found |= now
                if target:
                    channels[channel] = {'targets': sorted(target), 'before_found': sorted(was),
                                         'after_found': sorted(now), 'lost': sorted(was-now), 'gained': sorted(now-was)}
            key = f"{job['group']}:{mode}"
            bucket = summary.setdefault(key, {'n': 0, 'before_complete': 0, 'after_complete': 0,
                                              'loss_inputs': 0, 'gain_inputs': 0})
            bucket['n'] += 1
            bucket['before_complete'] += wanted <= old_found
            bucket['after_complete'] += wanted <= new_found
            bucket['loss_inputs'] += bool(old_found - new_found)
            bucket['gain_inputs'] += bool(new_found - old_found)
            record = {'before_complete': wanted <= old_found, 'after_complete': wanted <= new_found,
                      'missing': sorted(wanted-new_found), 'channels': channels}
            detail['runs'][mode] = record
            if old_found != new_found:
                changes.append({'qid': job['qid'], 'group': job['group'], 'input_mode': job['mode'], 'run': mode, **record})
        if job['suite'] == 'holdout30':
            ho.append(detail)
    assert len(ho) == 30
    return {'summary': summary, 'changes': changes, 'ho30': ho,
            'note': 'HO30 used for improvement is regression, not independent holdout. Case all-complete is labelled-case-set coverage, not legal necessity.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = compare(rows(args.before), rows(args.after))
    result['capture_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.before,args.after)}
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for key,value in result['summary'].items():
        if ':raw' in key: print(key, value)


if __name__ == '__main__':
    main()
