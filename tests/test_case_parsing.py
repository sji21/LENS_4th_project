"""공식 판례 상세 원천 -> 표준 CaseRecord 안전 변환 테스트."""

from __future__ import annotations

import json
from pathlib import Path

from src.ingestion.parse_cases import ReviewClassification, convert_file, parse_raw_lines


def raw_case(**service_overrides: object) -> dict[str, object]:
    service = {
        "사건번호": "2024다12345",
        "법원명": "대법원",
        "선고일자": "20240102",
        "사건종류명": "민사",
        "사건명": "임대차보증금반환",
        "판결요지": "주택임대차보호법상 대항력을 갖춘 임차인의 보증금반환청구권을 판단했다.",
        "판례내용": "공식 판례 전문 본문. 판결요지보다 훨씬 긴 원문을 보존한다.",
        "참조조문": "주택임대차보호법 제3조",
    }
    service.update(service_overrides)
    return {"case_id": "12345", "source_url": "https://example.test/case/12345", "service": service}


def parse(lines: list[object]):
    return parse_raw_lines(
        [line if isinstance(line, str) else json.dumps(line, ensure_ascii=False) for line in lines],
        collected_at="2026-08-30T00:00:00Z",
        source_label="tests/fixtures/case_details_sample.jsonl",
    )


def test_parse_raw_lines_preserves_full_official_text_and_holding():
    records, summary = parse([raw_case()])

    assert summary.counts() == {
        "input_records": 1, "records": 1, "excluded": 0, "errors": 0,
        "needs_review": 0, "conflicts": 0,
    }
    record = records[0]
    assert record.decision_date == "2024-01-02"
    assert record.holding.startswith("주택임대차보호법상")
    assert record.summary == record.holding
    assert record.full_text == "공식 판례 전문 본문. 판결요지보다 훨씬 긴 원문을 보존한다."


def test_parse_rejects_invalid_calendar_date_but_accepts_leap_day():
    invalid_month = raw_case(**{"사건번호": "2025다1", "선고일자": "20251340"})
    invalid_day = raw_case(**{"사건번호": "2025다2", "선고일자": "20250230"})
    ordinary_day = raw_case(**{"사건번호": "2025다3", "선고일자": "20250228"})
    leap_day = raw_case(**{"사건번호": "2024다4", "선고일자": "20240229"})
    for case_id, raw in enumerate((invalid_month, invalid_day, ordinary_day, leap_day), 1):
        raw["case_id"] = str(case_id)

    records, summary = parse([invalid_month, invalid_day, ordinary_day, leap_day])

    assert [record.case_number for record in records] == ["2024다4", "2025다3"]
    assert sum("실제 달력 날짜가 아님" in reason for reason in summary.errors) == 2


def test_manual_review_classification_only_promotes_explicit_approval():
    review_holding = "임대차보증금 반환에 관한 충분히 긴 판결요지이나 적용 법령은 명확하지 않습니다."
    approved = raw_case(**{"사건번호": "2024다승인", "참조조문": "", "판결요지": review_holding})
    rejected = raw_case(**{"사건번호": "2024다제외", "참조조문": "", "판결요지": review_holding})
    pending = raw_case(**{"사건번호": "2024다보류", "참조조문": "", "판결요지": review_holding})
    approved["case_id"] = "approved-id"
    rejected["case_id"] = "rejected-id"
    pending["case_id"] = "pending-id"

    records, summary = parse_raw_lines(
        [json.dumps(item, ensure_ascii=False) for item in (approved, rejected, pending)],
        collected_at="2026-08-30T00:00:00Z",
        source_label="fixture.jsonl",
        review_classifications={
            "approved-id": ReviewClassification("approved", "주거용 임대차 확인"),
            "rejected-id": ReviewClassification("rejected", "상가 사건"),
            "pending-id": ReviewClassification("pending", "원문 확인 필요"),
        },
    )

    assert [record.case_number for record in records] == ["2024다승인"]
    assert any("수동 검토 제외" in reason for reason in summary.excluded)
    assert any("원문 확인 필요" in reason for reason in summary.needs_review)


def test_manual_review_uses_case_id_when_case_numbers_are_duplicated():
    holding = "임대차보증금 반환에 관한 충분히 긴 판결요지이나 적용 법령은 명확하지 않습니다."
    approved = raw_case(**{"사건번호": "2024다중복", "참조조문": "", "판결요지": holding})
    pending = raw_case(**{"사건번호": "2024다중복", "참조조문": "", "판결요지": holding, "법원명": "서울고등법원"})
    approved["case_id"] = "approved-duplicate"
    pending["case_id"] = "pending-duplicate"

    records, summary = parse_raw_lines(
        [json.dumps(item, ensure_ascii=False) for item in (approved, pending)],
        collected_at="2026-08-30T00:00:00Z",
        source_label="fixture.jsonl",
        review_classifications={
            "approved-duplicate": ReviewClassification("approved", "주거용 임대차 확인"),
            "pending-duplicate": ReviewClassification("pending", "원문 확인 필요"),
        },
    )

    assert [record.case_id for record in records] == ["approved-duplicate"]
    assert any("pending-duplicate" in reason for reason in summary.needs_review)


def test_committed_fixture_covers_include_exclude_and_review() -> None:
    fixture = Path(__file__).with_name("fixtures") / "case_details_sample.jsonl"
    records, summary = parse(fixture.read_text(encoding="utf-8").splitlines())

    assert [record.case_id for record in records] == ["fixture-housing"]
    assert len(summary.excluded) == 1
    assert len(summary.needs_review) == 1


def test_parse_separates_errors_exclusions_and_review_targets():
    commercial = raw_case(**{
        "사건번호": "2024다2", "참조조문": "상가건물 임대차보호법 제10조",
        "사건명": "상가 권리금 반환", "판결요지": "상가 점포 권리금 반환에 관한 충분히 긴 공식 판결요지입니다.",
    })
    ambiguous = raw_case(**{
        "사건번호": "2024다3", "참조조문": "", "사건명": "임대차보증금반환",
        "판결요지": "임대차보증금 반환과 관련한 충분히 긴 공식 판결요지이지만 적용 법령이 드러나지 않습니다.",
    })
    records, summary = parse([raw_case(), commercial, ambiguous, "{not json}", {"case_id": "bad", "service": {}}])

    assert len(records) == 1
    assert any("상가" in reason for reason in summary.excluded)
    assert any("수동 검토" in reason for reason in summary.needs_review)
    assert any("JSON 파싱 실패" in reason for reason in summary.errors)
    assert any("case_number 이 비어 있음" in reason for reason in summary.errors)
    assert summary.error_records[-1]["missing_fields"] == [
        "case_number", "court_name", "decision_date", "case_name", "holding", "summary", "full_text",
    ]
    assert summary.reason_counts()["excluded"]["상가·점포·권리금 사건"] == 1


def test_parse_same_case_number_deduplicates_only_identical_records():
    newer = raw_case()
    newer["case_id"] = "67890"
    records, summary = parse([raw_case(), newer])

    assert [record.case_id for record in records] == ["67890"]
    assert any("동일 사건 공개본" in reason for reason in summary.excluded)


def test_parse_same_case_number_with_different_identity_is_conflict():
    different = raw_case(**{"법원명": "서울고등법원"})
    different["case_id"] = "67890"
    records, summary = parse([raw_case(), different])

    assert records == []
    assert any("동일성 필드가 다른" in reason for reason in summary.conflicts)
    assert not summary.can_publish


def test_convert_preserves_existing_output_when_source_has_errors(tmp_path: Path):
    input_path = tmp_path / "source.jsonl"
    output_path = tmp_path / "records.jsonl"
    report_path = tmp_path / "report.json"
    manifest_path = tmp_path / "manifest.json"
    input_path.write_text(json.dumps(raw_case(), ensure_ascii=False) + "\n{bad json}\n", encoding="utf-8")
    output_path.write_text("existing-record\n", encoding="utf-8")

    code, summary = convert_file(
        input_path=input_path, output_path=output_path, collected_at="2026-08-30T00:00:00Z",
        source_label="fixture", min_holding_length=30, include_all=False,
        report_path=report_path, manifest_path=manifest_path,
    )

    assert code == 1
    assert summary.errors
    assert output_path.read_text(encoding="utf-8") == "existing-record\n"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["published"] is False
    assert report["error_records"][0]["source_url"] == ""
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["case_ids"] == ["12345"]


def test_convert_publishes_complete_output_with_manifest_hashes(tmp_path: Path):
    input_path = tmp_path / "source.jsonl"
    output_path = tmp_path / "records.jsonl"
    report_path = tmp_path / "report.json"
    manifest_path = tmp_path / "manifest.json"
    input_path.write_text(json.dumps(raw_case(), ensure_ascii=False) + "\n", encoding="utf-8")

    code, summary = convert_file(
        input_path=input_path, output_path=output_path, collected_at="2026-08-30T00:00:00Z",
        source_label="fixture", min_holding_length=30, include_all=False,
        report_path=report_path, manifest_path=manifest_path,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert code == 0
    assert summary.can_publish
    assert json.loads(output_path.read_text(encoding="utf-8"))["full_text"].startswith("공식 판례 전문")
    assert manifest["record_count"] == 1
    assert len(manifest["input_sha256"]) == len(manifest["output_sha256"]) == 64


def test_convert_rejects_zero_records_without_overwriting_output(tmp_path: Path):
    input_path = tmp_path / "source.jsonl"
    output_path = tmp_path / "records.jsonl"
    input_path.write_text(json.dumps(raw_case(**{"판결요지": "짧음"}), ensure_ascii=False) + "\n", encoding="utf-8")
    output_path.write_text("existing-record\n", encoding="utf-8")

    code, summary = convert_file(
        input_path=input_path, output_path=output_path, collected_at="2026-08-30T00:00:00Z",
        source_label="fixture", min_holding_length=30, include_all=False,
        report_path=tmp_path / "report.json", manifest_path=tmp_path / "manifest.json",
    )

    assert code == 1
    assert "유효한 자동 적재 판례가 0건" in summary.errors
    assert output_path.read_text(encoding="utf-8") == "existing-record\n"
