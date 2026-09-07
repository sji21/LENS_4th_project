"""판례 수동 검토표 추출 테스트."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from src.ingestion.export_case_review_queue import review_rows, write_review_queue


def raw_case(**service_overrides: object) -> dict[str, object]:
    service = {
        "사건번호": "2024다12345",
        "법원명": "대법원",
        "선고일자": "20240102",
        "사건종류명": "민사",
        "사건명": "임대차보증금반환",
        "판결요지": "임대차보증금 반환과 관련한 충분히 긴 공식 판결요지입니다.",
        "판례내용": "공식 판례 전문입니다.",
        "참조조문": "",
    }
    service.update(service_overrides)
    return {"case_id": "review-1", "source_url": "https://example.test/review-1", "service": service}


def test_exports_only_valid_scope_review_records(tmp_path: Path) -> None:
    included = raw_case(**{"참조조문": "주택임대차보호법 제3조"})
    commercial = raw_case(**{"참조조문": "상가건물 임대차보호법 제10조", "사건명": "상가 권리금"})
    lines = [json.dumps(item, ensure_ascii=False) for item in (raw_case(), included, commercial, {"service": {}})]

    rows, summary = review_rows(
        lines,
        collected_at="2026-08-30T00:00:00Z",
        source_label="fixture.jsonl",
    )

    assert summary.input_records == 4
    assert summary.review_records == 1
    assert summary.skipped_invalid == 1
    assert rows[0]["review_decision"] == "pending"
    assert rows[0]["case_id"] == "review-1"

    output_path = tmp_path / "review.csv"
    write_review_queue(rows, output_path)
    with output_path.open(encoding="utf-8-sig", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert exported[0]["review_reason"] == "주택임대차보호법 적용·참조 근거가 없어 수동 검토 필요"
