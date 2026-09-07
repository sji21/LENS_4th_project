"""공개 API에서 검증된 판례 상세 응답만 새 변환 원천으로 만든다."""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.ingestion.parse_cases import clean, write_json_atomically
from src.ingestion.refetch_case_details import (
    fetch_detail_response,
    missing_detail_fields,
    oc_from_environment,
    write_jsonl_atomically,
)


_IDENTITY_NORMALIZER = re.compile(r"[^0-9A-Za-z가-힣]")


def normalize_identity(value: object) -> str:
    """사건번호·법원명·선고일의 표현 차이를 제외하고 비교한다."""

    return _IDENTITY_NORMALIZER.sub("", clean(value)).lower()


def identity_values_match(field: str, expected: object, actual: object) -> bool:
    """공식 API와 후보의 같은 사건 표기 차이만 허용해 비교한다."""

    normalized_expected = normalize_identity(expected)
    normalized_actual = normalize_identity(actual)
    if normalized_expected == normalized_actual:
        return bool(normalized_expected)
    if field == "court_name":
        def normalize_court(value: str) -> str:
            return (
                value.replace("고등법원", "고법")
                .replace("지방법원", "지법")
                .replace("재판부", "")
            )

        return normalize_court(normalized_expected) == normalize_court(normalized_actual)
    if field == "case_number":
        expected_text = clean(expected).replace(" ", "")
        actual_text = clean(actual).replace(" ", "")
        return (
            bool(expected_text)
            and actual_text.startswith(expected_text)
            and len(actual_text) > len(expected_text)
            and actual_text[len(expected_text)] in "(,"
        )
    return False


@dataclass(frozen=True)
class VerifiedCandidate:
    """공식 상세 API 응답과 대조할 후보 판례의 최소 동일성 정보."""

    case_id: str
    case_number: str
    court_name: str
    decision_date: str

    def identity(self) -> tuple[str, str, str, str]:
        return tuple(
            normalize_identity(value)
            for value in (self.case_id, self.case_number, self.court_name, self.decision_date)
        )


@dataclass
class VerifiedSourceSummary:
    candidates: int = 0
    accepted: int = 0
    unavailable: list[dict[str, str]] = field(default_factory=list)
    identity_mismatches: list[dict[str, object]] = field(default_factory=list)
    information_missing: list[dict[str, object]] = field(default_factory=list)

    @property
    def can_publish(self) -> bool:
        """모든 후보를 완전한 공식 상세 응답으로 확보했을 때만 발행한다."""

        return (
            self.candidates > 0
            and self.accepted + len(self.information_missing) == self.candidates
            and not self.unavailable
            and not self.identity_mismatches
        )


def load_verified_candidates(paths: list[Path]) -> list[VerifiedCandidate]:
    """후보의 ID와 사건 동일성 정보를 보존하고, 충돌 후보를 거부한다."""

    candidates: list[VerifiedCandidate] = []
    by_case_id: dict[str, VerifiedCandidate] = {}
    for path in paths:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"후보 파일 {path} {lineno}번째 줄이 객체가 아님")
            candidate = VerifiedCandidate(
                case_id=clean(raw.get("case_id")),
                case_number=clean(raw.get("case_number")),
                court_name=clean(raw.get("court_name")),
                decision_date=clean(raw.get("decision_date")),
            )
            metadata_count = sum(bool(value) for value in (
                candidate.case_number, candidate.court_name, candidate.decision_date,
            ))
            if not candidate.case_id or metadata_count < 2:
                raise ValueError(
                    f"후보 파일 {path} {lineno}번째 줄은 case_id와 사건번호·법원·선고일 중 2개 이상이 필요함"
                )
            previous = by_case_id.setdefault(candidate.case_id, candidate)
            if previous.identity() != candidate.identity():
                raise ValueError(f"같은 case_id 후보의 사건 동일성 정보가 충돌함: {candidate.case_id}")
            if previous is candidate:
                candidates.append(candidate)
    return candidates


def response_identity_check(candidate: VerifiedCandidate, service: dict[str, object]) -> dict[str, object]:
    """공식 ID 일치와 보조 동일성 필드 두 개 이상 일치를 검증한다."""

    actual = {
        "case_id": clean(service.get("판례정보일련번호")),
        "case_number": clean(service.get("사건번호")),
        "court_name": clean(service.get("법원명")),
        "decision_date": clean(service.get("선고일자")),
    }
    expected = {
        "case_id": candidate.case_id,
        "case_number": candidate.case_number,
        "court_name": candidate.court_name,
        "decision_date": candidate.decision_date,
    }
    matched_fields = [
        field for field in ("case_number", "court_name", "decision_date")
        if expected[field] and identity_values_match(field, expected[field], actual[field])
    ]
    mismatches = {
        field: {"expected": expected[field], "actual": actual[field]}
        for field in expected
        if not identity_values_match(field, expected[field], actual[field])
    }
    return {
        "accepted": "case_id" not in mismatches and len(matched_fields) >= 2,
        "matched_fields": ["case_id", *matched_fields] if "case_id" not in mismatches else matched_fields,
        "mismatches": mismatches,
    }


def build_verified_source(
    candidates: list[VerifiedCandidate], *, oc: str, delay: float,
) -> tuple[list[dict[str, object]], VerifiedSourceSummary]:
    summary = VerifiedSourceSummary(candidates=len(candidates))
    records: list[dict[str, object]] = []
    for candidate in candidates:
        case_id = candidate.case_id
        try:
            service, reason = fetch_detail_response(case_id, oc)
        except Exception as error:
            service, reason = None, f"API 요청 실패: {error}"
        if service is None:
            summary.unavailable.append({
                "case_id": case_id,
                "source_url": f"https://www.law.go.kr/LSW/precInfoP.do?precSeq={case_id}",
                "reason": reason,
            })
        else:
            identity = response_identity_check(candidate, service)
            if not identity["accepted"]:
                summary.identity_mismatches.append({
                    "case_id": case_id,
                    "source_url": f"https://www.law.go.kr/LSW/precInfoP.do?precSeq={case_id}",
                    **identity,
                })
            else:
                missing = missing_detail_fields(service)
                if missing == ["판결요지"]:
                    summary.information_missing.append({
                        "case_id": case_id,
                        "case_number": clean(service.get("사건번호")),
                        "court_name": clean(service.get("법원명")),
                        "decision_date": clean(service.get("선고일자")),
                        "source_url": f"https://www.law.go.kr/LSW/precInfoP.do?precSeq={case_id}",
                        "missing_fields": missing,
                        "display_message": "판결요지가 없습니다. 원문을 확인해주세요.",
                    })
                elif missing:
                    summary.unavailable.append({
                        "case_id": case_id,
                        "source_url": f"https://www.law.go.kr/LSW/precInfoP.do?precSeq={case_id}",
                        "reason": f"공식 응답 필수 필드 누락: {', '.join(missing)}",
                    })
                else:
                    records.append({
                        "case_id": case_id,
                        "source_url": f"https://www.law.go.kr/LSW/precInfoP.do?precSeq={case_id}",
                        "service": service,
                    })
                    summary.accepted += 1
        if delay:
            time.sleep(delay)
    return records, summary


def publish_verified_source(
    *, records: list[dict[str, object]], summary: VerifiedSourceSummary,
    output_path: Path, report_path: Path,
) -> bool:
    """완전 수집일 때만 원천을 교체하고, 보고서는 항상 남긴다."""

    published = summary.can_publish
    write_json_atomically({**asdict(summary), "published": published}, report_path)
    if published:
        write_jsonl_atomically(records, output_path)
    return published


def main() -> int:
    parser = argparse.ArgumentParser(description="공개 API 검증 판례 상세 원천 생성")
    parser.add_argument("--candidates", required=True, nargs="+", help="후보 JSONL(복수 가능)")
    parser.add_argument("--output", required=True, help="검증된 공식 상세 원천 JSONL")
    parser.add_argument("--report", required=True, help="수집 불가 사유 보고서 JSON")
    parser.add_argument("--oc-env-file", default=None)
    parser.add_argument("--delay", type=float, default=0.1)
    args = parser.parse_args()
    candidate_paths = [Path(path) for path in args.candidates]
    try:
        candidates = load_verified_candidates(candidate_paths)
        records, summary = build_verified_source(
            candidates,
            oc=oc_from_environment(Path(args.oc_env_file) if args.oc_env_file else None),
            delay=args.delay,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"후보 파일을 검증할 수 없습니다: {error}")
        return 1
    output_path, report_path = Path(args.output), Path(args.report)
    published = publish_verified_source(
        records=records, summary=summary, output_path=output_path, report_path=report_path,
    )
    print(
        f"공식 검증 후보 {summary.candidates}건 -> 사용 가능 {summary.accepted}건 "
        f"· 정보 부족 {len(summary.information_missing)}건 · 수집 불가 {len(summary.unavailable)}건 "
        f"· 동일성 불일치 {len(summary.identity_mismatches)}건"
    )
    print(f"보고서: {report_path}")
    if not published:
        print("수집 실패 또는 동일성 불일치가 있어 기존 원천 파일을 변경하지 않았습니다.")
        return 1
    print(f"원천: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
