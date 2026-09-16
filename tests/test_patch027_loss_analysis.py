from copy import deepcopy
import json
import shutil

import pytest

from scripts import patch027_loss_analysis as analysis
from scripts.patch027_context_tuning import civil_select


def sample(seed=()):
    return {"civil_seed": list(seed), "civil": {
        "bm25_context": ["a", "b", "c", "d"],
        "dense_context": ["a", "b", "d", "c"],
    }}


def test_seedless_counterfactual_changes_only_third_choice():
    row = sample()
    anchors = {x: x for x in "abcd"}
    assert civil_select(row, "context_reference", anchors, {})[0] == ["a", "b", "d"]
    assert analysis.select_seedless_rrf(row, anchors, {}) == ["a", "b", "c"]
    row["qid"] = "arbitrary-id"
    assert analysis.select_seedless_rrf(row, anchors, {}) == ["a", "b", "c"]


@pytest.mark.parametrize("seed,graph", [(["d"], {}), ([], {"a": {"d"}, "d": {"a"}})])
def test_seed_and_reference_choices_are_preserved(seed, graph):
    row = sample(seed)
    anchors = {x: x for x in "abcd"}
    before = deepcopy(row)
    assert analysis.select_seedless_rrf(row, anchors, graph) == civil_select(
        row, "context_reference", anchors, graph
    )[0]
    assert row == before


def test_alignment_uses_identity_not_capture_order():
    a = {"qid": "A", "mode": "question_only"}
    b = {"qid": "A", "mode": "context_diagnostic"}
    assert analysis.align([b, a], [a, b]) == [a, b]
    for rows, reference in (([a, a], [a, b]), ([a, b], [a, a]), ([a], [a, b])):
        with pytest.raises(ValueError):
            analysis.align(rows, reference)


@pytest.fixture(scope="module")
def verified():
    return analysis.check()


def test_frozen_losses_are_deduplicated_and_traceable(verified):
    counts = verified["counts"]
    assert (counts["returned_loss_inputs"], counts["top3_loss_inputs"],
            counts["overlap_inputs"], counts["union_inputs"]) == (8, 7, 4, 11)
    assert counts["complete_to_incomplete"] == 5
    assert counts["all_lost_targets_in_candidate_union"]
    assert counts["returned_losses_already_absent_untuned"] == 8
    improved = next(r for r in verified["items"] if r["qid"] == "DEV-088")
    assert not improved["complete_operating"] and improved["complete_current"]
    assert improved["top3_losses"] and not improved["returned_losses"]


def test_counterfactual_reports_both_recovery_and_new_loss(verified):
    cf = verified["counterfactual"]
    assert len(cf["changed_inputs"]) == 77
    gained = {(r["qid"], r["mode"], target) for r in cf["changed_inputs"] for target in r["gained_targets"]}
    lost = {(r["qid"], r["mode"], target) for r in cf["changed_inputs"] for target in r["lost_targets"]}
    assert gained == {("DEV-059", "question_only", "민법-제626조"),
                      ("DEV-064", "context_diagnostic", "민법-제105조")}
    assert lost == {("DEV-058", "context_diagnostic", "민법-제627조")}
    assert len(cf["losses_vs_operating"]) == 8
    assert len(cf["new_required_civil_articles"]) == 9


@pytest.mark.parametrize("rehash", [False, True])
def test_modified_analysis_is_rejected(tmp_path, rehash, verified):
    dest = tmp_path / "bundle"
    shutil.copytree(analysis.BUNDLE, dest)
    data = deepcopy(verified)
    data["counterfactual"]["losses_vs_current"] = []
    (dest / "analysis.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if rehash:
        (dest / "manifest.json").write_text(json.dumps({"analysis.json": analysis.sha(dest / "analysis.json")}), encoding="utf-8")
    with pytest.raises(ValueError, match="Loss analysis"):
        analysis.check(dest)
