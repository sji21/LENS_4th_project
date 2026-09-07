"""BM25 · 임베딩 · Hybrid 를 같은 평가셋으로 비교한다.

Hit 과 Recall 을 **함께** 보고한다. 생성 모델에 법령 상위 3건을 전달하므로
Recall@3를 주 지표로 삼는다. 정답 조문이 둘 이상인 문항에서 Hit@3만 보면
"정답 중 하나라도 찾았다"가 "필요한 근거를 모두 찾았다"로 읽힐 수 있다.

실행:
    python -m src.evaluation.compare_hybrid                 # 비교표
    python -m src.evaluation.compare_hybrid --sweep         # RRF 파라미터 스윕
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.evaluation.metrics import recall_at_k, standard_error
from src.evaluation.run_eval import load_questions
from src.retrieval.dense import DenseRetriever, SentenceTransformerEmbedding
from src.retrieval.hybrid import DEFAULT_DEPTH, DEFAULT_RRF_K, HybridRetriever, Member
from src.retrieval.retriever import BM25Retriever, chunk_to_article, load_chunks
from src.retrieval.service import LAW_RRF_K
from src.retrieval.terms import expand_law

CHUNKS = "data/sample/chunks_expanded.jsonl"
EVAL_SET = "data/eval/dev.jsonl"
MODEL = "nlpai-lab/KURE-v1"
OUT = Path("data/eval/runs/hybrid-comparison.json")
SWEEP_OUT = Path("data/eval/runs/hybrid-sweep.json")

SANG = ["상가건물 임대차보호법", "상가건물 임대차보호법 시행령"]
WHERE = {"title": {"$nin": SANG}}


def evaluate(retriever, chunks, questions, expand_weight: float = 0.0) -> dict:
    id_to_article = chunk_to_article(chunks)
    ranks: dict[str, int] = {}
    recalls_at_3: dict[str, float] = {}
    recalls_at_5: dict[str, float] = {}

    for q in questions:
        articles: list[str] = []
        for chunk_id, _ in retriever.search(q["question"], 5, WHERE, expand_weight):
            article = id_to_article[chunk_id]
            if article not in articles:
                articles.append(article)
        gold = q["gold_articles"]
        ranks[q["qid"]] = next(
            (i for i, a in enumerate(articles, 1) if a in set(gold)), 0
        )
        recalls_at_3[q["qid"]] = recall_at_k(articles, gold, 3)
        recalls_at_5[q["qid"]] = recall_at_k(articles, gold, 5)

    n = len(questions)
    hits = lambda k: sum(1 for r in ranks.values() if r and r <= k) / n
    return {
        "hit@1": hits(1),
        "hit@3": hits(3),
        "hit@5": hits(5),
        "recall@3": sum(recalls_at_3.values()) / n,
        "recall@5": sum(recalls_at_5.values()) / n,
        "mrr": sum(1 / r for r in ranks.values() if r) / n,
        "failures": sorted(q for q, r in ranks.items() if r == 0),
        "partial@3": sorted(q for q, v in recalls_at_3.items() if 0 < v < 1.0),
        # 기존 JSON 소비자를 깨지 않도록 partial은 계속 top-5 기준으로 둔다.
        "partial": sorted(q for q, v in recalls_at_5.items() if 0 < v < 1.0),
        "ranks": ranks,
    }


def build(chunks: list[dict]):
    bm25 = BM25Retriever(chunks, b=0.25, query_expander=expand_law)
    dense = DenseRetriever(chunks, SentenceTransformerEmbedding(MODEL))
    return bm25, dense


def hybrid_of(bm25, dense, rrf_k=LAW_RRF_K, depth=DEFAULT_DEPTH,
              w_bm25=1.0, w_dense=1.0) -> HybridRetriever:
    return HybridRetriever(
        [Member(bm25, "bm25", w_bm25, 1.0), Member(dense, "kure", w_dense, 0.0)],
        rrf_k=rrf_k,
        depth=depth,
    )


def print_table(results: dict[str, dict], n: int) -> None:
    print()
    print(f"  {'방식':<28}{'Hit@1':>8}{'Hit@3':>8}{'Recall@3':>10}"
          f"{'Hit@5':>8}{'Recall@5':>10}{'MRR':>8}")
    print("  " + "-" * 80)
    for name, r in results.items():
        print(f"  {name:<28}{r['hit@1']:>7.1%}{r['hit@3']:>8.1%}"
              f"{r['recall@3']:>10.1%}{r['hit@5']:>8.1%}"
              f"{r['recall@5']:>10.1%}{r['mrr']:>8.3f}")
    print()
    for name, r in results.items():
        fails = ", ".join(x.replace("dev-", "") for x in r["failures"]) or "없음"
        partial3 = ", ".join(x.replace("dev-", "") for x in r["partial@3"]) or "없음"
        partial5 = ", ".join(x.replace("dev-", "") for x in r["partial"]) or "없음"
        print(f"  {name}")
        print(f"    정답을 못 찾음: {fails}")
        print(f"    상위 3건에서 일부만 찾음: {partial3}")
        print(f"    상위 5건에서 일부만 찾음: {partial5}")
    print()
    print(f"  ※ 문항 {n}개 기준 한 문항이 {1 / n:.1%}p 다. "
          f"설정 간 1~2문항 차이는 {1 / n:.0%}~{2 / n:.0%}p 이므로 "
          f"우열을 단정하지 않는다.")


# 기본 설정은 세 축이 만나는 지점이라 축마다 다시 재면 같은 계산이 세 번 나온다.
# 기준선으로 한 번만 재고 각 축에서는 건너뛴다.
RRF_K_VALUES = [10, 20, 30, DEFAULT_RRF_K, 100]  # 채택값 5 제외
DEPTH_VALUES = [5, 10, 40, 60]                # 기본 20 제외
# RRF 점수는 가중치에 선형이므로 두 값을 같은 배로 늘리면 순위가 바뀌지 않는다.
# (1, 2) 와 (0.5, 1) 은 같은 설정이다. 비(比)만 남기고 기본 1:1 은 제외한다.
WEIGHT_RATIOS = [(2.0, 1.0), (1.0, 1.5), (1.0, 2.0)]


def run_sweep(bm25, dense, chunks, questions) -> dict:
    """한 번에 한 축만 바꾼다. 격자를 통째로 돌리면 무엇이 효과인지 알 수 없다."""
    sweep: dict[str, dict] = {
        f"채택값 (rrf_k={LAW_RRF_K}, depth={DEFAULT_DEPTH}, 1:1)": evaluate(
            hybrid_of(bm25, dense), chunks, questions)
    }
    for value in RRF_K_VALUES:
        sweep[f"rrf_k={value}"] = evaluate(
            hybrid_of(bm25, dense, rrf_k=value), chunks, questions)
    for value in DEPTH_VALUES:
        sweep[f"depth={value}"] = evaluate(
            hybrid_of(bm25, dense, depth=value), chunks, questions)
    for wb, wk in WEIGHT_RATIOS:
        sweep[f"weight={wb:g}:{wk:g}"] = evaluate(
            hybrid_of(bm25, dense, w_bm25=wb, w_dense=wk), chunks, questions)
    return sweep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default=CHUNKS)
    ap.add_argument("--eval-set", default=EVAL_SET)
    ap.add_argument("--sweep", action="store_true", help="RRF 파라미터를 축별로 훑는다")
    args = ap.parse_args()

    chunks = load_chunks(args.chunks)
    questions = [q for q in load_questions(args.eval_set) if q.get("gold_articles")]
    multi = [q for q in questions if len(q["gold_articles"]) > 1]
    n = len(questions)

    print(f"\n  코퍼스 {len(chunks)}청크 · 채점 {n}문항 "
          f"(정답이 2개 이상인 문항 {len(multi)}개)")

    bm25, dense = build(chunks)
    results = {
        "BM25 (b=0.25, 사전)": evaluate(bm25, chunks, questions, 1.0),
        MODEL: evaluate(dense, chunks, questions),
        "Hybrid RRF (BM25 + KURE)": evaluate(
            hybrid_of(bm25, dense), chunks, questions),
    }
    print_table(results, n)

    payload = {
        "chunks": args.chunks,
        "eval_set": args.eval_set,
        "n": n,
        "questions_with_multiple_gold": [q["qid"] for q in multi],
        "standard_error_hit5": round(
            standard_error(results["Hybrid RRF (BM25 + KURE)"]["hit@5"], n), 4),
        "one_question_is": round(1 / n, 4),
        "config": {"rrf_k": LAW_RRF_K, "depth": DEFAULT_DEPTH,
                   "weights": {"bm25": 1.0, "kure": 1.0}, "model": MODEL},
        "results": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  기록: {OUT}")

    if args.sweep:
        sweep = run_sweep(bm25, dense, chunks, questions)
        print()
        print(f"  {'설정':<34}{'Hit@1':>8}{'Hit@3':>8}{'Recall@3':>10}"
              f"{'Hit@5':>8}{'Recall@5':>10}{'MRR':>8}")
        print("  " + "-" * 86)
        for name, r in sweep.items():
            print(f"  {name:<34}{r['hit@1']:>7.1%}{r['hit@3']:>8.1%}"
                  f"{r['recall@3']:>10.1%}{r['hit@5']:>8.1%}"
                  f"{r['recall@5']:>10.1%}{r['mrr']:>8.3f}")
        SWEEP_OUT.write_text(
            json.dumps({"n": n, "sweep": sweep}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\n  기록: {SWEEP_OUT}")
    print()


if __name__ == "__main__":
    main()
