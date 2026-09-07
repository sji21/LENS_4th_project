"""국가법령정보 공동활용 API로 판례 상세 응답 누락분만 안전하게 재수집한다."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from src.ingestion.parse_cases import clean, write_json_atomically


REQUIRED_DETAIL_FIELDS = ("사건번호", "법원명", "선고일자", "사건명", "판결요지", "판례내용")
API_BASE = "https://www.law.go.kr/DRF/lawService.do"


@dataclass
class RefetchSummary:
    input_records: int = 0
    attempted: int = 0
    recovered: int = 0
    unavailable: list[dict[str, str]] = field(default_factory=list)

    @property
    def can_publish(self) -> bool:
        """재수집 대상이 모두 회복됐을 때만 새 원천을 발행한다."""

        return not self.unavailable


def oc_from_environment(env_file: Path | None) -> str:
    """환경 변수 또는 지정한 .env 파일에서 인증 식별자를 읽는다."""

    value = os.getenv("LAW_OPEN_API_OC", "").strip()
    if value:
        return value
    if env_file and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("LAW_OPEN_API_OC="):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("LAW_OPEN_API_OC가 없습니다. 환경 변수 또는 --oc-env-file을 확인하세요.")


def needs_detail_refetch(raw: object) -> bool:
    if not isinstance(raw, dict) or not clean(raw.get("case_id")):
        return False
    service = raw.get("service")
    if not isinstance(service, dict):
        return True
    return bool(missing_detail_fields(service))


def fetch_detail_response(case_id: str, oc: str) -> tuple[dict[str, object] | None, str]:
    """상세 API 응답을 보존한다. 필수 필드 검사는 호출자 정책에 맡긴다."""

    params = urllib.parse.urlencode({"OC": oc, "target": "prec", "ID": case_id, "type": "JSON"})
    request = urllib.request.Request(f"{API_BASE}?{params}", headers={"User-Agent": "LENS-data-repair/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    service = payload.get("PrecService")
    if not isinstance(service, dict):
        return None, "판례 상세 응답(PrecService)이 아님"
    return service, ""


def missing_detail_fields(service: dict[str, object]) -> list[str]:
    """검색 코퍼스에 필요한 공식 상세 필드의 누락 목록을 반환한다."""

    return [field for field in REQUIRED_DETAIL_FIELDS if not clean(service.get(field))]


def fetch_detail(case_id: str, oc: str) -> tuple[dict[str, object] | None, str]:
    """완전한 검색용 상세 응답만 반환하는 기존 복구 경로용 어댑터다."""

    service, reason = fetch_detail_response(case_id, oc)
    if service is None:
        return None, reason
    missing = missing_detail_fields(service)
    if missing:
        return None, f"공식 응답 필수 필드 누락: {', '.join(missing)}"
    return service, ""


def write_jsonl_atomically(records: list[object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def refetch_records(records: list[object], *, oc: str, delay: float) -> tuple[list[object], RefetchSummary]:
    result: list[object] = []
    summary = RefetchSummary(input_records=len(records))
    for raw in records:
        updated = raw
        if needs_detail_refetch(raw):
            assert isinstance(raw, dict)
            case_id = clean(raw.get("case_id"))
            summary.attempted += 1
            try:
                service, reason = fetch_detail(case_id, oc)
            except Exception as error:  # 네트워크·API 오류는 원본을 보존하고 보고한다.
                service, reason = None, f"API 요청 실패: {error}"
            if service is None:
                summary.unavailable.append({"case_id": case_id, "source_url": clean(raw.get("source_url")), "reason": reason})
            else:
                updated = {**raw, "service": service}
                summary.recovered += 1
            if delay:
                time.sleep(delay)
        result.append(updated)
    return result, summary


def publish_refetched_records(
    *, records: list[object], summary: RefetchSummary, output_path: Path, report_path: Path,
) -> bool:
    """완전 회복일 때만 결과를 교체하고, 실패 보고서는 항상 남긴다."""

    published = summary.can_publish
    write_json_atomically({**summary.__dict__, "published": published}, report_path)
    if published:
        write_jsonl_atomically(records, output_path)
    return published


def main() -> int:
    parser = argparse.ArgumentParser(description="누락된 공식 판례 상세 응답만 재수집")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True, help="원본과 다른 재수집 결과 JSONL 경로")
    parser.add_argument("--report", required=True)
    parser.add_argument("--id-mapping", default=None, help="{기존 case_id: 정식 precSeq} JSON 매핑")
    parser.add_argument("--oc-env-file", default=None, help="LAW_OPEN_API_OC가 있는 .env 경로")
    parser.add_argument("--delay", type=float, default=0.1, help="API 요청 사이 대기 시간(초)")
    args = parser.parse_args()
    input_path, output_path, report_path = Path(args.input), Path(args.output), Path(args.report)
    if input_path.resolve() == output_path.resolve():
        parser.error("--output은 원본 보존을 위해 --input과 달라야 합니다.")
    records = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.id_mapping:
        mappings = json.loads(Path(args.id_mapping).read_text(encoding="utf-8"))
        for raw in records:
            if isinstance(raw, dict) and str(raw.get("case_id") or "") in mappings:
                canonical_id = str(mappings[str(raw["case_id"])])
                raw["case_id"] = canonical_id
                raw["source_url"] = f"https://www.law.go.kr/LSW/precInfoP.do?precSeq={canonical_id}"
    updated, summary = refetch_records(records, oc=oc_from_environment(Path(args.oc_env_file) if args.oc_env_file else None), delay=args.delay)
    published = publish_refetched_records(
        records=updated, summary=summary, output_path=output_path, report_path=report_path,
    )
    print(f"재수집: 대상 {summary.attempted}건 · 회복 {summary.recovered}건 · 미회복 {len(summary.unavailable)}건")
    print(f"보고서: {report_path}")
    if not published:
        print("재수집 실패가 있어 기존 결과 파일을 변경하지 않았습니다.")
        return 1
    print(f"결과: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
