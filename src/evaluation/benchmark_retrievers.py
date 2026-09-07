"""동일 평가셋에서 검색 방식과 코퍼스 보강 효과를 비교한다.

외부 API 없이 재현 가능한 실험만 포함한다. ``bm25-enriched``는 검색 방식의
변경이 아니라 사람이 작성한 쉬운 설명을 코퍼스에 추가한 실험이므로 별도로
해석해야 한다.

실행:
    python -m src.evaluation.benchmark_retrievers
    python -m src.evaluation.benchmark_retrievers --output data/eval/runs/retriever-comparison.json
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

from src.evaluation.metrics import RunResult, hit_at_k
from src.evaluation.run_eval import check_gold_exists, load_questions, run
from src.retrieval.retriever import BM25Retriever, Retriever, TfidfRetriever, load_chunks


@dataclass
class Experiment:
    name: str
    family: str
    corpus: str
    chunks: list[dict]
    retriever: Retriever


def hit_rate_at(result: RunResult, k: int) -> float:
    if not result.results:
        return 0.0
    return sum(
        hit_at_k(item.retrieved_articles, item.gold_articles, k)
        for item in result.results
    ) / result.n


def average_latency_ms(
    retriever: Retriever,
    questions: list[dict],
    *,
    k: int,
    repeats: int,
) -> float:
    answerable = [q for q in questions if q.get("gold_articles")]
    if not answerable or repeats <= 0:
        return 0.0

    for q in answerable:
        retriever.search(q["question"], k)

    started = time.perf_counter()
    for _ in range(repeats):
        for q in answerable:
            retriever.search(q["question"], k)
    elapsed = time.perf_counter() - started
    return elapsed * 1000 / (len(answerable) * repeats)


def result_payload(
    experiment: Experiment,
    result: RunResult,
    latency_ms: float,
) -> dict:
    return {
        "name": experiment.name,
        "family": experiment.family,
        "corpus": experiment.corpus,
        "metrics": {
            "n": result.n,
            "hit@1": round(hit_rate_at(result, 1), 4),
            "hit@3": round(hit_rate_at(result, 3), 4),
            "hit@5": round(hit_rate_at(result, 5), 4),
            "mrr": round(result.mrr, 4),
            "recall@5": round(result.mean_recall, 4),
            "failures": result.failures,
        },
        "average_latency_ms": round(latency_ms, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="검색 방식 성능 비교")
    parser.add_argument("--eval-set", default="data/eval/dev.jsonl")
    parser.add_argument("--raw-chunks", default="data/sample/chunks_mock.jsonl")
    parser.add_argument(
        "--enriched-chunks", default="data/sample/chunks_mock_enriched.jsonl"
    )
    parser.add_argument("--repeats", type=int, default=200)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    questions = load_questions(args.eval_set)
    raw_chunks = load_chunks(args.raw_chunks)
    enriched_chunks = load_chunks(args.enriched_chunks)

    for label, chunks in (("raw", raw_chunks), ("enriched", enriched_chunks)):
        missing = check_gold_exists(questions, chunks)
        if missing:
            raise ValueError(f"{label} 코퍼스에 정답 조문 누락: {missing}")

    experiments = [
        Experiment(
            "bm25-default",
            "bm25",
            "raw",
            raw_chunks,
            BM25Retriever(raw_chunks, k1=1.5, b=0.75, char_ngram=2),
        ),
        Experiment(
            "bm25-b025",
            "bm25",
            "raw",
            raw_chunks,
            BM25Retriever(raw_chunks, k1=1.5, b=0.25, char_ngram=2),
        ),
        Experiment(
            "tfidf-cosine",
            "tfidf",
            "raw",
            raw_chunks,
            TfidfRetriever(raw_chunks, char_ngram=2),
        ),
        Experiment(
            "bm25-enriched",
            "bm25",
            "enriched",
            enriched_chunks,
            BM25Retriever(enriched_chunks, k1=1.5, b=0.25, char_ngram=2),
        ),
        Experiment(
            "tfidf-enriched",
            "tfidf",
            "enriched",
            enriched_chunks,
            TfidfRetriever(enriched_chunks, char_ngram=2),
        ),
    ]

    payloads = []
    print()
    print(f"평가셋: {args.eval_set}  |  전체 {len(questions)}문항")
    print("※ enriched는 사람이 작성한 쉬운 설명을 추가한 코퍼스이므로 raw와 분리 해석")
    print()
    print(
        f"{'실험':<19}{'코퍼스':<11}{'Hit@1':>8}{'Hit@3':>8}"
        f"{'Hit@5':>8}{'MRR':>8}{'Recall@5':>11}{'ms/q':>10}  실패"
    )
    print("-" * 105)

    for experiment in experiments:
        result = run(
            questions,
            experiment.chunks,
            experiment.retriever,
            run_id=experiment.name,
            k=5,
        )
        latency_ms = average_latency_ms(
            experiment.retriever,
            questions,
            k=5,
            repeats=args.repeats,
        )
        payload = result_payload(experiment, result, latency_ms)
        payloads.append(payload)
        metrics = payload["metrics"]
        failures = ",".join(qid.replace("dev-", "") for qid in metrics["failures"])
        print(
            f"{experiment.name:<19}{experiment.corpus:<11}"
            f"{metrics['hit@1']:>8.1%}{metrics['hit@3']:>8.1%}"
            f"{metrics['hit@5']:>8.1%}{metrics['mrr']:>8.3f}"
            f"{metrics['recall@5']:>11.1%}{latency_ms:>10.3f}  {failures or '-'}"
        )

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "eval_set": args.eval_set,
                    "answerable_questions": sum(
                        bool(q.get("gold_articles")) for q in questions
                    ),
                    "experiments": payloads,
                    "caution": (
                        "enriched 코퍼스의 쉬운 설명은 dev 질문을 참고해 작성되어 "
                        "독립 holdout 검증 전에는 일반화 성능으로 간주할 수 없음"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n기록: {output}")


if __name__ == "__main__":
    main()
