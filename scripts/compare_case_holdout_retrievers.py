"""판례 case_id 홀드아웃에서 BM25, 임베딩, 하이브리드를 같은 조건으로 비교한다."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.case_holdout import evaluate_case_holdout, load_case_holdout
from src.retrieval.dense import ChromaRetriever, SentenceTransformerEmbedding
from src.retrieval.hybrid import HybridRetriever, Member
from src.retrieval.retriever import BM25Retriever, load_chunks


def main() -> int:
    parser = argparse.ArgumentParser(description="판례 홀드아웃 검색기 비교")
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--eval-set", type=Path, required=True)
    parser.add_argument("--chroma-path", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--model", default="nlpai-lab/KURE-v1")
    parser.add_argument("--collection", default="knowledge_chunks")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    chunks = [
        chunk for chunk in load_chunks(args.chunks)
        if chunk.get("metadata", {}).get("doc_type") == "case"
    ]
    if not chunks:
        raise ValueError(f"판례 청크가 없습니다: {args.chunks}")
    questions = load_case_holdout(args.eval_set)
    embedding = SentenceTransformerEmbedding(args.model)
    dense = ChromaRetriever(embedding, args.chroma_path, args.collection)
    bm25 = BM25Retriever(chunks)
    retrievers = {
        "bm25": bm25,
        "dense": dense,
        "hybrid_rrf": HybridRetriever(
            [Member(bm25, "bm25"), Member(dense, "dense")],
            rrf_k=60,
            depth=max(20, args.k),
        ),
    }
    results = {
        name: evaluate_case_holdout(questions, chunks, retriever, args.k)
        for name, retriever in retrievers.items()
    }
    payload = {
        "protocol": "fixed 20 current-corpus case_id questions; no query-label tuning",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "chunks": str(args.chunks),
        "eval_set": str(args.eval_set),
        "chunk_count": len(chunks),
        "question_count": len(questions),
        "k": args.k,
        "model": args.model,
        "chroma_path": str(args.chroma_path),
        "collection": args.collection,
        "indexed_vector_count": dense.count(),
        "retrievers": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, report in results.items():
        metrics = report["metrics"]
        print(
            f"{name}: Hit@1 / @3 / @5 = {metrics['hit_at_1']:.2%} / "
            f"{metrics['hit_at_3']:.2%} / {metrics['hit_at_5']:.2%}, MRR={metrics['mrr']:.4f}"
        )
    print(f"보고서: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
