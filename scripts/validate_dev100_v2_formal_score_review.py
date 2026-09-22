"""Validate human-reviewed DEV100-v2 claim bindings for formal scoring.

``requirements.json`` intentionally describes expected claims without freezing a
claim-to-source mapping.  This command checks a reviewed CSV created by
``build_dev100_v2_scoring_worksheet.py``.  It reports readiness but never
edits the human-authored requirements or promotes an incomplete review.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "data/eval/dev100-v2/requirements.json"
WORKSHEET = ROOT / "tmp/dev100-v2-formal-score-review.csv"
VALID_ACTIVATIONS = {"active", "inactive"}


def expected_claims(requirements: list[dict]) -> dict[tuple[str, str], set[str]]:
    """Map each expected claim to its eligible evidence sources, including input."""
    expected: dict[tuple[str, str], set[str]] = {}
    for requirement in requirements:
        item = requirement["original_v2"]
        sources = {
            source_id
            for group in item.get("groups", [])
            for source_id in group.get("source_ids", [])
        }
        sources.update(
            requirement["source_id"]
            for requirement in item.get("non_retrieval_requirements", [])
            if requirement.get("source_id")
        )
        for claim in item.get("claims", []):
            expected[(requirement["qid"], claim["claim_id"])] = sources
    return expected


def validate_rows(rows: list[dict[str, str]], expected: dict[tuple[str, str], set[str]]) -> list[str]:
    """Return every blocker; an empty result means the bindings are review-ready."""
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.get("qid", ""), row.get("claim_id", ""))
        if key not in expected:
            errors.append(f"알 수 없는 기대 주장: {key[0]} / {key[1]}")
            continue
        if key in seen:
            errors.append(f"중복 기대 주장: {key[0]} / {key[1]}")
            continue
        seen.add(key)
        if row.get("review_status") != "approved":
            errors.append(f"{key[0]} / {key[1]}: 검토 승인이 필요합니다.")
        direct_source = row.get("direct_source_id", "")
        if direct_source not in expected[key]:
            errors.append(f"{key[0]} / {key[1]}: 직접 출처가 후보 목록에 없습니다.")
        for mode in ("question_only_activation", "context_diagnostic_activation"):
            if row.get(mode) not in VALID_ACTIVATIONS:
                errors.append(f"{key[0]} / {key[1]}: {mode}을 active 또는 inactive로 검토해야 합니다.")
    for qid, claim_id in expected.keys() - seen:
        errors.append(f"누락 기대 주장: {qid} / {claim_id}")
    return errors


def main() -> None:
    requirements = json.loads(REQUIREMENTS.read_text(encoding="utf-8"))
    expected = expected_claims(requirements)
    with WORKSHEET.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    errors = validate_rows(rows, expected)
    if errors:
        print(f"FORMAL_SCORE_READY=false blockers={len(errors)}")
        for error in errors[:20]:
            print(f"- {error}")
        if len(errors) > 20:
            print(f"- ... {len(errors) - 20} more")
        raise SystemExit(1)
    print(f"FORMAL_SCORE_READY=true claims={len(expected)}")


if __name__ == "__main__":
    main()
