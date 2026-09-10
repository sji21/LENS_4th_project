"""Scope and data-integrity regressions for the offline DEV v2 diagnostic."""
import copy
import json
import shutil

import pytest

from scripts.dev100_v2.report import (
    DEFAULT_DATASET, check_expected, classify, diagnose, load_dataset,
    read_json, sha256, validate_data, write_results,
)


@pytest.fixture
def inputs():
    return [read_json(DEFAULT_DATASET / name) for name in (
        "questions.json", "diagnostic-plan.json", "reference-run.json", "source-registry.json")]


def test_public_bundle_replays_all_expected_outputs():
    plan, reference = load_dataset(DEFAULT_DATASET)
    results = diagnose(plan, reference, sha256(DEFAULT_DATASET / "diagnostic-plan.json"))
    check_expected(DEFAULT_DATASET, results)
    assert sum(results["summary.json"]["counts"]["context_diagnostic"].values()) == 100


@pytest.mark.parametrize("mutation,error", [
    ("duplicate_question", "Duplicate question IDs"),
    ("duplicate_run", "Duplicate saved input"),
    ("missing_run", "Missing saved inputs"),
    ("wrong_query", "Query hash mismatch"),
    ("changed_body", "Returned body hash mismatch"),
    ("wrong_branch", "Wrong branch article"),
    ("wrong_law", "Source/target law article mismatch"),
    ("invalid_rank", "Invalid ranks"),
    ("unknown_source", "Unknown primary source"),
])
def test_rejects_corrupt_or_misaligned_reference_data(inputs, mutation, error):
    questions, plan, reference, sources = copy.deepcopy(inputs)
    if mutation == "duplicate_question":
        questions.append(questions[0])
    elif mutation == "duplicate_run":
        reference["results"].append(reference["results"][0])
    elif mutation == "missing_run":
        reference["results"].pop()
    elif mutation == "wrong_query":
        reference["results"][0]["query_sha256"] = "0" * 64
    elif mutation == "changed_body":
        reference["results"][0]["laws"][0]["body_sha256"] = "0" * 64
    elif mutation == "wrong_branch":
        target = next(p for p in plan if p["qid"] == "DEV-091")["law_targets"][0]
        target["article_anchor"] = "공공주택특별법-제49조"
    elif mutation == "wrong_law":
        target = next(p for p in plan if p["qid"] == "DEV-001")["law_targets"][0]
        assert target["source_id"] == "C114"
        target["article_anchor"] = "형법-제114조"
    elif mutation == "invalid_rank":
        reference["results"][0]["laws"][0]["rank"] = 2
    elif mutation == "unknown_source":
        plan[0]["primary_source_ids"].append("UNKNOWN")
    with pytest.raises(ValueError, match=error):
        validate_data(questions, plan, reference, sources)


def test_tampered_file_rejected_before_replay(tmp_path):
    dataset = tmp_path / "dataset"
    shutil.copytree(DEFAULT_DATASET, dataset)
    with (dataset / "questions.json").open("a", encoding="utf-8") as f:
        f.write(" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_dataset(dataset)


@pytest.mark.parametrize("name", [
    "01_최종정답_근거통합본.md", "02_리뷰1대비_변경표.md",
    "03_미확인_판단보류목록.md", "04_실제검색_원문확인기록.md",
    "05_리뷰2_최소보완_명제별근거표.md", "06_최소보완_검색확인기록.md",
    "검토진행.md",
])
def test_missing_raw_file_and_manifest_entry_rejected(tmp_path, name):
    dataset = tmp_path / "dataset"
    shutil.copytree(DEFAULT_DATASET, dataset)
    relative = "raw/" + name
    (dataset / relative).unlink()
    manifest = read_json(dataset / "manifest.json")
    del manifest["files"][relative]
    (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Manifest omits required files"):
        load_dataset(dataset)


@pytest.mark.parametrize("targets,available,ranked,historical,expected", [
    (set(), set(), [], False, "고정 필수 법령 목표 없음"),
    ({"a"}, {"a"}, ["a"], True, "시점별 근거 별도 확인"),
    ({"a", "b"}, {"a"}, [], False, "필수 조문 일부 미보유"),
    ({"a"}, set(), [], False, "필수 조문 전부 미보유"),
    ({"a", "b"}, {"a", "b"}, ["a", "b"], False, "전부 보유·TOP3 전부 반환"),
    ({"a"}, {"a"}, ["b", "c", "d", "a"], False, "전부 보유·TOP5만 전부 반환"),
    ({"a"}, {"a"}, ["b", "c", "d"], False, "전부 보유·TOP5 검색 누락"),
])
def test_categories_do_not_turn_missing_information_into_success(targets, available, ranked, historical, expected):
    assert classify(targets, available, ranked, historical) == expected


def test_supplement_and_branch_articles_are_preserved(inputs):
    _, plan, _, sources = inputs
    by_id = {p["qid"]: p for p in plan}
    assert by_id["DEV-060"]["primary_source_ids"] == ["C390"]
    assert by_id["DEV-071"]["primary_source_ids"] == ["C654", "C615"]
    for qid in ["DEV-082", "DEV-083", "DEV-084", "DEV-085", "DEV-093", "DEV-100"]:
        assert not by_id[qid]["law_targets"]
    assert by_id["DEV-091"]["law_targets"][0]["article_anchor"].endswith("제49조의2")
    assert by_id["DEV-092"]["law_targets"][0]["article_anchor"].endswith("제49조의3")
    for item in read_json(DEFAULT_DATASET / "requirements.json"):
        assert item["formal_whole_item_score_ready"] is False


def test_partial_data_and_retrieval_miss_are_both_retained():
    plan = [{"qid": f"DEV-{n:03}", "law_targets": [{"article_anchor": "a"}, {"article_anchor": "b"}],
             "historical_review_required": False, "non_flat_law_targets": []} for n in range(1, 101)]
    reference = {"inventory": [{"article_anchor": "a"}], "results": [
        {"qid": p["qid"], "mode": mode, "laws": [], "cases": [], "guides": []}
        for p in plan for mode in ("question_only", "context_diagnostic")]}
    result = diagnose(plan, reference, "test")["article-diagnostics.json"][0]
    assert result["absent"] == ["b"]
    assert result["modes"]["context_diagnostic"]["retrievable_but_not_top5"] == ["a"]
    assert result["whole_item_score"] is None


def test_output_does_not_overwrite_or_write_into_dataset(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    for out in (dataset, dataset / "new", tmp_path):
        with pytest.raises(ValueError, match="outside dataset"):
            write_results(out, dataset, {})
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="new output directory"):
        write_results(existing, dataset, {})
