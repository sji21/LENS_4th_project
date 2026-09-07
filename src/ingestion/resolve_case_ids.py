"""후보 메타데이터로 국가법령정보센터의 정식 판례 일련번호를 재확인한다."""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.ingestion.refetch_case_details import oc_from_environment


API_BASE = "https://www.law.go.kr/DRF/lawSearch.do"
_NON_ALNUM = re.compile(r"[^0-9A-Za-z가-힣]")
MISSING_DETAIL_RECORD_FIELDS = {
    "case_number", "court_name", "decision_date", "case_name", "holding", "summary", "full_text",
}


def normalized(value: object) -> str:
    return _NON_ALNUM.sub("", str(value or "")).lower()


@dataclass(frozen=True)
class Candidate:
    old_case_id: str
    case_number: str
    court_name: str
    decision_date: str


@dataclass
class ResolutionSummary:
    requested: int = 0
    resolved: dict[str, str] = field(default_factory=dict)
    unresolved: list[dict[str, object]] = field(default_factory=list)


def search_by_case_number(case_number: str, oc: str) -> list[dict[str, object]]:
    params = urllib.parse.urlencode({
        "OC": oc, "target": "prec", "type": "JSON", "mobileYn": "Y",
        "nb": case_number, "display": "100",
    })
    request = urllib.request.Request(f"{API_BASE}?{params}", headers={"User-Agent": "LENS-data-repair/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    search = payload.get("PrecSearch", {})
    results = search.get("prec", []) if isinstance(search, dict) else []
    if isinstance(results, dict):
        results = [results]
    return results if isinstance(results, list) else []


def exact_matches(candidate: Candidate, results: list[dict[str, object]]) -> list[dict[str, object]]:
    matches = [item for item in results if normalized(item.get("사건번호")) == normalized(candidate.case_number)]
    if candidate.court_name:
        matches = [item for item in matches if normalized(item.get("법원명")) == normalized(candidate.court_name)]
    if candidate.decision_date:
        matches = [item for item in matches if normalized(item.get("선고일자")) == normalized(candidate.decision_date)]
    return matches


def resolve_candidates(candidates: list[Candidate], *, oc: str) -> ResolutionSummary:
    summary = ResolutionSummary(requested=len(candidates))
    for candidate in candidates:
        try:
            matches = exact_matches(candidate, search_by_case_number(candidate.case_number, oc))
        except Exception as error:
            summary.unresolved.append({**asdict(candidate), "reason": f"목록 API 요청 실패: {error}"})
            continue
        sequences = {str(item.get("판례일련번호") or "").strip() for item in matches}
        sequences.discard("")
        if len(sequences) == 1:
            summary.resolved[candidate.old_case_id] = sequences.pop()
        elif not sequences:
            summary.unresolved.append({**asdict(candidate), "reason": "사건번호·법원·선고일 정확 일치 결과 없음"})
        else:
            summary.unresolved.append({**asdict(candidate), "reason": "정확 일치 판례가 여러 건"})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="후보 사건번호에서 정식 판례 일련번호를 재확인")
    parser.add_argument("--candidates", required=True, nargs="+", help="후보 JSONL(복수 가능)")
    parser.add_argument("--error-report", required=True, help="parse_cases의 error_records가 있는 JSON 보고서")
    parser.add_argument("--output", required=True, help="{old_case_id: precSeq} 매핑 JSON")
    parser.add_argument("--report", required=True)
    parser.add_argument("--oc-env-file", default=None)
    args = parser.parse_args()

    error_report = json.loads(Path(args.error_report).read_text(encoding="utf-8"))
    wanted_ids = {
        str(item.get("case_id") or "")
        for item in error_report.get("error_records", [])
        if MISSING_DETAIL_RECORD_FIELDS.issubset(set(item.get("missing_fields", [])))
    }
    candidates_by_id: dict[str, Candidate] = {}
    for candidate_path in args.candidates:
        for line in Path(candidate_path).read_text(encoding="utf-8").splitlines():
            raw = json.loads(line)
            old_case_id = str(raw.get("case_id") or "")
            if old_case_id in wanted_ids and raw.get("case_number"):
                candidates_by_id.setdefault(old_case_id, Candidate(
                    old_case_id=old_case_id,
                    case_number=str(raw["case_number"]),
                    court_name=str(raw.get("court_name") or ""),
                    decision_date=str(raw.get("decision_date") or ""),
                ))
    candidates = list(candidates_by_id.values())
    summary = resolve_candidates(candidates, oc=oc_from_environment(Path(args.oc_env_file) if args.oc_env_file else None))
    Path(args.output).write_text(json.dumps(summary.resolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.report).write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ID 재매핑: 요청 {summary.requested}건 · 성공 {len(summary.resolved)}건 · 미해결 {len(summary.unresolved)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
