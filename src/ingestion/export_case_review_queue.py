"""자동 범위 판정이 보류된 공식 판례를 사람이 검토할 CSV로 내보낸다."""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from src.ingestion.parse_cases import (
    DEFAULT_EXCLUDED_CASE_IDS,
    clean,
    record_from_raw,
    scope_decision,
)


FIELDNAMES = (
    "line",
    "case_id",
    "source_url",
    "case_number",
    "court_name",
    "decision_date",
    "case_type",
    "case_name",
    "holding",
    "referenced_law",
    "review_reason",
    "review_decision",
    "review_basis",
)


@dataclass
class ReviewQueueSummary:
    input_records: int = 0
    review_records: int = 0
    skipped_invalid: int = 0


def review_rows(
    lines: list[str], *, collected_at: str, source_label: str, min_holding_length: int = 30,
) -> tuple[list[dict[str, object]], ReviewQueueSummary]:
    """변환기와 같은 기준으로 수동 검토 대상과 필요한 원천 정보를 추린다."""

    rows: list[dict[str, object]] = []
    summary = ReviewQueueSummary()
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        summary.input_records += 1
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            summary.skipped_invalid += 1
            continue
        if not isinstance(raw, dict) or not isinstance(raw.get("service"), dict):
            summary.skipped_invalid += 1
            continue

        record = record_from_raw(raw, collected_at=collected_at, source_label=source_label)
        assert record is not None
        if record.validate() or len(record.holding) < min_holding_length:
            summary.skipped_invalid += 1
            continue
        if record.case_id in DEFAULT_EXCLUDED_CASE_IDS:
            continue

        decision, reason = scope_decision(raw)
        if decision != "review":
            continue
        service = raw["service"]
        assert isinstance(service, dict)
        rows.append(
            {
                "line": lineno,
                "case_id": record.case_id,
                "source_url": record.source_url,
                "case_number": record.case_number,
                "court_name": record.court_name,
                "decision_date": record.decision_date,
                "case_type": record.case_type,
                "case_name": record.case_name,
                "holding": record.holding,
                "referenced_law": clean(service.get("참조조문")),
                "review_reason": reason,
                "review_decision": "pending",
                "review_basis": "",
            }
        )
    summary.review_records = len(rows)
    return rows, summary


def write_review_queue(rows: list[dict[str, object]], path: Path) -> None:
    """Excel에서 바로 열 수 있도록 UTF-8 BOM CSV를 원자적으로 발행한다."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="수동 판례 범위 검토 CSV 생성")
    parser.add_argument("--input", required=True, help="공식 검증 상세 원천 JSONL")
    parser.add_argument("--output", required=True, help="검토용 CSV 경로")
    parser.add_argument("--collected-at", required=True)
    parser.add_argument("--report", default=None, help="추출 건수 보고서 JSON 경로")
    args = parser.parse_args()

    input_path, output_path = Path(args.input), Path(args.output)
    if not input_path.exists():
        parser.error(f"원천 파일이 없습니다: {input_path}")
    rows, summary = review_rows(
        input_path.read_text(encoding="utf-8").splitlines(),
        collected_at=args.collected_at,
        source_label=input_path.as_posix(),
    )
    write_review_queue(rows, output_path)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"수동 검토표: {output_path} ({summary.review_records}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
