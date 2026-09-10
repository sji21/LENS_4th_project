"""Run opt-in candidate experiments on copied indexes; never change app defaults."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                  ANONYMIZED_TELEMETRY='False', LANGSMITH_TRACING='false')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.retrieval_experiments.candidate_pipeline import CandidatePipeline, rerank_local, SYSTEM_PROMPT
from src.retrieval.service import RetrievalService


def fingerprints(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--queries', required=True, type=Path, help='JSON array with unique id and query strings')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--rerank', action='store_true', help='Call the installed local Qwen model; slow and experimental')
    args = parser.parse_args()
    queries = json.loads(args.queries.read_text(encoding='utf-8'))
    if not queries or any(not isinstance(q.get('id'), str) or not isinstance(q.get('query'), str) or not q['query'].strip() for q in queries):
        parser.error('Expected nonempty id/query entries')
    if len({q['id'] for q in queries}) != len(queries):
        parser.error('Duplicate query IDs')
    source, out = args.snapshot.resolve(), args.out.resolve()
    if out.is_relative_to(source) or source.is_relative_to(out):
        parser.error('Output must be outside source and its ancestors')
    if out.exists():
        parser.error('Use a new output directory')
    before = fingerprints(source)
    if not before:
        parser.error('Empty snapshot')
    out.mkdir(parents=True)
    shutil.copytree(source, out / 'snapshot')
    manifest = {'source_hashes': before, 'queries_sha256': hashlib.sha256(args.queries.read_bytes()).hexdigest(),
                'candidate_k': 12, 'final_k': 5, 'rerank': args.rerank,
                'prompt_sha256': hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
                'pipeline_sha256': hashlib.sha256(Path(__file__).with_name('candidate_pipeline.py').read_bytes()).hexdigest(),
                'purpose': 'development experiment, not production or independent evaluation'}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    data = out / 'snapshot/data'
    service = RetrievalService.from_index(
        chunk_paths=tuple(data / 'chunks' / f'{n}.jsonl' for n in ('chunks', 'cases', 'guides')),
        index_path=data / 'index/chroma_kurev1_1024', civil_index_path=data / 'index/chroma_civil_kurev1_1024')
    pipeline = CandidatePipeline(service)
    errors = 0
    for index, q in enumerate(queries, start=1):
        start = time.monotonic()
        row = {'id': q['id'], 'query': q['query'], 'candidates': pipeline.retrieve(q['query'])}
        if args.rerank:
            try:
                row['rerank'] = rerank_local(q['query'], row['candidates'])
            except (ValueError, OSError, KeyError, TypeError) as error:
                row['error'] = str(error)
                errors += 1
        row['seconds'] = time.monotonic() - start
        with (out / 'results.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(f'{index}/{len(queries)}', flush=True)
    if fingerprints(source) != before:
        raise RuntimeError('Source snapshot changed during experiment')
    (out / 'audit.json').write_text(json.dumps({'inputs': len(queries), 'errors': errors,
        'source_unchanged': True, 'completed': True}), encoding='utf-8')
    if errors:
        raise SystemExit('Experiment contains reranking errors; do not treat them as successful abstentions')


if __name__ == '__main__':
    main()
