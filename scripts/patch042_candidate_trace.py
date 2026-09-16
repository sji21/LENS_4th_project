"""Observe unchanged product searches; keep candidate ranks separate from gold."""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts import patch041_retrieval_eval as evaluation
from src.retrieval.context_policy import (
    _reference_select, civil_concepts, final_law_concepts,
)


def capture(data_root: Path, out: Path) -> None:
    if out.exists():
        raise ValueError('Use a fresh output directory')
    baseline = evaluation.ROOT / 'data/eval/patch041-rebuilt/capture'
    evaluation.check(baseline)
    baseline_rows = evaluation.read(baseline / 'rows.json')
    jobs = evaluation.build_jobs()
    traces = []
    original_prepare = evaluation.prepare_product_service
    driver_before = evaluation.source_sha(Path(__file__))

    def prepare(root):
        service, profile, logical = original_prepare(root)
        active = {}
        anchor = lambda cid: evaluation.norm(service._anchors[cid])

        def observe(hybrid, channel):
            original = hybrid.search_with_member_hits

            def search(*args, **kwargs):
                ranked, members = original(*args, **kwargs)
                scores = {}
                for member in hybrid.members:
                    name = member.name or str(id(member))
                    for rank, cid in enumerate(members[name], 1):
                        scores[cid] = scores.get(cid, 0.0) + member.weight / (hybrid.rrf_k + rank)
                full = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
                if full[:len(ranked)] != ranked:
                    raise ValueError('Observed fusion differs from actual product result')
                active[channel] = {
                    'members': {name: [anchor(cid) for cid in ids] for name, ids in members.items()},
                    'fused': [anchor(cid) for cid, _ in full],
                    'where': kwargs.get('where', args[2] if len(args) > 2 else None),
                }
                if channel == 'civil':
                    picked, references = _reference_select(
                        {'civil_seed': active.pop('_seed_ids', []), 'civil': members},
                        service._anchors, service._reference_graph,
                    )
                    active[channel]['selected'] = [anchor(cid) for cid in picked]
                    active[channel]['references'] = references
                return ranked, members
            hybrid.search_with_member_hits = search

        observe(service._context_law, 'general')
        observe(service._context_civil, 'civil')
        original_seed = service._search_civil

        def seed(*args, **kwargs):
            result = original_seed(*args, **kwargs)
            active['_seed_ids'] = [e.chunk_id for e in result]
            active['civil_seed'] = [anchor(e.chunk_id) for e in result]
            return result
        service._search_civil = seed
        original_search = service.search

        def search(query, **kwargs):
            job = jobs[len(traces)]
            if query != job['query']:
                raise ValueError('Unexpected search input/order')
            active.clear()
            result = original_search(query, **kwargs)
            if set(active) != {'general', 'civil', 'civil_seed'}:
                raise ValueError('Incomplete candidate trace')
            traces.append({
                'qid': job['qid'], 'mode': job['mode'], 'query_sha256': job['query_sha256'],
                'law_concepts': final_law_concepts(query), 'civil_concepts': civil_concepts(query),
                **active,
            })
            return result
        service.search = search
        return service, profile, logical

    evaluation.prepare_product_service = prepare
    try:
        evaluation.capture(data_root, out / 'capture')
    finally:
        evaluation.prepare_product_service = original_prepare
    evaluation.check(out / 'capture')
    current = evaluation.read(out / 'capture/rows.json')
    if len(current) != len(traces) or len(current) != 235:
        raise ValueError('Missing trace rows')
    for before, after, trace in zip(baseline_rows, current, traces):
        for field in ('qid', 'mode', 'query_sha256', 'result'):
            if before[field] != after[field]:
                raise ValueError(f'Observed product output differs: {after["qid"]} {field}')
        if trace['civil']['selected'] != [evaluation.norm(e['article_id']) for e in after['result']['civil_laws']]:
            raise ValueError('Civil selection trace differs from returned evidence')
    if driver_before != evaluation.source_sha(Path(__file__)):
        raise ValueError('Trace collector changed during execution')
    evaluation.write(out / 'traces.json', traces)
    evaluation.write(out / 'provenance.json', {
        'schema': 'patch042-observed-candidates-v1', 'inputs': 235,
        'driver_source_sha256': driver_before,
        'baseline_manifest_sha256': evaluation.sha(baseline / 'manifest.json'),
        'all_235_results_exactly_equal_to_patch041': True,
        'method': 'Observe actual product member hits without changing query, depth, weights or selection; full fusion recomputed and actual prefix checked',
        'candidate_depth': {'general_per_member': 20, 'civil_per_member': 26},
        'limitations': 'Gold not used in search or observation; candidate ranks are diagnostic, not tuned or independent evaluation',
    })
    evaluation.write(out / 'manifest.json', {
        p.relative_to(out).as_posix(): evaluation.sha(p)
        for p in sorted(out.rglob('*')) if p.is_file() and p != out / 'manifest.json'
    })
    print('Observed235 unchanged product searches; exact final evidence match verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    capture(args.data_root, args.out)
