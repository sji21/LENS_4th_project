"""임베딩 모델 비교.

같은 코퍼스·같은 평가셋·같은 채점 코드에 임베딩만 바꿔 끼운다.
BM25 를 함께 실행해 어휘 기반 대비 얼마나 다른지도 같이 본다.

실행:
    python -m src.evaluation.compare_embeddings                      # OpenAI 만
    python -m src.evaluation.compare_embeddings --local KURE bge     # 로컬까지
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from src.evaluation.metrics import hit_at_k, reciprocal_rank
from src.evaluation.run_eval import load_questions
from src.retrieval.retriever import BM25Retriever, chunk_to_article, load_chunks

CHUNKS = "data/sample/chunks_expanded.jsonl"
EVAL_SET = "data/eval/dev.jsonl"
OUT = Path("data/eval/runs/embedding-comparison.json")

SANG = ["상가건물 임대차보호법", "상가건물 임대차보호법 시행령"]
WHERE = {"title": {"$nin": SANG}}

LOCAL_MODELS = {
    "KURE": "nlpai-lab/KURE-v1",
    "bge": "BAAI/bge-m3",
}


def evaluate(retriever, chunks, questions, expand_weight: float) -> dict:
    id_to_article = chunk_to_article(chunks)
    ranks: dict[str, int] = {}
    started = time.perf_counter()

    for q in questions:
        articles: list[str] = []
        for chunk_id, _ in retriever.search(q["question"], 5, WHERE, expand_weight):
            article = id_to_article[chunk_id]
            if article not in articles:
                articles.append(article)
        gold = set(q["gold_articles"])
        ranks[q["qid"]] = next(
            (i for i, a in enumerate(articles, 1) if a in gold), 0
        )

    elapsed = time.perf_counter() - started
    n = len(questions)
    hits = lambda k: sum(1 for r in ranks.values() if r and r <= k) / n

    return {
        "hit@1": hits(1),
        "hit@3": hits(3),
        "hit@5": hits(5),
        "mrr": sum(1 / r for r in ranks.values() if r) / n,
        "failures": sorted(q for q, r in ranks.items() if r == 0),
        "ranks": ranks,
        "query_seconds": round(elapsed / n, 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default=CHUNKS)
    ap.add_argument("--eval-set", default=EVAL_SET)
    ap.add_argument("--local", nargs="*", default=[],
                    choices=sorted(LOCAL_MODELS), help="함께 비교할 로컬 모델")
    ap.add_argument("--no-openai", action="store_true")
    args = ap.parse_args()

    chunks = load_chunks(args.chunks)
    questions = [q for q in load_questions(args.eval_set) if q.get("gold_articles")]
    print(f"\n  코퍼스 {len(chunks)}청크 · 채점 {len(questions)}문항\n")

    results: dict[str, dict] = {}

    print("  BM25 …")
    results["BM25 (b=0.25, 사전)"] = evaluate(
        BM25Retriever(chunks, b=0.25), chunks, questions, 1.0
    )

    if not args.no_openai:
        from src.retrieval.dense import DenseRetriever, OpenAIEmbedding

        print("  text-embedding-3-small …")
        backend = OpenAIEmbedding()
        retriever = DenseRetriever(chunks, backend)
        results["text-embedding-3-small"] = evaluate(retriever, chunks, questions, 0.0)

    for key in args.local:
        from src.retrieval.dense import DenseRetriever, SentenceTransformerEmbedding

        model_id = LOCAL_MODELS[key]
        print(f"  {model_id} …")
        backend = SentenceTransformerEmbedding(model_id)
        retriever = DenseRetriever(chunks, backend)
        results[model_id] = evaluate(retriever, chunks, questions, 0.0)

    print()
    print(f"  {'검색 방식':<26}{'Hit@1':>8}{'Hit@3':>8}{'Hit@5':>8}{'MRR':>8}{'질의(초)':>10}")
    print("  " + "-" * 68)
    for name, r in results.items():
        print(f"  {name:<26}{r['hit@1']:>7.1%}{r['hit@3']:>8.1%}{r['hit@5']:>8.1%}"
              f"{r['mrr']:>8.3f}{r['query_seconds']:>10.3f}")

    print()
    for name, r in results.items():
        marks = ", ".join(x.replace("dev-", "") for x in r["failures"]) or "없음"
        print(f"  {name} 실패: {marks}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"chunks": args.chunks, "eval_set": args.eval_set,
                    "n": len(questions), "results": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  기록: {OUT}\n")


if __name__ == "__main__":
    main()
