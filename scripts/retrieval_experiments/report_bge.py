"""Score completed BGE experiments against unchanged public development targets."""
import argparse
import json
import math
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.dev100_v2.report import DEFAULT_DATASET, load_dataset, sha256


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def completed(path, count):
    rows = [json.loads(line) for line in (path / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
    if len(rows) != count or len({r['id'] for r in rows}) != count or read(path / 'audit.json')['completed'] != count:
        raise ValueError('Incomplete result set')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dev', required=True, type=Path)
    parser.add_argument('--probes', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Use a new output file')
    plans, reference = load_dataset(DEFAULT_DATASET)
    plan = {p['qid']: p for p in plans}
    available = {r['article_anchor'] for r in reference['inventory']}
    dev, probes = completed(args.dev, 200), completed(args.probes, 24)
    questions = read(DEFAULT_DATASET / 'questions.json')
    if {r['id'] for r in dev} != {q['qid'] + '/' + m for q in questions for m in q['modes']}:
        raise ValueError('Wrong evaluation IDs')
    probe_path = Path('data/eval/civil-candidate-probes.json')
    gold_probes = {p['id']: p for p in read(probe_path)}
    if {r['id'] for r in probes} != set(gold_probes):
        raise ValueError('Wrong probe IDs')
    protocols = {name: read(path / 'protocol.json') for name, path in [('dev', args.dev), ('probes', args.probes)]}
    for field in ['weights_sha256', 'runner_sha256', 'precision', 'batch_size', 'max_length', 'revision']:
        if protocols['dev'][field] != protocols['probes'][field]:
            raise ValueError('Different run conditions')
    if protocols['dev']['query_sha256'] != sha256(DEFAULT_DATASET / 'questions.json') or protocols['probes']['query_sha256'] != sha256(probe_path):
        raise ValueError('Queries changed since execution')
    groups = {}
    for row in dev:
        qid, mode = row['id'].split('/')
        p = plan[qid]
        targets = {t['article_anchor'] for t in p['law_targets']}
        if not targets or not targets <= available or p['historical_review_required']:
            continue
        for name, order in row['outputs'].items():
            g = groups.setdefault(mode, {}).setdefault(name, {'eligible': 0, 'top3': 0, 'top5': 0, 'top5_misses': []})
            g['eligible'] += 1
            for k in (3, 5):
                g[f'top{k}'] += int(targets <= set(order[:k]))
            if not targets <= set(order[:5]):
                g['top5_misses'].append(qid)
    probe_counts = {}
    for row in probes:
        p = gold_probes[row['id']]
        for name, order in row['outputs'].items():
            g = probe_counts.setdefault(name, {'forbid_total': 0, 'forbid_top3': 0, 'forbid_top5': 0,
                'positive_total': 0, 'positive_top3': 0, 'positive_top5': 0, 'forbidden_ids': []})
            if 'forbid' in p:
                g['forbid_total'] += 1
                for k in (3, 5):
                    g[f'forbid_top{k}'] += int(p['forbid'] in order[:k])
                if p['forbid'] in order[:5]:
                    g['forbidden_ids'].append(row['id'])
            targets = set(p.get('targets', []))
            if 'require' in p:
                targets.add(p['require'])
            if targets:
                g['positive_total'] += 1
                for k in (3, 5):
                    g[f'positive_top{k}'] += int(targets <= set(order[:k]))
    times = sorted(r['seconds_20_candidates'] for r in dev)
    report = {'purpose': 'development reranking only; no production or independent evaluation',
        'results': groups, 'probes': probe_counts,
        'saved_baseline': read('docs/patch012-common-ranking.json')['saved_baseline'],
        'timing': {'median_seconds_20': statistics.median(times), 'p95_seconds_20': times[math.ceil(.95 * len(times)) - 1],
                   'max_seconds_20': max(times), 'includes_retrieval_or_answer': False},
        'protocols': protocols, 'audits': {name: read(path / 'audit.json') for name, path in [('dev', args.dev), ('probes', args.probes)]},
        'artifacts': {name: {f: sha256(path / f) for f in ['results.jsonl', 'audit.json', 'protocol.json']}
                      for name, path in [('dev', args.dev), ('probes', args.probes)]},
        'plan_sha256': sha256(DEFAULT_DATASET / 'diagnostic-plan.json'),
        'probe_outputs': probes,
        'dev_outputs': [{'id': r['id'], 'outputs': r['outputs']} for r in dev]}
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ['results', 'probes', 'timing', 'audits']}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
