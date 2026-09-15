"""Question-only direct case baseline; never loads Gold or generation code."""
import argparse
import hashlib
import json
import os
import platform
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from threading import local

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def baseline(chunks, dense, profile):
    from src.retrieval.case_profile import CaseCorpusRetrievalService
    from src.retrieval.service import CASE
    from src.retrieval.retriever import BM25Retriever
    from src.retrieval.hybrid import HybridRetriever, Member
    from src.retrieval.terms import expand_civil
    # Construct only the case boundary. The original _search_one implementation
    # is called unchanged, with the original case corpus and member parameters.
    service = CaseCorpusRetrievalService.__new__(CaseCorpusRetrievalService)
    case = replace(CASE, query_expander=expand_civil, bm25_weight=2, dense_weight=1)
    service.corpora = (None, case)
    service._chunks = {c['chunk_id']: c for c in chunks}
    service.case_profile = profile
    service._request_trace = local()
    service._retrievers = {case.name: HybridRetriever([
        Member(BM25Retriever(chunks, b=case.bm25_b, query_expander=case.query_expander),
               '판례-bm25', case.bm25_weight, case.expand_weight),
        Member(dense, '판례-dense', case.dense_weight, 0.0)], rrf_k=60)}
    return service


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', type=Path, required=True)
    ap.add_argument('--profile', type=Path, required=True)
    ap.add_argument('--questions', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--k', type=int, default=2)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      ANONYMIZED_TELEMETRY='False', TOKENIZERS_PARALLELISM='false',
                      LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
    write(args.output/'INPUT_SEAL.json', {
        'questions': str(args.questions), 'questions_sha256': sha(args.questions),
        'profile_sha256': sha(args.profile), 'chunks_sha256': sha(args.data/'chunks/cases.jsonl'),
        'runner_sha256': sha(__file__), 'k': args.k, 'gold_loaded': False,
        'case_code': {p.name: sha(p) for p in (Path(__file__).resolve().parents[1]/'src/retrieval').glob('*.py')}})
    try:
        import torch
        import psutil
        from src.retrieval.dense import SentenceTransformerEmbedding, ChromaRetriever
        from src.retrieval.retriever import load_chunks
        torch.set_num_threads(min(8, os.cpu_count() or 1))
        start = time.perf_counter()
        profile = json.loads(args.profile.read_text('utf-8-sig'))
        chunks = load_chunks(args.data/'chunks/cases.jsonl')
        backend = SentenceTransformerEmbedding(profile['model_id'], revision=profile['model_revision'])
        dense = ChromaRetriever(backend, args.data/'index/chroma_kurev1_1024')
        service = baseline(chunks, dense, profile)
        write(args.output/'runtime.json', {'python': sys.version, 'platform': platform.platform(),
            'torch': torch.__version__, 'device': str(backend._model.device),
            'cuda_available': torch.cuda.is_available(), 'threads': torch.get_num_threads(),
            'init_seconds': time.perf_counter()-start, 'model_revision': profile['model_revision'],
            'case_chunks': len(chunks), 'index_records': dense.collection.count(),
            'entrypoint': 'CaseCorpusRetrievalService._search_one(CASE, query, k)'})
        questions = json.loads(args.questions.read_text('utf-8-sig'))
        with (args.output/'results.jsonl').open('x', encoding='utf-8') as stream:
            for i, item in enumerate(questions, 1):
                query = item.get('query', item.get('question', ''))
                start = time.perf_counter()
                service._request_trace.case = {}
                row = {'qid': item['qid'], 'query': query, 'k': args.k, 'error': None}
                try:
                    evidence = service._search_one(service.corpora[1], query, args.k) if query.strip() else []
                    row['cases'] = [dict(asdict(e), canonical_case_key=service._chunks[e.chunk_id]['metadata'].get('canonical_case_key')) for e in evidence]
                    row['trace'] = service._request_trace.case
                except Exception as error:
                    row.update(cases=[], error={'type': type(error).__name__, 'message': str(error)})
                row.update(latency_seconds=time.perf_counter()-start,
                           rss_bytes=psutil.Process().memory_info().rss)
                stream.write(json.dumps(row, ensure_ascii=False)+'\n')
                stream.flush()
                if i % 10 == 0:
                    print(json.dumps({'processed': i, 'output': str(args.output)}), flush=True)
    except BaseException as error:
        write(args.output/'failure.json', {'type': type(error).__name__, 'message': str(error)})
        raise
    finally:
        write(args.output/'RESULT_SEAL.json', {p.name: sha(p) for p in args.output.iterdir() if p.is_file() and p.name != 'RESULT_SEAL.json'})


if __name__ == '__main__':
    main()
