"""Block accidental reuse of an exposed case-retrieval evaluation set.

This gate performs deterministic identity checks only.  It intentionally does
not claim to detect semantic overlap; that still requires a separate review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


QUESTION_KEYS = ("question", "query", "input")
ID_KEYS = ("qid", "question_id", "id")


def _records(path: Path, content: bytes) -> list[Any]:
    text = content.decode("utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    value = json.loads(text)
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("questions", "items", "records", "data"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
    return [value]


def _normalize_question(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _dataset(path: Path) -> dict[str, Any]:
    """Read once so parsed identities and the reported hash share one snapshot."""
    content = path.read_bytes()
    records = _records(path, content)
    qids: list[str] = []
    questions: list[str] = []
    invalid_records: list[int] = []

    for index, item in enumerate(records):
        if not isinstance(item, dict):
            invalid_records.append(index)
            continue
        qid = next(
            (str(item[key]).strip() for key in ID_KEYS if isinstance(item.get(key), (str, int)) and str(item[key]).strip()),
            None,
        )
        question = next(
            (item[key].strip() for key in QUESTION_KEYS if isinstance(item.get(key), str) and item[key].strip()),
            None,
        )
        if qid is None or question is None:
            invalid_records.append(index)
            continue
        qids.append(qid)
        questions.append(_normalize_question(question))

    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(content).hexdigest(),
        "record_count": len(records),
        "valid_question_count": len(qids),
        "invalid_record_indices": invalid_records,
        "duplicate_qids": sorted({value for value in qids if qids.count(value) > 1}),
        "duplicate_normalized_questions": sorted(
            {value for value in questions if questions.count(value) > 1}
        ),
        "qids": set(qids),
        "questions": set(questions),
    }


def _public_dataset_report(dataset: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in dataset.items() if key not in {"qids", "questions"}}


def evaluate(candidate: Path, prior: list[Path], *, expected_candidate_count: int | None = None) -> dict[str, Any]:
    candidate_data = _dataset(candidate)
    candidate_ids = candidate_data["qids"]
    candidate_questions = candidate_data["questions"]
    candidate_sha = candidate_data["sha256"]
    prior_reports = []
    all_prior_ids: set[str] = set()
    all_prior_questions: set[str] = set()
    identical_file = False
    prior_valid = bool(prior)

    for path in prior:
        prior_data = _dataset(path)
        prior_ids = prior_data["qids"]
        prior_questions = prior_data["questions"]
        sha = prior_data["sha256"]
        identical_file = identical_file or sha == candidate_sha
        prior_valid = prior_valid and bool(prior_ids) and not prior_data["invalid_record_indices"]
        all_prior_ids.update(prior_ids)
        all_prior_questions.update(prior_questions)
        prior_reports.append(_public_dataset_report(prior_data))

    qid_overlap = sorted(candidate_ids & all_prior_ids)
    question_overlap = sorted(candidate_questions & all_prior_questions)
    candidate_valid = (
        bool(candidate_questions)
        and not candidate_data["invalid_record_indices"]
        and not candidate_data["duplicate_qids"]
        and not candidate_data["duplicate_normalized_questions"]
        and (expected_candidate_count is None or candidate_data["record_count"] == expected_candidate_count)
    )
    passed = candidate_valid and prior_valid and not identical_file and not qid_overlap and not question_overlap
    return {
        "schema": "lens-case-fresh-eval-gate-v1",
        "candidate": _public_dataset_report(candidate_data),
        "prior_sets": prior_reports,
        "checks": {
            "candidate_format_complete": candidate_valid,
            "all_prior_formats_complete_and_nonempty": prior_valid,
            "expected_candidate_count": expected_candidate_count,
            "file_sha_is_new": not identical_file,
            "qid_overlap_count": len(qid_overlap),
            "exact_normalized_question_overlap_count": len(question_overlap),
        },
        "qid_overlap": qid_overlap,
        "exact_normalized_question_overlap": question_overlap,
        "pass": passed,
        "limitations": [
            "문자열 정규화 기반 동일 문항만 검사하며 의미 중복은 판정하지 않는다.",
            "--prior로 지정하지 않은 과거 평가셋의 재사용 여부는 검사할 수 없다.",
            "Gold의 공식 출처·허용 대안·적용 조건과 표본 독립성은 별도 교차검토가 필요하다.",
        ],
    }


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as dst:
        dst.write(payload)
        dst.flush()
        os.fsync(dst.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--prior", type=Path, action="append", required=True)
    parser.add_argument("--expected-candidate-count", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = evaluate(
        args.candidate,
        args.prior,
        expected_candidate_count=args.expected_candidate_count,
    )
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        _write_exclusive(args.output, payload)
    except FileExistsError:
        parser.error(f"output already exists: {args.output}")
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
