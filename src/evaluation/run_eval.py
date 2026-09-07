"""검색 평가 실행기 (채점 프로그램).

하는 일
  1. 정답 조문이 코퍼스에 실재하는지 먼저 검사한다 (이게 깨져 있으면 이후 수치는 전부 무의미)
  2. 평가 문항을 전부 검색기에 넣고 채점한다
  3. 요약 지표와 실패 문항 목록을 출력하고 runs/{run_id}.json 으로 남긴다

실행 예시
    python -m src.evaluation.run_eval --run-id baseline
    python -m src.evaluation.run_eval --run-id exp01-k8 --k 8
    python -m src.evaluation.run_eval --run-id exp02-b0 --b 0.0 --compare baseline
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.evaluation.metrics import (
    QuestionResult,
    RunResult,
    compare,
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
    standard_error,
)
from src.retrieval.retriever import BM25Retriever, Retriever, chunk_to_article, load_chunks

RUNS_DIR = Path("data/eval/runs")


def load_questions(path: str | Path) -> list[dict]:
    questions = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    return questions


def check_gold_exists(questions: list[dict], chunks: list[dict]) -> list[str]:
    """평가셋의 정답 조문이 실제로 코퍼스에 있는지 확인한다.

    없는 조문을 정답으로 걸어두면 검색기가 아무리 좋아도 절대 못 맞힌다.
    그건 검색 성능이 아니라 데이터 결함이므로 실험 전에 걸러야 한다.
    """
    available = {c["metadata"]["article_id"] for c in chunks}
    missing = []
    for q in questions:
        for gold in q.get("gold_articles", []):
            if gold not in available:
                missing.append(f"{q['qid']}: {gold}")
    return missing


def run(
    questions: list[dict],
    chunks: list[dict],
    retriever: Retriever,
    run_id: str,
    k: int,
) -> RunResult:
    id_to_article = chunk_to_article(chunks)
    result = RunResult(run_id=run_id, k=k)

    for q in questions:
        # 정답이 없는 문항(보류/거절 대상)은 검색 지표 대상이 아니다.
        # 보류 정확도는 생성 단계 담당이므로 여기서는 제외한다.
        if not q.get("gold_articles"):
            continue

        hits = retriever.search(q["question"], k)
        # 한 조문이 여러 청크로 쪼개졌을 수 있으므로 조문 단위로 중복 제거하며 순서 유지
        articles: list[str] = []
        for chunk_id, _score in hits:
            article = id_to_article[chunk_id]
            if article not in articles:
                articles.append(article)

        gold = q["gold_articles"]
        result.results.append(
            QuestionResult(
                qid=q["qid"],
                question=q["question"],
                gold_articles=gold,
                retrieved_articles=articles,
                hit=hit_at_k(articles, gold, k),
                rr=reciprocal_rank(articles, gold),
                recall=recall_at_k(articles, gold, k),
            )
        )
    return result


def print_report(result: RunResult, questions: list[dict]) -> None:
    by_qid = {q["qid"]: q for q in questions}
    se = standard_error(result.hit_rate, result.n)

    print()
    print("=" * 72)
    print(f"  실행 이름: {result.run_id}      채점 문항: {result.n}개      k = {result.k}")
    print("=" * 72)
    print(f"  Hit@{result.k}      {result.hit_rate:6.1%}   "
          f"(상위 {result.k}개 안에 정답 조문이 있었던 비율)")
    print(f"  MRR         {result.mrr:6.3f}   (정답이 몇 등으로 나왔는지, 1등이면 1.0)")
    print(f"  Recall@{result.k}   {result.mean_recall:6.1%}   (정답 조문 전체 중 찾아낸 비율)")
    print()
    print(f"  ※ 문항 {result.n}개 기준 표준오차 약 {se:.1%}p — "
          f"다른 설정과 {2 * se:.1%}p 이내 차이는 우연일 수 있음")

    if result.failures:
        print()
        print(f"  실패 문항 {len(result.failures)}개")
        print("  " + "-" * 68)
        for qid in result.failures:
            q = by_qid[qid]
            r = next(r for r in result.results if r.qid == qid)
            print(f"  [{qid}] {q['question']}")
            print(f"      정답: {', '.join(r.gold_articles)}")
            print(f"      검색: {', '.join(r.retrieved_articles[:result.k]) or '(결과 없음)'}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="검색 성능 평가")
    parser.add_argument("--run-id", default="baseline", help="이 실험의 이름")
    parser.add_argument("--chunks", default="data/sample/chunks_mock.jsonl")
    parser.add_argument("--eval-set", default="data/eval/dev.jsonl")
    parser.add_argument("--k", type=int, default=5, help="상위 몇 개까지 볼지")
    parser.add_argument("--k1", type=float, default=1.5, help="BM25 k1 (단어 반복 가중)")
    parser.add_argument("--b", type=float, default=0.75, help="BM25 b (문서 길이 보정)")
    parser.add_argument("--char-ngram", type=int, default=2, help="문자 n-gram 크기")
    parser.add_argument("--compare", default=None, help="비교할 이전 실행 이름")
    args = parser.parse_args()

    chunks = load_chunks(args.chunks)
    questions = load_questions(args.eval_set)

    missing = check_gold_exists(questions, chunks)
    if missing:
        print("\n[경고] 코퍼스에 없는 정답 조문이 있습니다. 이 문항은 절대 맞출 수 없습니다:")
        for m in missing:
            print(f"  - {m}")
        print("  → 코퍼스를 넓히거나, 해당 문항을 unanswerable 로 재분류하세요.\n")

    retriever = BM25Retriever(chunks, k1=args.k1, b=args.b, char_ngram=args.char_ngram)
    result = run(questions, chunks, retriever, args.run_id, args.k)
    print_report(result, questions)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RUNS_DIR / f"{args.run_id}.json"
    payload = {
        "config": {
            "retriever": "bm25",
            "k": args.k,
            "k1": args.k1,
            "b": args.b,
            "char_ngram": args.char_ngram,
            "chunks": args.chunks,
            "eval_set": args.eval_set,
        },
        "metrics": result.summary(),
        "per_question": [
            {
                "qid": r.qid,
                "hit": r.hit,
                "rr": round(r.rr, 4),
                "retrieved": r.retrieved_articles,
                "gold": r.gold_articles,
            }
            for r in result.results
        ],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  기록: {out_path}\n")

    if args.compare:
        prev_path = RUNS_DIR / f"{args.compare}.json"
        if not prev_path.exists():
            print(f"  [건너뜀] 비교 대상 {prev_path} 이 없습니다.\n")
            return
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
        prev_result = RunResult(run_id=args.compare, k=prev["config"]["k"])
        prev_result.results = [
            QuestionResult(
                qid=p["qid"],
                question="",
                gold_articles=p["gold"],
                retrieved_articles=p["retrieved"],
                hit=p["hit"],
                rr=p["rr"],
                recall=0.0,
            )
            for p in prev["per_question"]
        ]
        diff = compare(prev_result, result)
        print("-" * 72)
        print(f"  {args.compare}  →  {args.run_id}")
        print("-" * 72)
        print(f"  Hit  {diff['hit_before']:.1%} → {diff['hit_after']:.1%} "
              f"({diff['hit_delta']:+.1%}p)")
        print(f"  실패→성공으로 뒤집힘: {len(diff['fixed'])}개  {diff['fixed']}")
        print(f"  성공→실패로 뒤집힘: {len(diff['broken'])}개  {diff['broken']}")
        print(f"  순증감: {diff['net']:+d}개")
        print()


if __name__ == "__main__":
    main()
