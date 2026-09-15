"""Independent checks for the rebuilt-corpus measurement contract."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import patch041_retrieval_eval as evaluation


ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def frozen_capture():
    """Use stored evidence, never a model, to independently check the scorer."""
    jobs = {(j["qid"], j["mode"]): j for j in evaluation.build_jobs()}
    anchors = read("data/eval/patch027-context-tuning/audit.json")["anchors"]
    current = []
    for old in read("data/eval/patch027-final-test/rows.json"):
        job = jobs[old["qid"], old["mode"]]
        result = deepcopy(old["record_lookup"])
        for channel, evidence in result.items():
            for item in evidence:
                item["article_id"] = (anchors[item["chunk_id"]]
                                      if channel in ("laws", "civil_laws")
                                      else item["chunk_id"])
        current.append({"qid": old["qid"], "mode": old["mode"],
                        "group": job["group"], "query_sha256": old["query_sha256"],
                        "result": result})
    available = read("data/eval/patch027-full/capture/audit.json")["available_after"]
    return current, available


def assess(frozen_capture, rows=None, available=None):
    original, inventory = frozen_capture
    return evaluation.analyze(original if rows is None else rows,
                              available_articles=inventory if available is None else available)


def test_jobs_preserve_all_frozen_input_identities_and_hashes():
    jobs = evaluation.build_jobs()
    previous = read("data/eval/patch015-baseline/capture/results.json")
    expected = {(r["qid"], r["mode"]): r for r in previous}
    assert len(jobs) == len({(r["qid"], r["mode"]) for r in jobs}) == 235
    assert Counter(j["mode"] for j in jobs) == {
        "question_only": 100, "context_diagnostic": 100, "question_context": 35}
    for job in jobs:
        old = expected[job["qid"], job["mode"]]
        assert job["query"] == old["query"]
        assert hashlib.sha256(job["query"].encode()).hexdigest() == old["query_sha256"] == job["query_sha256"]


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "query_hash", "qid", "mode", "group"])
def test_invalid_input_identity_is_rejected(frozen_capture, corruption):
    rows = deepcopy(frozen_capture[0])
    if corruption == "missing":
        rows.pop()
    elif corruption == "duplicate":
        rows[-1] = deepcopy(rows[0])
    elif corruption == "query_hash":
        rows[0]["query_sha256"] = "0" * 64
    elif corruption == "qid":
        rows[0]["qid"] = "DEV-999"
    elif corruption == "mode":
        rows[0]["mode"] = "question_context"
    else:
        rows[0]["group"] = "wrong_group"
    with pytest.raises(ValueError):
        assess(frozen_capture, rows)


def test_frozen_scoring_keeps_denominators_and_channel_cutoffs(frozen_capture):
    report = assess(frozen_capture)
    for mode, general_hits, civil_hits in (
        ("question_only", (18, 34, 36), (18, 23)),
        ("context_diagnostic", (17, 33, 40), (14, 21)),
    ):
        group = report["groups"][mode]
        assert group["n"] == 100
        assert group["union_all_required"] == {"hits": 43, "n": 75}
        assert group["categories"]["no_fixed_target"] == 23
        assert group["categories"]["historical_review"] == 2
        general = group["channel_metrics"]["general"]["all_target_items"]
        civil = group["channel_metrics"]["civil"]["all_target_items"]
        assert general["n"] == 47
        assert civil["n"] == 32
        for k, hits in zip((1, 3, 5), general_hits):
            assert general[f"hit@{k}"] == pytest.approx(hits / 47)
        for k, hits in zip((1, 3), civil_hits):
            assert civil[f"hit@{k}"] == pytest.approx(hits / 32)
        assert civil.get("hit@5") is None
    assert report["groups"]["required_law"]["union_all_required"] == {"hits": 28, "n": 29}
    assert report["groups"]["scope_provisional"]["n"] == 1
    assert report["groups"]["diagnostic_only"]["union_all_required"] == {"hits": 0, "n": 0}


def test_absent_corpus_targets_do_not_shrink_official_denominators(frozen_capture):
    rows = deepcopy(frozen_capture[0])
    for row in rows:
        row["result"]["laws"] = []
        row["result"]["civil_laws"] = []
    report = assess(frozen_capture, rows, available=[])
    for mode in ("question_only", "context_diagnostic"):
        group = report["groups"][mode]
        assert group["union_all_required"] == {"hits": 0, "n": 75}
        assert group["categories"]["data_missing_all"] == 75
        assert group["channel_metrics"]["general"]["all_target_items"]["n"] == 47
        assert group["channel_metrics"]["civil"]["all_target_items"]["n"] == 32


@pytest.mark.parametrize("corruption", ["civil_in_general", "general_in_civil", "over_budget", "duplicate_article", "rank"])
def test_invalid_channel_evidence_is_rejected(frozen_capture, corruption):
    rows = deepcopy(frozen_capture[0])
    result = rows[0]["result"]
    if corruption == "civil_in_general":
        result["laws"][0] = deepcopy(result["civil_laws"][0])
    elif corruption == "general_in_civil":
        result["civil_laws"][0] = deepcopy(result["laws"][0])
    elif corruption == "over_budget":
        result["civil_laws"].append(deepcopy(result["civil_laws"][0]))
        result["civil_laws"][-1]["rank"] = 4
    elif corruption == "duplicate_article":
        result["laws"][1] = deepcopy(result["laws"][0])
        result["laws"][1]["rank"] = 2
    else:
        result["laws"][0]["rank"] = 99
    with pytest.raises(ValueError):
        assess(frozen_capture, rows)


def test_rebuilt_chunk_ids_do_not_create_article_ranking_or_required_losses(frozen_capture):
    rows = deepcopy(frozen_capture[0])
    for row in rows:
        for channel in ("laws", "civil_laws"):
            for evidence in row["result"][channel]:
                evidence["chunk_id"] = "rebuilt:" + evidence["chunk_id"]
    report = assess(frozen_capture, rows)
    assert report["comparison"]["lost_required_inputs"] == []
    assert report["comparison"]["gained_required_inputs"] == []
    assert report["comparison"]["article_ranking_changes"] == []
    for mode in ("question_only", "context_diagnostic"):
        assert report["groups"][mode]["union_all_required"] == {"hits": 43, "n": 75}


def test_removing_required_evidence_reports_the_exact_input_and_article(frozen_capture):
    rows = deepcopy(frozen_capture[0])
    row = next(r for r in rows if r["qid"] == "CIV-DRAFT-001")
    target = "민법-제623조"
    assert any(e["article_id"] == target for e in row["result"]["civil_laws"])
    row["result"]["civil_laws"] = [e for e in row["result"]["civil_laws"] if e["article_id"] != target]
    for rank, evidence in enumerate(row["result"]["civil_laws"], 1):
        evidence["rank"] = rank
    report = assess(frozen_capture, rows)
    assert report["comparison"]["lost_required_inputs"] == [
        {"qid": "CIV-DRAFT-001", "mode": "question_context", "lost": [target]}]
    assert report["groups"]["required_law"]["union_all_required"] == {"hits": 27, "n": 29}


@pytest.mark.parametrize("corruption", ["duplicate", "extra", "missing", "query_hash"])
def test_invalid_comparison_reference_cannot_hide_or_duplicate_a_row(frozen_capture, corruption):
    reference = evaluation.load_reference_rows()
    if corruption == "duplicate":
        reference.append(deepcopy(reference[0]))
    elif corruption == "extra":
        extra = deepcopy(reference[0])
        extra["qid"] = "DEV-999"
        reference.append(extra)
    elif corruption == "missing":
        reference.pop()
    else:
        reference[0]["query_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        evaluation.analyze(frozen_capture[0], available_articles=frozen_capture[1],
                           reference_rows=reference)


@pytest.fixture
def artifact(tmp_path, frozen_capture):
    """A complete synthetic audit for offline validation, without live-data access."""
    rows = deepcopy(frozen_capture[0])
    for row in rows:
        row.update(model_calls=1, seconds=0.01)
    profile = read("data/eval/patch027-rollout/adopted/audit.json")["profile"]
    data_hashes = {**profile["files"], evaluation.PROFILE: "a" * 64}
    for index in evaluation.INDEXES:
        data_hashes[index + "/chroma.sqlite3"] = "b" * 64
    sources = {"src/retrieval/service.py": evaluation.sha(ROOT / "src/retrieval/service.py")}
    audit = {
        "schema": "patch041-retrieval-eval-v1", "inputs": 235,
        "generation_performed": False, "product_source_clean": True,
        "channel_limits": evaluation.CHANNEL_LIMITS, "profile": profile,
        "settings": {"profile": evaluation.POLICY, "search_k": evaluation.SEARCH_K},
        "data_hashes_before": data_hashes, "data_hashes_after": data_hashes,
        "payload_hashes_before": data_hashes, "payload_hashes_after": data_hashes,
        "product_source_hashes_before": sources, "product_source_hashes_after": sources,
        "logical_index_hashes_before": profile["index_hashes"],
        "logical_index_hashes_after": profile["index_hashes"],
        "model_hashes_before": {"snapshots/test/model.safetensors": "c" * 64},
        "model_hashes_after": {"snapshots/test/model.safetensors": "c" * 64},
        "model_usage": {"actual_kure_calls": 235, "general_backend_requests": 235,
                        "civil_backend_requests": 235},
        "criteria_hashes_before": evaluation.criteria_hashes(),
        "criteria_hashes_after": evaluation.criteria_hashes(),
        "reference_hashes_before": evaluation.reference_hashes(),
        "reference_hashes_after": evaluation.reference_hashes(),
        "evaluator_source_hashes_before": evaluation.evaluator_source_hashes(),
        "evaluator_source_hashes_after": evaluation.evaluator_source_hashes(),
        "available_articles": frozen_capture[1],
    }
    for name, value in (("rows.json", rows), ("audit.json", audit),
                        ("report.json", assess(frozen_capture, rows))):
        evaluation.write(tmp_path / name, value)
    rehash(tmp_path)
    return tmp_path


def rehash(path):
    evaluation.write(path / "manifest.json", {
        name: evaluation.sha(path / name) for name in evaluation.ARTIFACTS})


def test_valid_offline_bundle_replays_without_model_loading(artifact):
    checked = evaluation.check(artifact)
    assert checked["inputs"] == 235
    assert checked["groups"]["question_only"]["union_all_required"] == {"hits": 43, "n": 75}


@pytest.mark.parametrize("rehash_changed_file", [False, True])
def test_modified_report_is_rejected_even_when_manifest_is_rehashed(artifact, rehash_changed_file):
    path = artifact / "report.json"
    report = evaluation.read(path)
    report["groups"]["question_only"]["union_all_required"]["hits"] = 75
    evaluation.write(path, report)
    if rehash_changed_file:
        rehash(artifact)
    with pytest.raises(ValueError):
        evaluation.check(artifact)


@pytest.mark.parametrize("prefix", ["data_hashes", "product_source_hashes", "logical_index_hashes",
                                    "payload_hashes", "model_hashes", "criteria_hashes",
                                    "reference_hashes", "evaluator_source_hashes"])
def test_missing_both_sides_of_provenance_is_rejected(artifact, prefix):
    audit = evaluation.read(artifact / "audit.json")
    del audit[prefix + "_before"]
    del audit[prefix + "_after"]
    evaluation.write(artifact / "audit.json", audit)
    rehash(artifact)
    with pytest.raises(ValueError):
        evaluation.check(artifact)


def test_zero_kure_calls_for_one_input_cannot_be_hidden_in_total(artifact):
    rows = evaluation.read(artifact / "rows.json")
    rows[0]["model_calls"] = 0
    rows[1]["model_calls"] = 2
    evaluation.write(artifact / "rows.json", rows)
    rehash(artifact)
    with pytest.raises(ValueError):
        evaluation.check(artifact)


def test_removing_artifact_and_manifest_entry_is_rejected(artifact):
    (artifact / "rows.json").unlink()
    manifest = evaluation.read(artifact / "manifest.json")
    del manifest["rows.json"]
    evaluation.write(artifact / "manifest.json", manifest)
    with pytest.raises(ValueError):
        evaluation.check(artifact)


@pytest.mark.parametrize("filename", ["src/retrieval/service.py", "setup_data.py", "requirements.txt"])
def test_first_unstaged_product_change_stops_capture_before_model_loading(monkeypatch, tmp_path, filename):
    def git_output(command, **kwargs):
        if command[1:] == ["status", "--porcelain"]:
            return " M " + filename + "\n"
        if command[1] == "ls-files":
            return filename + "\n"
        raise AssertionError("Unexpected git operation")

    def model_must_not_load(*args, **kwargs):
        raise AssertionError("Dirty source reached model initialization")

    monkeypatch.setattr(evaluation.subprocess, "check_output", git_output)
    monkeypatch.setattr(evaluation, "prepare_product_service", model_must_not_load)
    with pytest.raises(ValueError, match="source is dirty"):
        evaluation.capture(tmp_path / "unused_data", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_source_hash_allows_only_line_ending_drift_and_keeps_data_hash_raw(tmp_path):
    lf, crlf, changed = [tmp_path / name for name in ("unix.py", "windows.py", "changed.py")]
    lf.write_bytes(b"def query():\n    return 1\n")
    crlf.write_bytes(b"def query():\r\n    return 1\r\n")
    changed.write_bytes(b"def query():\n    return 2\n")
    assert evaluation.source_sha(lf) == evaluation.source_sha(crlf)
    assert evaluation.source_sha(lf) != evaluation.source_sha(changed)
    assert evaluation.sha(lf) != evaluation.sha(crlf)


@pytest.mark.parametrize("prefix", ["criteria_hashes", "reference_hashes", "evaluator_source_hashes"])
def test_changed_execution_dependency_is_rejected_even_with_rehashed_audit(artifact, prefix):
    audit = evaluation.read(artifact / "audit.json")
    first = next(iter(audit[prefix + "_after"]))
    audit[prefix + "_after"][first] = "0" * 64
    evaluation.write(artifact / "audit.json", audit)
    rehash(artifact)
    with pytest.raises(ValueError):
        evaluation.check(artifact)
