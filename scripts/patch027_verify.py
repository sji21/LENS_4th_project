"""Verify the selected product path against all frozen partition traces."""
import argparse
import os
from pathlib import Path
import subprocess

from scripts.patch026_expand import ROOT, read, write, sha, norm, score
from scripts.patch025_ranking import index_digest, close
from scripts.patch027_partition import report, select
from src.retrieval.service import RetrievalService
from src.evaluation.baseline import settings, SEARCH_K


def verify(run, out):
    if out.exists():
        raise ValueError("Use a new output path")
    if subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip():
        raise ValueError("Commit before capture")
    expected = report(run)['pool_equal']
    audit = read(run/'audit.json')
    traces = {(r['qid'], r['mode']): r for r in read(run/'traces.json')}
    old = read(ROOT/'data/eval/patch026-expansion/before.json')
    prior = {(r['qid'], r['mode']): r for r in old}
    svc = RetrievalService.from_index()
    indexes = [index_digest(r) for r in (svc.dense, svc.civil_dense)]
    if indexes != audit['index_semantic_hashes']:
        raise ValueError("Candidate index differs from comparison")
    source_hashes = {p.relative_to(ROOT).as_posix(): sha(p) for directory in ('chunks', 'database')
                     for p in (ROOT/'data'/directory).rglob('*') if p.is_file()}
    rows = []
    for q in read(ROOT/'data/eval/patch015-baseline/capture/results.json'):
        key = q['qid'], q['mode']
        result = svc.search(q['query'], **SEARCH_K)
        def anchor(e):
            return norm(svc._chunks[e.chunk_id]['metadata']['article_id'])
        row = {'qid': q['qid'], 'mode': q['mode'],
               'laws': [anchor(e) for e in result.laws],
               'civil_laws': [anchor(e) for e in result.civil_laws],
               'cases': [e.chunk_id for e in result.cases],
               'guides': [e.chunk_id for e in result.guides]}
        assert row['laws'] == [audit['candidate_anchors'][cid] for cid in select(traces[key], 'pool_equal')], key
        assert all(row[name] == prior[key][name] for name in ('civil_laws', 'cases', 'guides')), key
        rows.append(row)
        if len(rows) % 25 == 0:
            print(f'{len(rows)}/235 product matches', flush=True)
    measured = score(rows, read(ROOT/'data/eval/patch026-expansion/full/audit.json')['available_after'], old)
    assert close(measured, expected), 'Product metrics differ from comparison'
    assert not measured['losses']
    assert indexes == [index_digest(r) for r in (svc.dense, svc.civil_dense)]
    assert all(sha(ROOT/p) == digest for p, digest in source_hashes.items())
    assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
    write(out, {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'clean': True, 'settings': settings(svc), 'rows': rows, 'matches': len(rows),
                'other_channels_preserved': True, 'source_hashes': source_hashes,
                'index_semantic_hashes': indexes, 'trace_sha256': sha(run/'traces.json'),
                'candidate_only': True, 'report': measured})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', LANGSMITH_TRACING='false', ANONYMIZED_TELEMETRY='False')
    verify(args.run, args.out)
