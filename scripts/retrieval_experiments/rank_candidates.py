"""Development-only common-corpus ranking and member-level omission trace."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.retrieval_experiments.candidate_sweep import coverage, read_json, sha256, DEFAULT_DATASET, load_dataset
from scripts.retrieval_experiments.candidate_pipeline import CandidatePipeline
from scripts.retrieval_experiments.run_candidates import fingerprints
from src.retrieval.hybrid import HybridRetriever
from src.retrieval.service import RetrievalService, LAW, CIVIL_TITLE, route_law_corpus


def restrict_ranking(candidates, ranked_ids):
    """Restrict a common-corpus ranking, never compare source-local scores."""
    ids = [c['id'] for c in candidates]
    if len(ids) != len(set(ids)) or len(ranked_ids) != len(set(ranked_ids)):
        raise ValueError('Duplicate candidate or ranking ID')
    positions = {cid: i for i, cid in enumerate(ranked_ids)}
    if not set(ids) <= positions.keys():
        raise ValueError('Ranking does not cover candidate pool')
    return sorted(candidates, key=lambda c: positions[c['id']])


def fused_ids(member_hits, weights, rrf_k):
    scores = {}
    for hits, weight in zip(member_hits, weights, strict=True):
        for rank, (cid, _) in enumerate(hits, 1):
            scores[cid] = scores.get(cid, 0) + weight / (rrf_k + rank)
    return sorted(scores, key=lambda cid: (-scores[cid], cid))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Completed candidate sweep directory')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    source, out = args.source.resolve(), args.out.resolve()
    if out.exists() or out.is_relative_to(source) or source.is_relative_to(out):
        parser.error('Use a new output directory outside source')
    plans, reference = load_dataset(DEFAULT_DATASET)
    plan = {p['qid']: p for p in plans}
    questions = read_json(DEFAULT_DATASET / 'questions.json')
    queries = {(q['qid'], mode): value['query'] for q in questions for mode, value in q['modes'].items()}
    probes = read_json(Path('data/eval/civil-candidate-probes.json'))
    pools = [json.loads(line) for line in (source / 'pools.jsonl').read_text(encoding='utf-8').splitlines()]
    if {(r['qid'], r['mode']) for r in pools} != set(queries) or len(pools) != 200:
        raise ValueError('Expected complete 200-input pool')
    old_protocol = read_json(source / 'protocol.json')
    for field, path in [('questions_sha256', DEFAULT_DATASET / 'questions.json'),
                        ('plan_sha256', DEFAULT_DATASET / 'diagnostic-plan.json'),
                        ('probe_sha256', Path('data/eval/civil-candidate-probes.json'))]:
        if old_protocol[field] != sha256(path):
            raise ValueError('Input changed since candidate capture')
    before = fingerprints(source / 'snapshot')
    out.mkdir(parents=True)
    protocol = {'pool_sha256': sha256(source / 'pools.jsonl'), 'source_hashes': before,
                'candidate_budgets': [[10, 5], [13, 7]],
                'ranking_methods': ['dense', 'bm25', 'rrf5', 'rrf60'],
                'ranking_member_depth': 140, 'top_k': [3, 5],
                'gold_used_for_ranking': False, 'generation': False,
                'input_protocol': old_protocol, 'runner_sha256': sha256(Path(__file__))}
    (out / 'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copytree(source / 'snapshot', out / 'snapshot')
    data = out / 'snapshot/data'
    service = RetrievalService.from_index(
        chunk_paths=tuple(data / 'chunks' / f'{n}.jsonl' for n in ('chunks', 'cases', 'guides')),
        index_path=data / 'index/chroma_kurev1_1024', civil_index_path=data / 'index/chroma_civil_kurev1_1024')
    pipeline = CandidatePipeline(service)
    available = {r['article_anchor'] for r in reference['inventory']}
    import hashlib
    for item in reference['inventory']:
        if hashlib.sha256(service._chunks[item['chunk_id']]['text'].encode()).hexdigest() != item['body_sha256']:
            raise ValueError('Law body differs from reference')

    def common_rankings(query):
        route = route_law_corpus(query)
        where = replace(route, exclude_titles=tuple(t for t in route.exclude_titles if t != CIVIL_TITLE)).where()
        members = pipeline.retriever.members
        hits = [HybridRetriever._ask(member, query, 140, where) for member in members]
        # BM25 omits zero-score documents. Keep them last, tied by ID, only
        # for its ranking-only control; do not give them artificial RRF votes.
        lexical = [cid for cid, _ in hits[0]]
        lexical += sorted({cid for cid, _ in hits[1]} - set(lexical))
        return {'bm25': lexical, 'dense': [cid for cid, _ in hits[1]],
                'rrf5': fused_ids(hits, [m.weight for m in members], 5),
                'rrf60': fused_ids(hits, [m.weight for m in members], 60)}

    results = {}
    for index, row in enumerate(pools, 1):
        query = queries[row['qid'], row['mode']]
        start = time.perf_counter()
        ranks = common_rankings(query)
        outputs = {}
        item = plan[row['qid']]
        targets = {t['article_anchor'] for t in item['law_targets']}
        for g, c in ((10, 5), (13, 7)):
            candidates = row['general'][:g] + row['civil'][:c]
            for method, order in ranks.items():
                selected = restrict_ranking(candidates, order)
                name = f'g{g}_c{c}_{method}'
                outputs[name] = [r['article'] for r in selected[:5]]
                group = results.setdefault(row['mode'], {}).setdefault(name, {'eligible': 0, 'top3': 0, 'top5': 0})
                for k in (3, 5):
                    verdict = coverage(targets, available, selected[:k], item['historical_review_required'])
                    group[f'top{k}'] += int(verdict['all_required_found'])
                group['eligible'] += int(verdict['eligible'])
        record = {'qid': row['qid'], 'mode': row['mode'], 'outputs': outputs, 'seconds': time.perf_counter() - start}
        if row['qid'] == 'DEV-089':
            general = service._retrievers[LAW.name]
            where = route_law_corpus(query).where()
            member_hits = [HybridRetriever._ask(m, query, 140, where) for m in general.members]
            record['omission_trace'] = {'where': where, 'members': {
                m.name: [service._chunks[cid]['metadata']['article_id'].replace(' ', '') for cid, _ in hits]
                for m, hits in zip(general.members, member_hits)},
                'fused_depth20': [service._chunks[cid]['metadata']['article_id'].replace(' ', '')
                                  for cid in fused_ids([h[:20] for h in member_hits], [m.weight for m in general.members], general.rrf_k)],
                'fused_depth140': [service._chunks[cid]['metadata']['article_id'].replace(' ', '')
                                   for cid in fused_ids(member_hits, [m.weight for m in general.members], general.rrf_k)]}
        with (out / 'results.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        if index % 20 == 0:
            print(f'{index}/200', flush=True)
    probe_rows = [json.loads(line) for line in (source / 'probe-pools.jsonl').read_text(encoding='utf-8').splitlines()]
    probe_by_id = {p['id']: p for p in probes}
    if len(probe_rows) != len(probes) or {r['id'] for r in probe_rows} != set(probe_by_id):
        raise ValueError('Incomplete probe capture')
    for row in probe_rows:
        p = probe_by_id[row['id']]
        ranks = common_rankings(p['query'])
        record = {'id': p['id'], 'outputs': {}}
        for g, c in ((10, 5), (13, 7)):
            for method, order in ranks.items():
                selected = restrict_ranking(row['general'][:g] + row['civil'][:c], order)
                articles = [r['article'] for r in selected[:5]]
                record['outputs'][f'g{g}_c{c}_{method}'] = {'articles': articles,
                    'forbidden_returned': p.get('forbid') in articles if 'forbid' in p else None}
        with (out / 'probes.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    if fingerprints(source / 'snapshot') != before:
        raise RuntimeError('Source changed')
    (out / 'summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
