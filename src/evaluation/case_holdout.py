"""현재 판례 코퍼스의 case_id 정답을 사용하는 검색 홀드아웃 평가 도구."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.retrieval.retriever import Retriever


def load_case_holdout(path: Path | str) -> list[dict[str, Any]]:
    """판례 질문과 안정적인 정답 ``case_id``를 읽고 기본 형식을 검증한다."""

    questions = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not questions:
        raise ValueError("판례 홀드아웃 문항이 없습니다.")

    seen_qids: set[str] = set()
    for item in questions:
        qid = str(item.get("qid", "")).strip()
        question = str(item.get("question", "")).strip()
        gold_case_ids = item.get("gold_case_ids")
        if not qid or not question or not isinstance(gold_case_ids, list) or not gold_case_ids:
            raise ValueError(f"홀드아웃 문항 형식이 올바르지 않습니다: {item!r}")
        if qid in seen_qids:
            raise ValueError(f"홀드아웃 qid가 중복되었습니다: {qid}")
        seen_qids.add(qid)
    return questions


def case_id_by_chunk(chunks: list[dict[str, Any]]) -> dict[str, str]:
    """검색 청크 ID를 판례의 안정 식별자 ``case_id``로 변환한다."""

    result: dict[str, str] = {}
    for chunk in chunks:
        case_id = str(chunk.get("metadata", {}).get("case_id", "")).strip()
        if not case_id:
            raise ValueError(f"판례 case_id가 없는 청크입니다: {chunk.get('chunk_id')}")
        result[str(chunk["chunk_id"])] = case_id
    return result


def check_gold_case_ids(questions: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> list[str]:
    """정답 판례가 실제 평가 코퍼스에 모두 있는지 확인한다."""

    available = set(case_id_by_chunk(chunks).values())
    return [
        f"{item['qid']}: {case_id}"
        for item in questions
        for case_id in item["gold_case_ids"]
        if str(case_id) not in available
    ]


def evaluate_case_holdout(
    questions: list[dict[str, Any]], chunks: list[dict[str, Any]], retriever: Retriever, k: int = 5
) -> dict[str, Any]:
    """판례 ID 단위로 Hit@k, MRR와 문항별 순위를 계산한다."""

    if k < 1:
        raise ValueError("k는 1 이상이어야 합니다.")
    missing = check_gold_case_ids(questions, chunks)
    if missing:
        raise ValueError("코퍼스에 없는 정답 판례가 있습니다: " + ", ".join(missing))

    ids = case_id_by_chunk(chunks)
    rows: list[dict[str, Any]] = []
    for item in questions:
        gold = {str(case_id) for case_id in item["gold_case_ids"]}
        ranked: list[dict[str, Any]] = []
        seen_case_ids: set[str] = set()
        for chunk_id, score in retriever.search(str(item["question"]), k):
            case_id = ids[chunk_id]
            if case_id in seen_case_ids:
                continue
            seen_case_ids.add(case_id)
            ranked.append({"case_id": case_id, "score": round(float(score), 6)})

        rank = next(
            (position for position, item in enumerate(ranked, start=1) if item["case_id"] in gold),
            None,
        )
        rows.append(
            {
                "qid": item["qid"],
                "question": item["question"],
                "gold_case_ids": sorted(gold),
                "rank": rank,
                "top_k": ranked,
            }
        )

    total = len(rows)
    def hit_at(limit: int) -> float:
        return round(sum(row["rank"] is not None and row["rank"] <= limit for row in rows) / total, 4)

    return {
        "question_count": total,
        "metrics": {
            "hit_at_1": hit_at(1),
            "hit_at_3": hit_at(min(3, k)),
            "hit_at_5": hit_at(min(5, k)),
            "mrr": round(sum(1 / row["rank"] if row["rank"] else 0 for row in rows) / total, 4),
        },
        "results": rows,
    }
