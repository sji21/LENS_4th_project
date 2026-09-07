"""현재 검증 판례를 대상으로 case_id 정답 홀드아웃을 실행한다."""

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
from src.retrieval.retriever import BM25Retriever, load_chunks


def main() -> int:
    parser = argparse.ArgumentParser(description="판례 case_id 홀드아웃 평가(BM25 기준선)")
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--eval-set", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    chunks = [
        chunk for chunk in load_chunks(args.chunks)
        if chunk.get("metadata", {}).get("doc_type") == "case"
    ]
    if not chunks:
        raise ValueError(f"판례 청크가 없습니다: {args.chunks}")
    questions = load_case_holdout(args.eval_set)
    report = evaluate_case_holdout(questions, chunks, BM25Retriever(chunks), args.k)
    report.update(
        {
            "protocol": "current-corpus case_id retrieval holdout; BM25 baseline; no query-label tuning",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "chunks": str(args.chunks),
            "eval_set": str(args.eval_set),
            "retriever": "bm25",
            "k": args.k,
            "chunk_count": len(chunks),
        }
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = report["metrics"]
    print(f"판례 청크: {len(chunks)}건 / 홀드아웃 문항: {report['question_count']}건")
    print(f"Hit@1 / @3 / @5: {metrics['hit_at_1']:.2%} / {metrics['hit_at_3']:.2%} / {metrics['hit_at_5']:.2%}")
    print(f"MRR: {metrics['mrr']:.4f}")
    print(f"보고서: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
