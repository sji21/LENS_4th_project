"""Offline, opt-in BGE cross-encoder experiment on frozen candidate captures.

No database writes, generated answers, gold-based scoring, or app integration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time


REVISION = '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rank_scores(candidates, scores):
    if len(scores) != len(candidates) or len({c['id'] for c in candidates}) != len(candidates):
        raise ValueError('Invalid score count or duplicate candidate')
    if any(not math.isfinite(s) for s in scores):
        raise ValueError('Nonfinite model score')
    return sorted([dict(c, rerank_score=s) for c, s in zip(candidates, scores)],
                  key=lambda c: (-c['rerank_score'], c['id']))


def validate_lengths(lengths, limit):
    if not lengths or any(n > limit or n <= 0 for n in lengths):
        raise ValueError('Input exceeds limit; do not silently truncate legal text')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--stage', choices=['probes', 'dev'], required=True)
    args = parser.parse_args()
    source, out, model_path = args.source.resolve(), args.out.resolve(), args.model.resolve()
    if out.exists() or any(out.is_relative_to(p) or p.is_relative_to(out) for p in (source, model_path)):
        parser.error('Use a new output directory outside source/model')
    source_protocol = read(source / 'protocol.json')
    capture_name = 'probe-pools.jsonl' if args.stage == 'probes' else 'pools.jsonl'
    captured = [json.loads(s) for s in (source / capture_name).read_text(encoding='utf-8').splitlines()]
    expected_hashes = read('docs/patch012-candidate-sweep.json')['artifact_hashes']
    if digest(source / capture_name) != expected_hashes[capture_name]:
        raise ValueError('Candidate capture differs from published artifact')
    if args.stage == 'probes':
        query_path = Path('data/eval/civil-candidate-probes.json')
        if digest(query_path) != source_protocol['probe_sha256']:
            raise ValueError('Probe queries changed')
        queries = {p['id']: p['query'] for p in read(query_path)}
        keyed = [(r['id'], r) for r in captured]
    else:
        query_path = Path('data/eval/dev100-v2/questions.json')
        if digest(query_path) != source_protocol['questions_sha256']:
            raise ValueError('Development queries changed')
        queries = {q['qid'] + '/' + mode: v['query'] for q in read(query_path) for mode, v in q['modes'].items()}
        keyed = [(r['qid'] + '/' + r['mode'], r) for r in captured]
    if len(keyed) != len(queries) or {key for key, _ in keyed} != set(queries):
        raise ValueError('Incomplete or duplicate inputs')
    chunks = {}
    chunk_paths = sorted((source / 'snapshot/data/chunks').glob('*.jsonl'))
    chunk_hashes = {p.name: digest(p) for p in chunk_paths}
    for p in chunk_paths:
        for line in p.read_text(encoding='utf-8').splitlines():
            c = json.loads(line)
            chunks[c['chunk_id']] = c
    for item in read('data/eval/dev100-v2/reference-run.json')['inventory']:
        if hashlib.sha256(chunks[item['chunk_id']]['text'].encode()).hexdigest() != item['body_sha256']:
            raise ValueError('Law body differs from reference')
    import torch
    import transformers
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    if not torch.cuda.is_available():
        raise RuntimeError('GPU required for this experiment')
    out.mkdir(parents=True)
    protocol = {'stage': args.stage, 'model': 'BAAI/bge-reranker-v2-m3', 'revision': REVISION,
                'weights_sha256': digest(model_path / 'model.safetensors'),
                'runner_sha256': digest(Path(__file__)), 'query_sha256': digest(query_path),
                'capture_sha256': digest(source / capture_name), 'chunk_hashes': chunk_hashes,
                'torch': torch.__version__, 'transformers': transformers.__version__,
                'gpu': torch.cuda.get_device_name(0), 'precision': 'float16', 'batch_size': 2,
                'max_length': 2048, 'truncation': False, 'candidate_budgets': [[10, 5], [13, 7]],
                'threshold': None, 'ranking_only': True, 'top_k': [3, 5], 'inputs': len(keyed)}
    (out / 'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding='utf-8')
    start = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=False)
    model = AutoModelForSequenceClassification.from_pretrained(model_path, local_files_only=True,
        trust_remote_code=False, torch_dtype=torch.float16).to('cuda').eval()
    load_seconds = time.perf_counter() - start

    def score(query, candidates):
        pairs = [[query, chunks[c['id']]['text']] for c in candidates]
        lengths = [len(tokenizer(q, t, truncation=False)['input_ids']) for q, t in pairs]
        validate_lengths(lengths, 2048)
        scores = []
        with torch.inference_mode():
            for offset in range(0, len(pairs), 2):
                inputs = tokenizer(pairs[offset:offset + 2], padding=True, truncation=False, return_tensors='pt').to('cuda')
                scores.extend(model(**inputs).logits.flatten().float().cpu().tolist())
        return scores, max(lengths)

    # Warm up separately; do not include first-use costs in steady-state timing.
    key, first = keyed[0]
    start = time.perf_counter()
    score(queries[key], first['general'][:1])
    torch.cuda.synchronize()
    warmup_seconds = time.perf_counter() - start
    torch.cuda.reset_peak_memory_stats()
    for index, (key, row) in enumerate(keyed, 1):
        candidates = row['general'][:13] + row['civil'][:7]
        if len({c['id'] for c in candidates}) != len(candidates):
            raise ValueError('Duplicate source candidate')
        start = time.perf_counter()
        scores, maximum = score(queries[key], candidates)
        torch.cuda.synchronize()
        seconds = time.perf_counter() - start
        ranked = rank_scores(candidates, scores)
        smaller = {c['id'] for c in row['general'][:10] + row['civil'][:5]}
        result = {'id': key, 'seconds_20_candidates': seconds, 'max_tokens': maximum,
                  'scores': [{'id': c['id'], 'article': c['article'], 'score': c['rerank_score']} for c in ranked],
                  'outputs': {'g13_c7': [c['article'] for c in ranked[:5]],
                              'g10_c5': [c['article'] for c in ranked if c['id'] in smaller][:5]}}
        with (out / 'results.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + '\n')
        print(f'{index}/{len(keyed)} {seconds:.3f}s', flush=True)
    if {p.name: digest(p) for p in chunk_paths} != chunk_hashes:
        raise RuntimeError('Source law files changed')
    audit = {'completed': len(keyed), 'load_seconds': load_seconds, 'warmup_seconds': warmup_seconds,
             'peak_allocated_bytes': torch.cuda.max_memory_allocated(),
             'peak_reserved_bytes': torch.cuda.max_memory_reserved(), 'chunk_files_unchanged': True}
    (out / 'audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
