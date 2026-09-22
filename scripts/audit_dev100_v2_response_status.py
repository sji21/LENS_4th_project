"""Compare DEV100-v2 delivery status with the human-authored expected answers.

The review2 source says every DEV item has a ``현재 정보로 반드시 답할 내용``
section.  It also says missing facts require a scoped or conditional answer,
not a whole-answer abstention.  This audit therefore checks only delivery
status; it deliberately does not pretend that status matching proves semantic
answer correctness.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "data/eval/dev100-v2/raw/01_최종정답_근거통합본.md"
HEADING = re.compile(r"^## (DEV-\d{3}) —", re.MULTILINE)
MUST_ANSWER = "### 현재 정보로 반드시 답할 내용"


def expected_answer_qids(text: str) -> set[str]:
    """Return only sections explicitly marked as requiring a present answer."""
    matches = list(HEADING.finditer(text))
    qids: set[str] = set()
    for index, match in enumerate(matches):
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if MUST_ANSWER in text[match.end():section_end]:
            qids.add(match.group(1))
    return qids


def audit(results: list[dict], expected_qids: set[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for result in results:
        qid = result["qid"]
        actual = result.get("answer", {}).get("status", "")
        expected = "answered" if qid in expected_qids else "not_scored"
        if expected == "answered" and actual == "answered":
            verdict = "status_matched"
        elif expected == "answered":
            verdict = "whole_answer_over_abstained"
        else:
            verdict = "not_scored"
        rows.append({"qid": qid, "expected_status": expected, "actual_status": actual, "status_verdict": verdict})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    expected_qids = expected_answer_qids(EXPECTED.read_text(encoding="utf-8"))
    results = [json.loads(line) for line in args.results.read_text(encoding="utf-8").splitlines() if line]
    rows = audit(results, expected_qids)
    if {row["qid"] for row in rows} != expected_qids:
        raise ValueError("실행 결과와 사람이 작성한 DEV100-v2 기대 답안의 문항 ID가 다릅니다.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"items": len(rows), **Counter(row["status_verdict"] for row in rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
