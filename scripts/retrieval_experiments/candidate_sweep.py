"""Compare candidate coverage, not final retrieval quality or answer accuracy.

Retrieve each source pool once per query and evaluate predefined budgets offline.
No law-topic rules, gold labels, or reranker are used to build the candidate pools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import sys
import time

os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                  ANONYMIZED_TELEMETRY='False', LANGSMITH_TRACING='false')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dev100_v2.report import DEFAULT_DATASET, load_dataset, read_json, sha256
from scripts.retrieval_experiments.candidate_pipeline import CandidatePipeline
from scripts.retrieval_experiments.run_candidates import fingerprints
from src.retrieval.service import LAW, CIVIL, RetrievalService, route_law_corpus


# Freeze budgets before observing results. Equal-size unified controls are included.
SPLIT_BUDGETS = ((8, 2), (10, 2), (10, 3), (10, 5), (13, 7), (15, 5), (17, 3), (20, 7))
UNIFIED_BUDGETS = (10, 12, 13, 15, 20)


def split_candidates(general, civil, general_k, civil_k):
    """Membership with per-source ranks; concatenation is NOT a final ranking."""
    if general_k < 0 or civil_k < 0:
        raise ValueError('Negative candidate budget')
    selected = [*general[:general_k], *civil[:civil_k]]
    if len({c['id'] for c in selected}) != len(selected):
        raise ValueError('Overlapping source pools')
    return selected


def variants(general, civil, unified):
    pools = {f'split_g{g}_c{c}': split_candidates(general, civil, g, c)
             for g, c in SPLIT_BUDGETS}
    pools.update({f'unified_{k}': unified[:k] for k in UNIFIED_BUDGETS})
    pools['general_only_20'] = general[:20]
    return pools


def coverage(targets, available, candidates, historical=False):
    held = targets & available
    found = {c['article'] for c in candidates}
    eligible = bool(targets) and targets <= available and not historical
    return {'eligible': eligible, 'all_required_found': eligible and targets <= found,
            'held_targets': len(held) if not historical else 0,
            'held_targets_found': len(held & found) if not historical else 0,
            'missing_held_targets': sorted(held - found) if not historical else []}


def summarize(rows, plan, available):
    groups = {}
    for row in rows:
        item = plan[row['qid']]
        targets = {t['article_anchor'] for t in item['law_targets']}
        for name, pool in variants(row['general'], row['civil'], row['unified']).items():
            group = groups.setdefault(row['mode'], {}).setdefault(name, {
                'inputs': 0, 'eligible': 0, 'all_required_found': 0, 'held_targets': 0,
                'held_targets_found': 0, 'sizes': [], 'text_chars': [], 'misses': []})
            result = coverage(targets, available, pool, item['historical_review_required'])
            group['inputs'] += 1
            for field in ('eligible', 'all_required_found', 'held_targets', 'held_targets_found'):
                group[field] += int(result[field])
            group['sizes'].append(len(pool))
            group['text_chars'].append(sum(c['text_chars'] for c in pool))
            if result['eligible'] and not result['all_required_found']:
                group['misses'].append({'qid': row['qid'], 'articles': result['missing_held_targets']})
    for modes in groups.values():
        for group in modes.values():
            sizes, lengths = group.pop('sizes'), group.pop('text_chars')
            group['mean_candidates'] = statistics.mean(sizes)
            group['min_candidates'], group['max_candidates'] = min(sizes), max(sizes)
            group['median_text_chars'] = statistics.median(lengths)
            group['max_text_chars'] = max(lengths)
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--probes', type=Path)
    args = parser.parse_args()
    plan_rows, reference = load_dataset(args.dataset)
    questions = read_json(args.dataset / 'questions.json')
    source, out = args.snapshot.resolve(), args.out.resolve()
    if out.exists() or out.is_relative_to(source) or source.is_relative_to(out):
        parser.error('Use a new output directory outside source and its ancestors')
    before = fingerprints(source)
    if not before:
        parser.error('Empty source snapshot')
    out.mkdir(parents=True)
    protocol = {'split_budgets': SPLIT_BUDGETS, 'unified_budgets': UNIFIED_BUDGETS,
                'hybrid_member_depth': 20, 'inputs': 200, 'final_ranking_evaluated': False,
                'no_reranker_or_generation': True, 'source_hashes': before,
                'questions_sha256': sha256(args.dataset / 'questions.json'),
                'plan_sha256': sha256(args.dataset / 'diagnostic-plan.json'),
                'probe_sha256': sha256(args.probes) if args.probes else None,
                'code_hashes': {p.name: sha256(p) for p in (Path(__file__), Path(__file__).with_name('candidate_pipeline.py'))}}
    (out / 'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copytree(source, out / 'snapshot')
    data = out / 'snapshot/data'
    service = RetrievalService.from_index(
        chunk_paths=tuple(data / 'chunks' / f'{n}.jsonl' for n in ('chunks', 'cases', 'guides')),
        index_path=data / 'index/chroma_kurev1_1024', civil_index_path=data / 'index/chroma_civil_kurev1_1024')
    unified = CandidatePipeline(service)
    inventory = {r['chunk_id']: r for r in reference['inventory']}
    for cid, stored in inventory.items():
        chunk = service._chunks[cid]
        if hashlib.sha256(chunk['text'].encode()).hexdigest() != stored['body_sha256']:
            raise ValueError('Snapshot law body differs from the fixed reference')
    available = {r['article_anchor'] for r in inventory.values()}
    if len(available) != 140:
        raise ValueError('Expected 140 law articles')

    def record(hits, name):
        return [{'id': cid, 'source': name, 'source_rank': i + 1, 'score_in_source': score,
                 'article': service._chunks[cid]['metadata']['article_id'].replace(' ', ''),
                 'text_chars': len(service._chunks[cid]['text'])} for i, (cid, score) in enumerate(hits)]

    def retrieve(query):
        start = time.perf_counter()
        general = record(service._retrievers[LAW.name].search(query, 20, route_law_corpus(query).where()), 'general')
        general_time = time.perf_counter() - start
        start = time.perf_counter()
        civil = record(service._retrievers[CIVIL.name].search(query, 7, service.civil.where()), 'civil')
        civil_time = time.perf_counter() - start
        start = time.perf_counter()
        merged = unified.retrieve(query, 20)
        unified_time = time.perf_counter() - start
        global_pool = [{k: v for k, v in c.items() if k != 'text'} | {'text_chars': len(c['text'])} for c in merged]
        return {'general': general, 'civil': civil, 'unified': global_pool,
                'seconds': {'general': general_time, 'civil': civil_time, 'unified': unified_time}}

    rows = []
    for index, q in enumerate(questions, start=1):
        for mode, value in q['modes'].items():
            row = {'qid': q['qid'], 'mode': mode, **retrieve(value['query'])}
            rows.append(row)
            with (out / 'pools.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        if index % 10 == 0:
            print(f'{index}/100', flush=True)
    if args.probes:
        for p in read_json(args.probes):
            row = {'id': p['id'], **retrieve(p['query'])}
            with (out / 'probe-pools.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    if fingerprints(source) != before:
        raise RuntimeError('Source snapshot changed')
    result = {'purpose': 'candidate coverage only, no final relevance or legal correctness',
              'results': summarize(rows, {p['qid']: p for p in plan_rows}, available),
              'median_seconds': {'split_pair': statistics.median(r['seconds']['general'] + r['seconds']['civil'] for r in rows),
                                 'unified': statistics.median(r['seconds']['unified'] for r in rows)},
              'inputs': len(rows), 'source_unchanged': True, 'independent_evaluation': False}
    (out / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
