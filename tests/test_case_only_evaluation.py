"""PATCH-018 판례 청크 기반 평가 보조 도구 테스트."""

from __future__ import annotations

import json

from src.evaluation.case_only import load_case_chunks, metrics, rank_of


def test_load_case_chunks_accepts_patch_018_case_schema(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(
            {
                "chunk_id": "case:CASE-1#0",
                "text": "[대법원 2024다1 판례]\n판결 요지",
                "metadata": {
                    "doc_type": "case",
                    "case_id": "CASE-1",
                    "case_number": "2024다1",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    assert load_case_chunks(path)[0]["chunk_id"] == "case:CASE-1#0"


def test_case_only_metrics_exclude_abstained_questions():
    rows = [
        {"expected": "answer", "rank": 1},
        {"expected": "answer", "rank": 3},
        {"expected": "abstain", "rank": None},
    ]

    assert metrics(rows) == {
        "hit_at_1": 0.5,
        "hit_at_3": 1.0,
        "hit_at_5": 1.0,
        "mrr": 0.6667,
    }
    assert rank_of(
        [{"rank": 1, "source_id": "CASE-OTHER"}, {"rank": 2, "source_id": "CASE-1"}],
        {"CASE-1"},
    ) == 2
