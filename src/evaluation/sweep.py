"""파라미터 스윕.

한 번에 한 축만 바꾸고 나머지는 Baseline 값에 고정한다.
격자 전체를 한꺼번에 돌리면 무엇이 효과를 냈는지 알 수 없다.

실행:
    python -m src.evaluation.sweep
"""

from __future__ import annotations

from dataclasses import dataclass

from src.evaluation.metrics import (
    QuestionResult,
    RunResult,
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
    standard_error,
)
from src.evaluation.run_eval import load_questions
from src.retrieval.retriever import BM25Retriever, chunk_to_article, load_chunks

CHUNKS = "data/sample/chunks_mock.jsonl"
EVAL_SET = "data/eval/dev.jsonl"

BASELINE = {"k": 5, "k1": 1.5, "b": 0.75, "char_ngram": 2}


@dataclass
class Config:
    k: int = 5
    k1: float = 1.5
    b: float = 0.75
    char_ngram: int = 2

    def label(self, axis: str) -> str:
        return f"{axis}={getattr(self, axis)}"


def evaluate(cfg: Config, chunks: list[dict], questions: list[dict]) -> RunResult:
    retriever = BM25Retriever(chunks, k1=cfg.k1, b=cfg.b, char_ngram=cfg.char_ngram)
    id_to_article = chunk_to_article(chunks)
    result = RunResult(run_id="sweep", k=cfg.k)

    for q in questions:
        gold = q.get("gold_articles")
        if not gold:
            continue
        articles: list[str] = []
        for chunk_id, _ in retriever.search(q["question"], cfg.k):
            article = id_to_article[chunk_id]
            if article not in articles:
                articles.append(article)
        result.results.append(
            QuestionResult(
                qid=q["qid"],
                question=q["question"],
                gold_articles=gold,
                retrieved_articles=articles,
                hit=hit_at_k(articles, gold, cfg.k),
                rr=reciprocal_rank(articles, gold),
                recall=recall_at_k(articles, gold, cfg.k),
            )
        )
    return result


def hit_at(result: RunResult, k: int) -> float:
    """같은 실행 결과를 더 작은 k로 다시 채점한다."""
    if not result.results:
        return 0.0
    hits = sum(hit_at_k(r.retrieved_articles, r.gold_articles, k) for r in result.results)
    return hits / len(result.results)


def flips(base: RunResult, other: RunResult) -> tuple[list[str], list[str]]:
    b = {r.qid: r.hit for r in base.results}
    o = {r.qid: r.hit for r in other.results}
    fixed = sorted(q for q in b if not b[q] and o.get(q))
    broken = sorted(q for q in b if b[q] and not o.get(q))
    return fixed, broken


def main() -> None:
    chunks = load_chunks(CHUNKS)
    questions = load_questions(EVAL_SET)

    base_cfg = Config(**BASELINE)
    base = evaluate(base_cfg, chunks, questions)
    se = standard_error(base.hit_rate, base.n)

    print()
    print(f"Baseline  k1={base_cfg.k1} b={base_cfg.b} ngram={base_cfg.char_ngram}  "
          f"(문항 {base.n}개)")
    print(f"  Hit@1 {hit_at(base,1):.1%}   Hit@3 {hit_at(base,3):.1%}   "
          f"Hit@5 {base.hit_rate:.1%}   MRR {base.mrr:.3f}")
    print(f"  표준오차 약 {se:.1%}p - 문항 {round(2*se*base.n)}개 미만 차이는 노이즈로 본다")

    axes = {
        "k1": [0.6, 0.9, 1.2, 1.5, 2.0, 2.5],
        "b": [0.0, 0.25, 0.5, 0.75, 1.0],
        "char_ngram": [2, 3],
    }

    for axis, values in axes.items():
        print()
        print("=" * 74)
        print(f"  축: {axis}   (나머지는 Baseline 고정)")
        print("=" * 74)
        print(f"  {'설정':<16}{'Hit@1':>8}{'Hit@3':>8}{'Hit@5':>8}{'MRR':>8}   변화")
        print("  " + "-" * 70)
        for value in values:
            cfg = Config(**{**BASELINE, axis: value})
            res = evaluate(cfg, chunks, questions)
            fixed, broken = flips(base, res)
            hit1_delta = hit_at(res, 1) - hit_at(base, 1)
            mrr_delta = res.mrr - base.mrr
            if value == getattr(base_cfg, axis):
                change = "(baseline)"
            else:
                changes: list[str] = []
                if fixed or broken:
                    changes.append(f"Hit@{cfg.k} +{len(fixed)} / -{len(broken)}")
                else:
                    changes.append(f"Hit@{cfg.k} 동일")
                if fixed:
                    changes.append(f"살아남: {','.join(f.replace('dev-','') for f in fixed)}")
                if broken:
                    changes.append(f"깨짐: {','.join(b.replace('dev-','') for b in broken)}")
                if abs(hit1_delta) >= 0.00005:
                    changes.append(f"Hit@1 {hit1_delta:+.1%}p")
                if abs(mrr_delta) >= 0.00005:
                    changes.append(f"MRR {mrr_delta:+.3f}")
                if len(changes) == 1 and not fixed and not broken:
                    changes[0] = "전체 동일"
                change = "  ".join(changes)
            print(f"  {cfg.label(axis):<16}{hit_at(res,1):>7.1%}{hit_at(res,3):>8.1%}"
                  f"{res.hit_rate:>8.1%}{res.mrr:>8.3f}   {change}")
    print()


if __name__ == "__main__":
    main()
