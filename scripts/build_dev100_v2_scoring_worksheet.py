"""Create an auditable review worksheet before DEV100-v2 formal scoring.

The requirements deliberately leave claim-to-source bindings unresolved.  This
tool never promotes an item to formal-score-ready; reviewers must explicitly
approve every claim and mode branch in the generated worksheet first.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "data/eval/dev100-v2/requirements.json"
OUT = ROOT / "tmp/dev100-v2-formal-score-review.csv"


def _candidate_sources(item: dict) -> list[str]:
    """List candidates only; a reviewer must make the final direct binding."""
    seen: list[str] = []
    for group in item.get("groups", []):
        for source_id in group.get("source_ids", []):
            if source_id not in seen:
                seen.append(source_id)
    for requirement in item.get("non_retrieval_requirements", []):
        source_id = requirement.get("source_id", "")
        if source_id and source_id not in seen:
            seen.append(source_id)
    return seen


def _single_required_source(item: dict) -> str:
    """Return an unambiguous required-source candidate, never an approval."""
    required: list[str] = []
    for group in item.get("groups", []):
        if group.get("role") == "required":
            required.extend(source for source in group.get("source_ids", []) if source != "INPUT")
    required = list(dict.fromkeys(required))
    return required[0] if len(required) == 1 else ""


def build_rows(requirements: list[dict]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for requirement in requirements:
        item = requirement["original_v2"]
        candidates = ";".join(_candidate_sources(item))
        proposed = _single_required_source(item)
        for claim in item.get("claims", []):
            rows.append({
                "qid": requirement["qid"],
                "claim_id": claim["claim_id"],
                "claim_text": claim["text"],
                "candidate_source_ids": candidates,
                "proposed_direct_source_id": proposed,
                "direct_source_id": "",
                "question_only_activation": "review_required",
                "context_diagnostic_activation": "review_required",
                "review_status": "candidate_ready" if proposed else "unreviewed",
                "reviewer_note": "",
            })
    return rows


def main() -> None:
    requirements = json.loads(REQUIREMENTS.read_text(encoding="utf-8"))
    rows = build_rows(requirements)
    if len(requirements) != 100 or len(rows) != 349:
        raise ValueError("DEV100-v2 문항 또는 기대 주장 수가 예상과 다릅니다.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(OUT)
    print(f"items={len(requirements)} claims={len(rows)}")


if __name__ == "__main__":
    main()
