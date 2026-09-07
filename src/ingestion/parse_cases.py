"""국가법령정보센터 판례 상세 원천 JSONL을 안전하게 표준 JSONL로 변환한다.

원천 응답의 API 오류와 범위 제외를 같은 ``skipped`` 목록에 섞지 않는다. 오류나
중복 충돌이 있으면 기존 출력은 건드리지 않으며, 변환 보고서와 검토 manifest만 남긴다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

from src.ingestion.load_cases import CaseRecord, checksum_of, write_case_records


_HTML = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")
_DATE = re.compile(r"\d{8}")
HOUSING_LAW = re.compile(r"주택\s*임대차\s*보호법")
COMMERCIAL_TERMS = re.compile(r"상가|점포|권리금|상가건물\s*임대차\s*보호법")
LEGACY_HOUSING_SIGNALS = re.compile(
    r"주택|임대차|임차인|임대인|보증금|대항력|우선변제|소액임차|"
    r"확정일자|전입|주민등록|임차권"
)
DEFAULT_EXCLUDED_CASE_IDS = frozenset({"216659", "240969", "619451"})
REVIEW_DECISIONS = frozenset({"approved", "rejected", "pending"})


def clean(value: object) -> str:
    """API의 HTML·줄바꿈을 검색용 평문으로 정리한다."""

    text = _HTML.sub(" ", str(value or "")).replace("&nbsp;", " ")
    return _SPACE.sub(" ", text).strip()


def date_of(value: object) -> str:
    """``YYYYMMDD`` 또는 날짜 문자열을 ``YYYY-MM-DD``로 정규화한다."""

    matched = _DATE.search(str(value or ""))
    if not matched:
        return ""
    digits = matched.group(0)
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


@dataclass(frozen=True)
class ReviewClassification:
    decision: Literal["approved", "rejected", "pending"]
    basis: str


def load_review_classifications(path: Path) -> dict[str, ReviewClassification]:
    """수동 검토 CSV의 case_id별 승인·제외·보류 결정을 읽는다."""

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"case_id", "review_decision", "review_basis"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            missing = ", ".join(sorted(required.difference(reader.fieldnames or [])))
            raise ValueError(f"수동 검토 CSV 필수 열이 없습니다: {missing}")
        classifications: dict[str, ReviewClassification] = {}
        for lineno, row in enumerate(reader, 2):
            case_id = clean(row.get("case_id"))
            decision = clean(row.get("review_decision")).lower()
            basis = clean(row.get("review_basis"))
            if not case_id:
                raise ValueError(f"수동 검토 CSV {lineno}번째 줄의 case_id가 비어 있음")
            if decision not in REVIEW_DECISIONS:
                raise ValueError(f"수동 검토 CSV {lineno}번째 줄의 review_decision이 허용값이 아님: {decision}")
            classification = ReviewClassification(decision=decision, basis=basis)
            previous = classifications.setdefault(case_id, classification)
            if previous != classification:
                raise ValueError(f"수동 검토 CSV의 case_id 결정이 충돌함: {case_id}")
    return classifications


@dataclass
class ParseSummary:
    input_records: int = 0
    records: int = 0
    excluded: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    error_records: list[dict[str, object]] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def can_publish(self) -> bool:
        return self.records > 0 and not self.errors and not self.conflicts

    def counts(self) -> dict[str, int]:
        return {
            "input_records": self.input_records,
            "records": self.records,
            "excluded": len(self.excluded),
            "errors": len(self.errors),
            "needs_review": len(self.needs_review),
            "conflicts": len(self.conflicts),
        }

    def reason_counts(self) -> dict[str, dict[str, int]]:
        groups = {
            "excluded": self.excluded,
            "errors": self.errors,
            "needs_review": self.needs_review,
            "conflicts": self.conflicts,
        }
        counts: dict[str, dict[str, int]] = {}
        for group, reasons in groups.items():
            grouped: dict[str, int] = {}
            for reason in reasons:
                category = reason.rsplit(": ", 1)[-1]
                grouped[category] = grouped.get(category, 0) + 1
            counts[group] = dict(sorted(grouped.items()))
        return counts

    def add_error(
        self, *, lineno: int, raw: object, reason: str, missing_fields: list[str] | None = None,
    ) -> None:
        """사람용 오류 문구와 재수집용 원천 위치를 함께 남긴다."""

        raw_mapping = raw if isinstance(raw, dict) else {}
        case_id = clean(raw_mapping.get("case_id"))
        source_url = clean(raw_mapping.get("source_url"))
        prefix = f"{lineno}번째 줄 {case_id or '(case_id 없음)'}"
        self.errors.append(f"{prefix}: {reason}")
        self.error_records.append(
            {
                "line": lineno,
                "case_id": case_id,
                "source_url": source_url,
                "reason": reason,
                "missing_fields": missing_fields or [],
            }
        )


def record_from_raw(
    raw: dict[str, object], *, collected_at: str, source_label: str
) -> CaseRecord | None:
    """공식 상세 응답 하나를 서비스 표준 레코드로 바꾼다."""

    service = raw.get("service")
    if not isinstance(service, dict):
        return None

    case_id = clean(raw.get("case_id"))
    case_number = clean(service.get("사건번호"))
    court_name = clean(service.get("법원명"))
    decision_date = date_of(service.get("선고일자"))
    case_type = clean(service.get("사건종류명")) or "민사"
    case_name = clean(service.get("사건명"))
    holding = clean(service.get("판결요지"))
    full_text = clean(service.get("판례내용"))
    source_url = clean(raw.get("source_url"))
    if not source_url and case_id:
        source_url = f"https://www.law.go.kr/LSW/precInfoP.do?precSeq={case_id}"

    return CaseRecord(
        case_id=case_id,
        case_number=case_number,
        court_name=court_name,
        decision_date=decision_date,
        case_type=case_type,
        case_name=case_name,
        holding=holding,
        summary=holding,
        full_text=full_text,
        source_url=source_url,
        collected_at=collected_at,
        status="current",
        file_path=source_label,
        summary_type="official",
        summary_model=None,
    )


def scope_decision(raw: dict[str, object]) -> tuple[Literal["include", "exclude", "review"], str]:
    """주택임대차 자동 적재 여부를 보수적으로 판정한다.

    주택임대차보호법 적용·참조 근거가 있는 사건만 자동 적재한다. 상가 신호가
    있거나 단순 임대차 키워드만 있는 사건은 코퍼스에 넣지 않고 별도 검토한다.
    """

    service = raw.get("service")
    if not isinstance(service, dict):
        return "review", "service 객체를 확인할 수 없음"
    legal_text = " ".join(
        clean(service.get(key))
        for key in ("참조조문", "판시사항", "판결요지", "사건명")
    )
    has_housing_law = bool(HOUSING_LAW.search(legal_text))
    has_commercial_signal = bool(COMMERCIAL_TERMS.search(legal_text))
    if has_housing_law and not has_commercial_signal:
        return "include", "주택임대차보호법 적용·참조 확인"
    if has_commercial_signal and not has_housing_law:
        return "exclude", "상가·점포·권리금 사건"
    if has_housing_law and has_commercial_signal:
        return "review", "주택·상가 적용 신호가 함께 있어 수동 검토 필요"
    if LEGACY_HOUSING_SIGNALS.search(legal_text):
        return "review", "주택임대차보호법 적용·참조 근거가 없어 수동 검토 필요"
    return "exclude", "주택임대차 범위 밖"


def record_identity(record: CaseRecord) -> tuple[str, str, str, str, str]:
    """같은 사건번호 공개본이 같은 사건인지 판단하는 최소 비교 기준."""

    return (
        record.case_number,
        record.court_name,
        record.decision_date,
        record.case_name,
        checksum_of(record.full_text),
    )


def parse_raw_lines(
    lines: list[str], *, collected_at: str, source_label: str, min_holding_length: int = 30,
    include_all: bool = False, review_classifications: Mapping[str, ReviewClassification] | None = None,
) -> tuple[list[CaseRecord], ParseSummary]:
    """원천 줄을 검사하고, 오류·제외·검토 대상을 분리해 반환한다."""

    summary = ParseSummary()
    candidates: list[CaseRecord] = []
    by_case_id: dict[str, CaseRecord] = {}

    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        summary.input_records += 1
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            summary.add_error(lineno=lineno, raw={}, reason=f"JSON 파싱 실패 - {error}")
            continue
        if not isinstance(raw, dict):
            summary.add_error(lineno=lineno, raw=raw, reason="최상위 값이 객체가 아님")
            continue
        if not isinstance(raw.get("service"), dict):
            summary.add_error(lineno=lineno, raw=raw, reason="service 객체가 없음")
            continue

        record = record_from_raw(raw, collected_at=collected_at, source_label=source_label)
        assert record is not None
        problems = record.validate()
        if problems:
            missing_fields = [problem.removesuffix(" 이 비어 있음") for problem in problems if problem.endswith(" 이 비어 있음")]
            summary.add_error(
                lineno=lineno,
                raw=raw,
                reason="; ".join(problems),
                missing_fields=missing_fields,
            )
            continue
        if len(record.holding) < min_holding_length:
            summary.excluded.append(
                f"{lineno}번째 줄 {record.case_id}: 판결요지가 너무 짧음"
            )
            continue
        if not include_all and record.case_id in DEFAULT_EXCLUDED_CASE_IDS:
            summary.excluded.append(
                f"{lineno}번째 줄 {record.case_id}: 수동 제외 목록의 상가 사건"
            )
            continue
        if not include_all:
            decision, reason = scope_decision(raw)
            if decision == "exclude":
                summary.excluded.append(f"{lineno}번째 줄 {record.case_id}: {reason}")
                continue
            if decision == "review":
                classification = (review_classifications or {}).get(record.case_id)
                if classification is None or classification.decision == "pending":
                    suffix = f" ({classification.basis})" if classification and classification.basis else ""
                    summary.needs_review.append(f"{lineno}번째 줄 {record.case_id}: {reason}{suffix}")
                    continue
                if classification.decision == "rejected":
                    suffix = f": {classification.basis}" if classification.basis else ""
                    summary.excluded.append(f"{lineno}번째 줄 {record.case_id}: 수동 검토 제외{suffix}")
                    continue
                reason = f"수동 검토 승인{': ' + classification.basis if classification.basis else ''}"

        existing = by_case_id.get(record.case_id)
        if existing:
            if record_identity(existing) == record_identity(record):
                summary.excluded.append(f"{lineno}번째 줄 {record.case_id}: 동일 case_id 중복")
            else:
                summary.conflicts.append(
                    f"{lineno}번째 줄 {record.case_id}: 동일 case_id의 필드 값이 다름"
                )
            continue
        by_case_id[record.case_id] = record
        candidates.append(record)

    by_number: dict[str, list[CaseRecord]] = {}
    for record in candidates:
        by_number.setdefault(record.case_number, []).append(record)

    records: list[CaseRecord] = []
    for case_number, group in by_number.items():
        identities = {record_identity(record) for record in group}
        if len(identities) > 1:
            ids = ", ".join(sorted(record.case_id for record in group))
            summary.conflicts.append(
                f"사건번호 {case_number}: 동일성 필드가 다른 case_id ({ids}) - 수동 검토 필요"
            )
            continue
        selected = max(
            group,
            key=lambda record: (int(record.case_id), record.case_id)
            if record.case_id.isdecimal() else (-1, record.case_id),
        )
        records.append(selected)
        for record in group:
            if record is not selected:
                summary.excluded.append(
                    f"사건번호 {case_number}: 동일 사건 공개본 {record.case_id} 제외 (선택: {selected.case_id})"
                )

    records.sort(key=lambda record: (record.decision_date, record.case_number, record.case_id))
    summary.records = len(records)
    return records, summary


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_atomically(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def write_conversion_metadata(
    *, input_path: Path, output_path: Path, records: list[CaseRecord], summary: ParseSummary,
    report_path: Path, manifest_path: Path,
) -> None:
    manifest = {
        "input": str(input_path),
        "input_sha256": sha256_of(input_path),
        "record_count": len(records),
        "case_ids": [record.case_id for record in records],
    }
    if output_path.exists() and summary.can_publish:
        manifest["output"] = str(output_path)
        manifest["output_sha256"] = sha256_of(output_path)
    report = {
        "input": str(input_path),
        "output": str(output_path),
        "counts": summary.counts(),
        "reason_counts": summary.reason_counts(),
        "published": summary.can_publish,
        "excluded": summary.excluded,
        "errors": summary.errors,
        "error_records": summary.error_records,
        "needs_review": summary.needs_review,
        "conflicts": summary.conflicts,
    }
    write_json_atomically(manifest, manifest_path)
    write_json_atomically(report, report_path)


def convert_file(
    *, input_path: Path, output_path: Path, collected_at: str, source_label: str,
    min_holding_length: int, include_all: bool, report_path: Path, manifest_path: Path,
    review_classifications: Mapping[str, ReviewClassification] | None = None,
) -> tuple[int, ParseSummary]:
    """원천을 변환한다. 실패 시 기존 ``output_path``를 절대 변경하지 않는다."""

    records, summary = parse_raw_lines(
        input_path.read_text(encoding="utf-8").splitlines(),
        collected_at=collected_at,
        source_label=source_label,
        min_holding_length=min_holding_length,
        include_all=include_all,
        review_classifications=review_classifications,
    )
    if not summary.records:
        summary.errors.append("유효한 자동 적재 판례가 0건")
    if summary.can_publish:
        write_case_records(records, output_path)
    write_conversion_metadata(
        input_path=input_path,
        output_path=output_path,
        records=records,
        summary=summary,
        report_path=report_path,
        manifest_path=manifest_path,
    )
    return (0 if summary.can_publish else 1), summary


def main() -> int:
    parser = argparse.ArgumentParser(description="공식 판례 상세 원천을 안전하게 CaseRecord JSONL로 변환")
    parser.add_argument("--input", required=True, help="공식 판례 상세 원천 JSONL")
    parser.add_argument("--output", default="data/parsed/case_records.jsonl", help="표준 CaseRecord JSONL")
    parser.add_argument("--collected-at", required=True, help="원천 수집 시각(예: 2026-08-30T00:00:00Z)")
    parser.add_argument("--source-label", default=None, help="레코드 file_path에 남길 원천 경로")
    parser.add_argument("--min-holding-length", type=int, default=30)
    parser.add_argument("--include-all", action="store_true", help="범위 및 수동 제외 규칙을 적용하지 않는다.")
    parser.add_argument("--review-classification", default=None, help="수동 판례 범위 검토 CSV 경로")
    parser.add_argument("--report", default=None, help="제외·오류·검토 사유 보고서 JSON 경로")
    parser.add_argument("--manifest", default=None, help="입력·출력 SHA-256과 case_id manifest 경로")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"원천 파일이 없습니다: {input_path}")
        return 1
    if args.min_holding_length < 1:
        print("--min-holding-length는 1 이상이어야 합니다.")
        return 1

    source_label = args.source_label or input_path.as_posix()
    try:
        review_classifications = (
            load_review_classifications(Path(args.review_classification))
            if args.review_classification else None
        )
    except (OSError, ValueError) as error:
        print(f"수동 검토 CSV를 읽을 수 없습니다: {error}")
        return 1
    report_path = Path(args.report) if args.report else output_path.with_suffix(".report.json")
    manifest_path = Path(args.manifest) if args.manifest else output_path.with_suffix(".manifest.json")
    code, summary = convert_file(
        input_path=input_path,
        output_path=output_path,
        collected_at=args.collected_at,
        source_label=source_label,
        min_holding_length=args.min_holding_length,
        include_all=args.include_all,
        report_path=report_path,
        manifest_path=manifest_path,
        review_classifications=review_classifications,
    )
    print(f"  표준 판례 레코드: {output_path} ({summary.records}건)")
    print("  결과: " + " · ".join(f"{key} {value}건" for key, value in summary.counts().items()))
    print(f"  보고서: {report_path}")
    print(f"  manifest: {manifest_path}")
    if code:
        print("  오류 또는 충돌이 있어 기존 출력 파일을 변경하지 않았습니다.")
    return code


if __name__ == "__main__":
    sys.exit(main())
