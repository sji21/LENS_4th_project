"""Evaluation identities and complete-evidence checks, without any model."""
from copy import deepcopy
import json

import pytest

from scripts import patch051_companion_eval as evaluator


def _evidence(article, chunk=None):
    return {"article_id": article, "chunk_id": chunk or article + "#0"}


def _row(qid="q", scope="general"):
    result = {"laws": [], "civil_laws": [], "cases": [], "guides": []}
    return {"qid": qid, "mode": "question_only", "group": "test", "track": "test",
            "scope": scope, "score": True, "targets": ["주택임대차보호법-제3조", "주택임대차보호법-제3조의2"],
            "results": {"3": deepcopy(result), "5": deepcopy(result)}, "model_calls": 0}


def _write_capture(directory, rows):
    directory.mkdir()
    snapshot = {key: {} for key in ("criteria", "payload", "model", "logical_indices", "settings", "public_inputs", "tax_selection")}
    for name, value in (("rows.json", rows), ("audit.json", {"before": snapshot, "after": snapshot})):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    manifest = {name: evaluator.base.sha(directory / name) for name in ("rows.json", "audit.json")}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_case_gold_uses_metadata_identity_instead_of_chunk_prefix():
    row = _row(scope="case")
    row["results"]["3"]["cases"] = [_evidence("CASE-2025DA210305", "case:CASE-2025DA210305#0")]
    assert evaluator.retrieved(row, 3) == ["CASE-2025DA210305"]


def test_law_scope_does_not_count_case_or_guide_mentions_as_law_evidence():
    row = _row()
    row["results"]["3"].update(
        laws=[_evidence("주택임대차보호법-제3조")],
        civil_laws=[_evidence("민법-제623조")],
        cases=[_evidence("주택임대차보호법-제3조의2")],
        guides=[_evidence("주택임대차보호법-제3조의2")],
    )
    assert evaluator.retrieved(row, 3) == ["주택임대차보호법-제3조"]
    row["scope"] = "combined"
    assert evaluator.retrieved(row, 3) == ["주택임대차보호법-제3조", "민법-제623조"]


def test_all_required_differs_from_any_hit_and_reports_individual_target_loss(tmp_path):
    old = [_row(str(index)) for index in range(313)]
    for row in old[2:]:
        row["score"] = False
    for row in old[:2]:
        for result in row["results"].values():
            result["laws"] = [_evidence("주택임대차보호법-제3조")]
    new = deepcopy(old)
    for result in new[0]["results"].values():
        result["laws"].append(_evidence("주택임대차보호법-제3조의2"))
    for result in new[1]["results"].values():
        result["laws"] = [_evidence("주택임대차보호법-제3조의2")]
    before, after = tmp_path / "before", tmp_path / "after"
    _write_capture(before, old)
    _write_capture(after, new)
    output = tmp_path / "comparison.json"

    evaluator.compare(before, after, output)
    report = json.loads(output.read_text(encoding="utf-8"))
    for group in report["groups"].values():
        assert group == {"n": 2, "before_any": 2, "after_any": 2, "before_all": 0, "after_all": 1}
    assert len(report["required_target_losses"]) == 2
    assert {row["qid"] for row in report["required_target_losses"]} == {"1"}
    assert all(row["lost"] == ["주택임대차보호법-제3조"] for row in report["required_target_losses"])

    new[0]["targets"] = ["주택임대차보호법-제3조"]
    changed_gold = tmp_path / "changed_gold"
    _write_capture(changed_gold, new)
    with pytest.raises(ValueError, match="Input/gold drift"):
        evaluator.compare(before, changed_gold, output)
