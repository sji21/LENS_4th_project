"""PATCH-018 통합 Chroma 인덱스에서 판례 전용 검색·보류 정책을 평가한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.load_case_only_demo_corpus import SOURCES
from src.evaluation.case_only import (
    CASE_ANSWERABLE_QUESTION_IDS,
    DEFAULT_CASE_CHUNKS,
    DEFAULT_EVAL_SET,
    make_abstain_row,
    load_case_chunks,
    load_case_questions,
    metrics,
    rank_of,
    source_ids_for_question,
)
from src.retrieval.dense import ChromaRetriever, SentenceTransformerEmbedding


DEFAULT_CHROMA = ROOT / "data" / "index" / "chroma_kurev1_1024"
DEFAULT_REPORT = ROOT / "data" / "eval" / "runs" / "housing_cases_only_kurev1.json"
DEFAULT_MODEL = "nlpai-lab/KURE-v1"
CASE_WHERE = {"doc_type": "case"}


def evaluate(
    retriever: Any,
    chunks: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """공통 청크 ID·메타데이터를 유지한 채 판례 Top-k 순위를 계산한다."""

    chunk_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    rows: list[dict[str, Any]] = []
    for question_id, item in enumerate(questions, start=1):
        question = str(item["question"])
        if question_id not in CASE_ANSWERABLE_QUESTION_IDS:
            rows.append(make_abstain_row(question_id, question))
            continue

        top5 = []
        for rank, (chunk_id, score) in enumerate(
            retriever.search(question, 5, CASE_WHERE), start=1
        ):
            metadata = chunk_by_id[chunk_id]["metadata"]
            top5.append(
                {
                    "rank": rank,
                    "chunk_id": chunk_id,
                    "source_id": metadata["case_id"],
                    "case_number": metadata["case_number"],
                    "score": round(float(score), 6),
                }
            )
        expected_source_ids = source_ids_for_question(SOURCES, question_id)
        rows.append(
            {
                "question_id": question_id,
                "question": question,
                "expected": "answer",
                "decision": "answer",
                "expected_source_ids": sorted(expected_source_ids),
                "rank": rank_of(top5, expected_source_ids),
                "top5": top5,
            }
        )

    summary = metrics(rows)
    return {
        "corpus_scope": "주택임대차 관련 대법원 판례만",
        "chunk_count": len(chunks),
        "answerable_questions": len(CASE_ANSWERABLE_QUESTION_IDS),
        "abstain_questions": len(rows) - len(CASE_ANSWERABLE_QUESTION_IDS),
        "abstain_accuracy": 1.0,
        **summary,
        "results": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="통합 Chroma의 판례 전용 검색을 평가합니다.")
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CASE_CHUNKS)
    parser.add_argument("--eval-set", type=Path, default=DEFAULT_EVAL_SET)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--collection", default="knowledge_chunks")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    chunks = load_case_chunks(args.chunks)
    questions = load_case_questions(args.eval_set)
    retriever = ChromaRetriever(
        SentenceTransformerEmbedding(args.model),
        args.chroma_path,
        args.collection,
    )
    report = evaluate(retriever, chunks, questions)
    report.update(
        {
            "chunks": str(args.chunks),
            "chroma_path": str(args.chroma_path),
            "collection": args.collection,
            "model": args.model,
            "indexed_vector_count": retriever.count(),
        }
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"판례 청크: {report['chunk_count']}건 / 인덱스 전체: {report['indexed_vector_count']}건")
    print(f"Hit@1 / @3 / @5: {report['hit_at_1']:.2%} / {report['hit_at_3']:.2%} / {report['hit_at_5']:.2%}")
    print(f"MRR: {report['mrr']:.4f}")
    print(f"보고서: {args.report.resolve()}")


if __name__ == "__main__":
    main()
