"""서비스 법령 검색의 상위 3건 계약을 같은 조건으로 비교한다.

법령 검색만 평가하므로 평가 질문의 정답 중 현재 법령 코퍼스에 실제로 존재하는
법률·시행령·시행규칙 조문만 분모에 넣는다. 공식 안내 정답까지 법령 Recall 분모에
넣으면 검색기가 찾을 수 없는 자료를 실패로 세게 되므로 지표가 왜곡된다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from src.evaluation.metrics import hit_at_k, recall_at_k, reciprocal_rank
from src.evaluation.run_eval import load_questions
from src.retrieval.dense import ChromaRetriever, SentenceTransformerEmbedding
from src.retrieval.retriever import load_chunks
from src.retrieval.service import (
    DEFAULT_INDEX,
    DEFAULT_MODEL,
    LAW,
    LAW_CHUNKS,
    LAW_TYPES,
    RetrievalService,
)
from src.retrieval.terms import expand, expand_law

EVAL_SET = Path("data/eval/dev.jsonl")
HOLDOUT_SET = Path("data/eval/holdout.jsonl")
OUT = Path("data/eval/runs/law-top3-comparison.json")


def law_questions(questions: list[dict], chunks: list[dict]) -> list[dict]:
    """법령 코퍼스에 존재하는 정답만 남기고, 법령 정답이 없는 질문은 제외한다."""
    available = {
        chunk["metadata"].get("article_id")
        for chunk in chunks
        if chunk["metadata"].get("doc_type") in LAW_TYPES
    }
    filtered: list[dict] = []
    for question in questions:
        gold = [article for article in question.get("gold_articles", []) if article in available]
        if gold:
            filtered.append({**question, "gold_articles": gold})
    return filtered


def evaluate(
    service: RetrievalService,
    questions: list[dict],
    article_by_chunk: dict[str, str],
) -> dict:
    rows: dict[str, dict] = {}
    retrieved: dict[str, list[str]] = {}
    for question in questions:
        result = service.search(question["question"], k_law=10, k_case=0, k_guide=0)
        articles: list[str] = []
        for evidence in result.laws:
            article = article_by_chunk[evidence.chunk_id]
            if article not in articles:
                articles.append(article)
        gold = question["gold_articles"]
        retrieved[question["qid"]] = articles
        rows[question["qid"]] = {
            "gold": gold,
            "gold_ranks": {
                article: (articles.index(article) + 1 if article in articles else 0)
                for article in gold
            },
        }

    n = len(rows)
    gold = {qid: row["gold"] for qid, row in rows.items()}
    mean = lambda values: sum(values) / n if n else 0.0
    return {
        "n": n,
        "hit@1": mean(hit_at_k(retrieved[qid], gold[qid], 1) for qid in rows),
        "hit@3": mean(hit_at_k(retrieved[qid], gold[qid], 3) for qid in rows),
        "hit@5": mean(hit_at_k(retrieved[qid], gold[qid], 5) for qid in rows),
        "recall@3": mean(recall_at_k(retrieved[qid], gold[qid], 3) for qid in rows),
        "recall@5": mean(recall_at_k(retrieved[qid], gold[qid], 5) for qid in rows),
        "mrr": mean(reciprocal_rank(retrieved[qid], gold[qid]) for qid in rows),
        "questions": rows,
    }


def context_rule_activations(questions: list[dict]) -> list[str]:
    """공통 확장과 법령 확장이 달라지는 질문 ID를 진단한다."""
    return [
        question["qid"]
        for question in questions
        if expand_law(question["question"]) != expand(question["question"])
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=Path, default=LAW_CHUNKS)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--eval-set", type=Path, default=EVAL_SET)
    parser.add_argument("--holdout-set", type=Path, default=HOLDOUT_SET)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    chunks = load_chunks(args.chunks)
    questions = law_questions(load_questions(args.eval_set), chunks)
    holdout_questions = law_questions(load_questions(args.holdout_set), chunks)
    dense = ChromaRetriever(SentenceTransformerEmbedding(args.model), args.index)
    configs = {
        "rrf_k=60, shared expansion": replace(LAW, rrf_k=60, query_expander=expand),
        "rrf_k=5, shared expansion": replace(LAW, rrf_k=5, query_expander=expand),
        "rrf_k=5, law context expansion": LAW,
    }
    article_by_chunk = {
        chunk["chunk_id"]: chunk["metadata"]["article_id"] for chunk in chunks
    }
    results = {
        name: evaluate(
            RetrievalService(chunks, dense, law=corpus),
            questions,
            article_by_chunk,
        )
        for name, corpus in configs.items()
    }

    print(f"법령 정답이 있는 질문: {len(questions)}")
    print(f"{'설정':<38}{'Hit@1':>8}{'Hit@3':>8}{'Recall@3':>10}{'Hit@5':>8}")
    for name, result in results.items():
        print(
            f"{name:<38}{result['hit@1']:>8.1%}{result['hit@3']:>8.1%}"
            f"{result['recall@3']:>10.1%}{result['hit@5']:>8.1%}"
        )

    observed = questions + holdout_questions
    activations = context_rule_activations(observed)
    print(f"문맥 규칙 발동: {len(activations)}/{len(observed)} ({', '.join(activations) or '없음'})")

    payload = {
        "chunks": str(args.chunks),
        "index": str(args.index),
        "eval_set": str(args.eval_set),
        "holdout_set": str(args.holdout_set),
        "gold_scope": "law/decree/rule article IDs present in the evaluated chunks",
        "context_rule_activation": {
            "scope": "law-answerable dev + published holdout questions; diagnostic only",
            "n": len(observed),
            "qids": activations,
        },
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기록: {args.out}")


if __name__ == "__main__":
    main()
