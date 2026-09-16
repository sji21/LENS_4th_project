"""Independent regressions for paired labels, budget limits, and trace scope."""
import json
from pathlib import Path
import unicodedata

import pytest

from scripts import patch042_gap_analysis as gap


ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def normalized(value):
    return "".join(unicodedata.normalize("NFC", value).split())


@pytest.fixture(scope="module")
def independent_rows():
    plan = {item["qid"]: item for item in
            read("data/eval/dev100-v2/diagnostic-plan.json")}
    result = {}
    for row in read("data/eval/patch041-rebuilt/capture/rows.json"):
        item = plan.get(row["qid"])
        if (item is None or item.get("historical_review_required")
                or not item["law_targets"]):
            continue
        targets = {normalized(target["article_anchor"])
                   for target in item["law_targets"]}
        returned = {normalized(evidence["article_id"])
                    for channel in ("laws", "civil_laws")
                    for evidence in row["result"][channel]}
        result[row["qid"], row["mode"]] = {
            "targets": targets, "missing": targets - returned,
        }
    return result


@pytest.fixture(scope="module")
def analysis():
    return gap.check()


def test_exclusive_failure_labels_follow_question_and_context(analysis, independent_rows):
    missing = {
        mode: {qid for (qid, row_mode), row in independent_rows.items()
               if row_mode == mode and row["missing"]}
        for mode in ("question_only", "context_diagnostic")
    }
    questions = missing["question_only"] - missing["context_diagnostic"]
    contexts = missing["context_diagnostic"] - missing["question_only"]
    assert questions == {"DEV-010", "DEV-012", "DEV-016", "DEV-019", "DEV-053"}
    assert contexts == {"DEV-001", "DEV-013", "DEV-027", "DEV-034", "DEV-060"}
    for key, expected in (("question_only_missing_only", questions),
                          ("context_diagnostic_missing_only", contexts)):
        assert analysis["summary"][key] == {"count": len(expected), "qids": sorted(expected)}


def test_return_capacity_and_consumption_capacity_are_distinct(analysis, independent_rows):
    for mode in ("question_only", "context_diagnostic"):
        rows = {qid: row for (qid, row_mode), row in independent_rows.items()
                if row_mode == mode}
        counts = {qid: (sum(not target.startswith("민법-") for target in row["targets"]),
                        sum(target.startswith("민법-") for target in row["targets"]))
                  for qid, row in rows.items()}
        returned_overflow = {qid for qid, (general, civil) in counts.items()
                             if general > 5 or civil > 3}
        consumed_overflow = {qid for qid, (general, civil) in counts.items()
                             if general > 3 or civil > 3}
        assert returned_overflow == set()
        assert consumed_overflow == {"DEV-003", "DEV-025", "DEV-026", "DEV-028", "DEV-038"}
        actual = analysis["capacity_analysis"]["by_mode"][mode]
        assert actual["returned_budget_overflow_inputs"] == len(returned_overflow)
        assert actual["consumed_budget_overflow_inputs"] == len(consumed_overflow)
        assert actual["consumed_budget_overflow_qids"] == sorted(consumed_overflow)


def test_missing_trace_covers_exact_dev_targets_without_civil_review(analysis, independent_rows):
    expected = {(qid, mode, target) for (qid, mode), row in independent_rows.items()
                for target in row["missing"]}
    actual = []
    for row in analysis["missing_inputs"]:
        for trace in row["candidate_visibility"]["missing_target_trace"]:
            assert trace["track"] == "dev100"
            assert trace["qid"] == row["qid"] and trace["mode"] == row["mode"]
            actual.append((trace["qid"], trace["mode"], trace["target"]))
    assert len(actual) == len(set(actual)) == len(expected) == 83
    assert set(actual) == expected
