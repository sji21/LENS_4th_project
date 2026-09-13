import json

import pytest

from scripts.patch027_paths import current_data_paths, read, read_records, sha


def test_frozen_paths_resolve_without_changing_capture_bytes(tmp_path):
    path = tmp_path / "audit.json"
    original = {
        "dependencies": {"data/eval/patch026-full/manifest.json": "digest"},
        "source_hashes": {"scripts/patch026_full_eval.py": "original-code-hash"},
        "path": "data/eval/patch026-expansion/sources/RR16.html",
        "commit": "original-execution-commit",
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    digest = sha(path)
    resolved = read(path)
    assert resolved["dependencies"] == {"data/eval/patch027-full/manifest.json": "digest"}
    assert resolved["path"] == "data/eval/patch027-expansion/sources/RR16.html"
    assert resolved["source_hashes"] == original["source_hashes"]
    assert resolved["commit"] == original["commit"]
    assert sha(path) == digest
    assert json.loads(path.read_text()) == original
    assert current_data_paths(resolved) == resolved


def test_path_alias_does_not_rewrite_unrelated_names_or_prose():
    values = ["data/eval/patch026-full-other/a.json", "scripts/patch026_full_eval.py",
              "C:/team_project/patch026-worktree", "See data/eval/patch026-full/a.json"]
    assert current_data_paths(values) == values


def test_alias_collision_is_rejected_in_both_orders():
    entries = [("data/eval/patch026-full/a.json", "old"),
               ("data/eval/patch027-full/a.json", "new")]
    for items in (entries, entries[::-1]):
        with pytest.raises(ValueError, match="Duplicate data path"):
            current_data_paths(dict(items))


def test_persisted_ingestion_record_keeps_content_and_hash():
    from scripts.patch027_paths import ROOT
    from src.ingestion.load_laws import read_records as original_reader
    path = ROOT / "data/eval/patch027-expansion/records.jsonl"
    digest = sha(path)
    original, resolved = original_reader(path), read_records(path)
    assert len(original) == len(resolved) == 5
    for old, new in zip(original, resolved):
        assert new.file_path == old.file_path.replace("patch026-expansion", "patch027-expansion")
        assert new.content == old.content
        assert new.source_url == old.source_url
    assert sha(path) == digest


def test_source_verification_does_not_overwrite_frozen_records(tmp_path):
    from scripts.patch027_full_sources import OUT, compile_records
    path = OUT / "records.jsonl"
    digest = sha(path)
    assert len(compile_records()) == 56
    assert sha(path) == digest
    output = tmp_path / "records.jsonl"
    compile_records(output=output)
    assert len(read_records(output)) == 56
    assert sha(path) == digest
