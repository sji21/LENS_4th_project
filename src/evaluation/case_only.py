"""PATCH-018 청크 규격을 쓰는 판례 전용 평가 공통 도구.

판례 원천은 ``scripts/load_case_only_demo_corpus.py``가 공통 SQLite에 적재하고
``src.ingestion.load_cases.export_case_chunks``가 JSONL로 추출한다. 이 모듈은 그
산출물을 다시 적재하지 않고, 평가 질문·정답 판례·순위 지표만 담당한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

from src.evaluation.run_eval import load_questions
from src.retrieval.retriever import load_chunks


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASE_CHUNKS = ROOT / "data" / "chunks" / "cases.jsonl"
DEFAULT_EVAL_SET = ROOT / "data" / "eval" / "dev.jsonl"

# 현재 27개 평가 질문 중 판례 요약만으로 답할 수 있는 문항이다. 나머지는 현행
# 법조문·금액·행정절차 또는 개별 사실관계가 필요하므로 검색 성능에 포함하지 않는다.
CASE_ANSWERABLE_QUESTION_IDS = frozenset({1, 2, 3, 4, 5, 6, 9, 10, 12, 13, 18, 20, 22})
ABSTAIN_REASON = "판례만으로는 현행 법조문·금액·행정절차를 완결해 답할 수 없음"


def load_case_chunks(path: Path | str = DEFAULT_CASE_CHUNKS) -> list[dict[str, Any]]:
    """PATCH-018 ``export_case_chunks`` 산출물에서 판례 청크만 읽는다."""

    chunks = [
        chunk for chunk in load_chunks(path)
        if chunk.get("metadata", {}).get("doc_type") == "case"
    ]
    if not chunks:
        raise ValueError(f"판례 청크가 없습니다: {path}")
    for chunk in chunks:
        metadata = chunk["metadata"]
        if not metadata.get("case_id") or not metadata.get("case_number"):
            raise ValueError(f"PATCH-018 판례 청크 메타데이터가 아닙니다: {chunk['chunk_id']}")
    return chunks


def load_case_questions(path: Path | str = DEFAULT_EVAL_SET) -> list[dict[str, Any]]:
    """판례 전용 실험이 사용하는 프로젝트 공통 27개 평가 질문을 읽는다."""

    questions = load_questions(path)
    if len(questions) != 27:
        raise ValueError(f"27개 평가 질문을 기대했지만 {len(questions)}개입니다: {path}")
    return questions


def source_ids_for_question(sources: Iterable[Any], question_id: int) -> set[str]:
    """판례 원천의 ``question_ids``에서 해당 질문의 정답 판례 ID를 구한다."""

    return {
        str(source.source_id)
        for source in sources
        if question_id in source.question_ids
    }


def make_abstain_row(question_id: int, question: str) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "question": question,
        "expected": "abstain",
        "decision": "abstain",
        "reason": ABSTAIN_REASON,
        "rank": None,
        "top5": [],
    }


def rank_of(top5: Sequence[dict[str, Any]], expected_source_ids: set[str]) -> int | None:
    return next(
        (int(item["rank"]) for item in top5 if item.get("source_id") in expected_source_ids),
        None,
    )


def metrics(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    answer_rows = [row for row in rows if row["expected"] == "answer"]
    if not answer_rows:
        raise ValueError("답변 가능 판례 질문이 없습니다.")

    def hit_at(k: int) -> float:
        return sum(
            row["rank"] is not None and row["rank"] <= k for row in answer_rows
        ) / len(answer_rows)

    mrr = sum((1 / row["rank"]) if row["rank"] else 0 for row in answer_rows)
    return {
        "hit_at_1": round(hit_at(1), 4),
        "hit_at_3": round(hit_at(3), 4),
        "hit_at_5": round(hit_at(5), 4),
        "mrr": round(mrr / len(answer_rows), 4),
    }
