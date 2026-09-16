"""Diagnose observed rank failures and bounded, offline alternatives."""
from collections import Counter
from pathlib import Path

from scripts import patch041_retrieval_eval as evaluation

ROOT = evaluation.ROOT
TRACE = ROOT / 'data/eval/patch042-candidate-trace'
OUTPUT = ROOT / 'data/eval/patch042-candidate-diagnosis.json'


def position(values, target):
    return values.index(target) + 1 if target in values else None


def analyze():
    for name, digest in evaluation.read(TRACE / 'manifest.json').items():
        if evaluation.sha(TRACE / name) != digest:
            raise ValueError(f'Trace hash mismatch: {name}')
    evaluation.check(TRACE / 'capture')
    traces = evaluation.read(TRACE / 'traces.json')
    trace_map = {(r['qid'], r['mode']): r for r in traces}
    rows = evaluation.read(TRACE / 'capture/rows.json')
    jobs = evaluation.build_jobs()
    if len(trace_map) != 235 or len(traces) != 235:
        raise ValueError('Incomplete trace identities')
    details, alternatives = [], {}
    for row, job in zip(rows, jobs):
        identity = job['qid'], job['mode']
        if (row['qid'], row['mode']) != identity:
            raise ValueError('Input identity mismatch')
        trace = trace_map[identity]
        if trace['query_sha256'] != job['query_sha256']:
            raise ValueError('Trace query mismatch')
        targets = {evaluation.norm(a) for a in job['targets']}
        general = [evaluation.norm(e['article_id']) for e in row['result']['laws']]
        civil = [evaluation.norm(e['article_id']) for e in row['result']['civil_laws']]
        found = set(general + civil)
        eligible = bool(targets) and not job['historical']
        group = job['mode'] if job['track'] == 'dev100' else job['track']
        for target in sorted(targets - found) if eligible else []:
            channel = 'civil' if target.startswith('민법-') else 'general'
            candidates = trace[channel]
            fused_rank = position(candidates['fused'], target)
            member_ranks = {name: position(values, target) for name, values in candidates['members'].items()}
            if fused_rank is None:
                stage = 'outside_observed_candidates'
            elif channel == 'general':
                stage = 'general_fusion_outside_top5'
            elif fused_rank <= 3:
                stage = 'civil_selection_displaced_top3'
            else:
                stage = 'civil_fusion_outside_top3'
            details.append({
                'qid': job['qid'], 'mode': job['mode'], 'track': job['track'],
                'target': target, 'channel': channel, 'stage': stage,
                'fused_rank': fused_rank, 'member_ranks': member_ranks,
                'civil_seed': trace['civil_seed'] if channel == 'civil' else [],
                'civil_references': trace['civil']['references'] if channel == 'civil' else [],
                'law_concepts': trace['law_concepts'], 'civil_concepts': trace['civil_concepts'],
            })
        variants = {
            'general_top10_same_civil3': (trace['general']['fused'][:10], civil),
            'seedless_tail_override_removed': (
                general, trace['civil']['fused'][:3]
                if not trace['civil_seed'] and not trace['civil']['references'] else civil),
        }
        for name, (laws, civils) in variants.items():
            variant = alternatives.setdefault(name, {'groups': {}, 'changed_inputs': [], 'gained_targets': [], 'lost_targets': []})
            if laws != general or civils != civil:
                variant['changed_inputs'].append({'qid': job['qid'], 'mode': job['mode']})
            if not eligible:
                continue
            after = set(laws + civils)
            counts = variant['groups'].setdefault(group, {'n': 0, 'before_complete': 0, 'after_complete': 0})
            counts['n'] += 1
            counts['before_complete'] += targets <= found
            counts['after_complete'] += targets <= after
            for field, ids in (('gained_targets', (after - found) & targets), ('lost_targets', (found - after) & targets)):
                if ids:
                    variant[field].append({'qid': job['qid'], 'mode': job['mode'], 'targets': sorted(ids)})
    dev = [d for d in details if d['track'] == 'dev100']
    return {
        'source_manifest_sha256': evaluation.sha(TRACE / 'manifest.json'),
        'analysis_source_sha256': evaluation.source_sha(Path(__file__)),
        'unit': 'missing (question, mode, target article); not unique questions',
        'dev_missing_target_instances': len(dev),
        'dev_stages': dict(Counter(d['stage'] for d in dev)),
        'dev_general_fused_ranks_6_to_10': sum(d['channel'] == 'general' and d['fused_rank'] is not None and 6 <= d['fused_rank'] <= 10 for d in dev),
        'dev_missing_with_member_top5': sum(any(rank is not None and rank <= 5 for rank in d['member_ranks'].values()) for d in dev),
        'details': details,
        'offline_alternatives': alternatives,
        'limitations': 'Alternatives recombine observed candidates after seeing DEV results. No production changes or new policy live runs; no LLM or independent holdout evaluation. General top10 returns more evidence and may increase downstream context/token cost, which was not measured; member depth remains20. Gains are not a recommendation to increase budgets.',
    }


if __name__ == '__main__':
    result = analyze()
    evaluation.write(OUTPUT, result)
    print({k: v for k, v in result.items() if k not in ('details', 'offline_alternatives')})
    for name, value in result['offline_alternatives'].items():
        print(name, value['groups'], 'gains', len(value['gained_targets']), 'losses', len(value['lost_targets']))
