from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from scripts import manage_retrieval_data as manager

REAL_RUN_STEP = manager.run_step


def test_concurrent_installation_is_rejected_and_lock_released(tmp_path):
    with manager.installation_lock(tmp_path):
        with pytest.raises(ValueError, match="실행 중"):
            with manager.installation_lock(tmp_path):
                pytest.fail("Second writer entered")
    with manager.installation_lock(tmp_path):
        pass


def test_lock_releases_when_operation_raises(tmp_path):
    with pytest.raises(RuntimeError):
        with manager.installation_lock(tmp_path):
            raise RuntimeError("failed installation")
    with manager.installation_lock(tmp_path):
        pass


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "ROOT", tmp_path)
    monkeypatch.setattr(manager, "require_clean_code", lambda: None)
    monkeypatch.setattr(manager, "data_status", lambda _: {"state": "baseline"})
    monkeypatch.setattr(manager, "inspect_in_process", lambda *_: {})
    calls = []
    monkeypatch.setattr(manager, "run_step", lambda action, **paths: calls.append((action, paths)))
    return tmp_path, calls


def test_repeat_install_is_noop_without_source_backup_or_verification(flow, monkeypatch):
    root, calls = flow
    monkeypatch.setattr(manager, "data_status", lambda _: {"state": "installed"})
    assert manager.apply_data(None)["state"] == "installed"
    assert calls == []
    assert not (root / "tmp/retrieval-data").exists()


def test_no_argument_entry_shows_three_steps_without_raw_json(flow, monkeypatch, capsys):
    _, calls = flow
    monkeypatch.setattr(manager, "data_status", lambda _: {"state": "installed", "counts": {
        "laws": 178, "civil_laws": 26, "cases": 26, "guides": 6}})
    assert manager.main([]) == 0
    output = capsys.readouterr().out
    assert output.index("[1/3]") < output.index("[2/3]") < output.index("[3/3]")
    assert "추가 불필요" in output and "민법 26개" in output
    assert '"state"' not in output
    assert calls == []


def test_source_is_requested_only_when_update_is_needed(flow, monkeypatch):
    root, calls = flow
    monkeypatch.setattr(manager, "request_source", lambda: root / "source")
    assert manager.apply_data(None)["state"] == "installed"
    assert calls[0][1]["data"] == root / "source"


def test_noninteractive_missing_source_fails_without_waiting(monkeypatch):
    monkeypatch.setattr(manager.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValueError, match="데이터 폴더"):
        manager.request_source()


def test_failed_step_retains_diagnostics_in_log(flow, monkeypatch):
    root, _ = flow
    def fail(command, **kwargs):
        kwargs["stdout"].write(b"specific diagnostic")
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(manager.subprocess, "run", fail)
    with pytest.raises(ValueError, match="상세 기록"):
        REAL_RUN_STEP("verify", data=root / "data", out=root / "preflight")
    assert (root / "preflight-verify.log").read_bytes() == b"specific diagnostic"


def test_broken_installed_data_is_not_treated_as_success(flow, monkeypatch):
    root, calls = flow
    def invalid(_):
        raise ValueError("profile mismatch")
    monkeypatch.setattr(manager, "data_status", invalid)
    with pytest.raises(ValueError, match="profile mismatch"):
        manager.apply_data(root / "source")
    assert calls == []


def test_apply_orders_preflight_install_postflight_and_saves_backup(flow):
    root, calls = flow
    result = manager.apply_data(root / "source")
    assert [name for name, _ in calls] == ["stage", "verify", "apply", "verify"]
    assert calls[-1][1]["data"] == root / "data"
    assert Path(result["backup"]).parent.joinpath("result.json").is_file()


@pytest.mark.parametrize("phase", ["preflight", "apply", "postflight", "result"])
def test_failure_before_install_does_not_mutate_and_after_install_restores(flow, monkeypatch, phase):
    root, calls = flow
    def step(action, **paths):
        calls.append((action, paths))
        fails = (phase == "preflight" and action == "verify" and paths["out"].name == "preflight"
                 or phase == "apply" and action == "apply"
                 or phase == "postflight" and action == "verify" and paths["out"].name == "postflight")
        if fails:
            raise subprocess.CalledProcessError(1, action)
    monkeypatch.setattr(manager, "run_step", step)
    if phase == "result":
        def fail_save(*_):
            raise OSError("disk full")
        monkeypatch.setattr(manager, "save_result", fail_save)
    with pytest.raises((subprocess.CalledProcessError, OSError)):
        manager.apply_data(root / "source")
    actions = [name for name, _ in calls]
    assert ("restore" in actions) == (phase in {"postflight", "result"})
    if phase == "preflight":
        assert "apply" not in actions


def test_source_equal_to_target_is_rejected(flow):
    root, calls = flow
    with pytest.raises(ValueError, match="다른 폴더"):
        manager.apply_data(root / "data")
    assert calls == []


def test_wrong_backup_is_rejected_before_restore(flow, monkeypatch):
    root, calls = flow
    monkeypatch.setattr(manager, "data_status", lambda _: {"state": "installed"})
    def wrong_backup(*_):
        raise ValueError("backup mismatch")
    monkeypatch.setattr(manager, "inspect_in_process", wrong_backup)
    with pytest.raises(ValueError, match="backup mismatch"):
        manager.restore_data(root / "wrong-backup")
    assert calls == []


@pytest.fixture
def small_data(tmp_path):
    data = tmp_path / "data"
    def row(cid, title, article):
        return {"chunk_id": cid, "text": "content", "metadata": {
            "title": title, "article_id": article, "version": "v1", "effective_date": "2026-01-01"}}
    civil = row("civil", "민법", "민법-제1조")
    contents = {
        "chunks/chunks.jsonl": [row("law", "검증법", "검증법-제1조"), civil],
        "chunks/civil.jsonl": [civil],
        "chunks/cases.jsonl": [row("case", "판례", "case")],
        "chunks/guides.jsonl": [row("guide", "안내", "guide")],
    }
    for name, rows in contents.items():
        path = data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    (data / "database").mkdir()
    for name in ("knowledge.sqlite3", "civil.sqlite3"):
        with sqlite3.connect(data / "database" / name) as db:
            db.execute("CREATE TABLE law_articles(law_version_id,article_number,paragraph_number,item_number)")
    return data


def test_civil_copy_in_combined_file_is_intentional_not_duplicate(small_data):
    assert manager.check_duplicates(small_data) == {"laws": 1, "civil_laws": 1, "cases": 1, "guides": 1}


@pytest.mark.parametrize("different_id", [False, True])
def test_duplicate_id_or_same_article_edition_with_different_id_is_rejected(small_data, different_id):
    path = small_data / "chunks/chunks.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    extra = deepcopy(rows[0])
    if different_id:
        extra["chunk_id"] = "other-id"
    with path.open("a", encoding="utf-8") as out:
        out.write("\n" + json.dumps(extra))
    with pytest.raises(ValueError, match="중복"):
        manager.check_duplicates(small_data)


def test_civil_copy_mismatch_is_rejected(small_data):
    path = small_data / "chunks/civil.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["text"] = "different content"
    path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="일치하지"):
        manager.check_duplicates(small_data)


def test_duplicate_database_article_is_rejected(small_data):
    with sqlite3.connect(small_data / "database/knowledge.sqlite3") as db:
        db.executemany("INSERT INTO law_articles VALUES(?,?,?,?)", [("v1", "제1조", "", "")] * 2)
    with pytest.raises(ValueError, match="SQLite 조문 중복"):
        manager.check_duplicates(small_data)


def test_empty_data_is_reported_uninstalled_without_touching_eval(tmp_path):
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval/proof.json").write_text("proof")
    assert manager.data_status(tmp_path)["state"] == "empty"
    assert (tmp_path / "eval/proof.json").read_text() == "proof"


def test_partial_base_data_is_rejected(tmp_path):
    (tmp_path / "chunks").mkdir()
    (tmp_path / "chunks/chunks.jsonl").write_text("partial")
    with pytest.raises(ValueError, match="기본 데이터"):
        manager.data_status(tmp_path)


@pytest.mark.parametrize("action", ["status", "apply", "restore"])
def test_source_build_is_explained_without_overwriting(tmp_path, monkeypatch, capsys, action):
    monkeypatch.setattr(manager, "ROOT", tmp_path)
    monkeypatch.setattr(manager, "require_clean_code", lambda: None)
    marker = tmp_path / "data/index/server-build.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"version":1}')
    monkeypatch.setattr(manager, "inspect_in_process", lambda *a: pytest.fail("must reject before inspection"))
    monkeypatch.setattr(manager, "run_step", lambda *a, **k: pytest.fail("must not overwrite"))
    assert manager.main([action, "--backup", str(tmp_path / "backup")]) == 1
    assert "별도 체크아웃" in capsys.readouterr().err
    assert marker.read_text() == '{"version":1}'


def test_source_build_cannot_be_used_as_frozen_bundle_source(tmp_path):
    marker = tmp_path / "index/server-build.json"
    marker.parent.mkdir()
    marker.write_text('{}')
    with pytest.raises(ValueError, match="원천 구축 DB"):
        manager.inspect_in_process(tmp_path, "expanded")


def test_inconsistent_profile_is_not_a_successful_noop(small_data, monkeypatch):
    profile = {"files": {name: manager.sha(small_data / name) for name in manager.FILES}, "index_hashes": []}
    (small_data / "index").mkdir()
    (small_data / manager.PROFILE).write_text('{"policy":"wrong"}', encoding="utf-8")
    monkeypatch.setattr(manager, "expected_profile", lambda: profile)
    with pytest.raises(ValueError, match="프로필"):
        manager.inspect_data(small_data, "expanded")


def test_missing_index_is_rejected_without_creating_empty_index(small_data, monkeypatch):
    profile = {"files": {name: manager.sha(small_data / name) for name in manager.FILES}, "index_hashes": []}
    monkeypatch.setattr(manager, "expected_profile", lambda: profile)
    with pytest.raises(ValueError, match="인덱스가 없습니다"):
        manager.inspect_data(small_data, "expanded")
    assert not (small_data / "index").exists()


def test_index_inspection_changes_only_temporary_copy_and_cleans_it(small_data, monkeypatch):
    root = small_data.parent
    monkeypatch.setattr(manager, "ROOT", root)
    for name in manager.INDEXES:
        path = small_data / name / "chroma.sqlite3"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"original index")
    inspected = []
    def inspect(command, **kwargs):
        snapshot = Path(command[command.index("--source") + 1])
        assert snapshot != small_data
        inspected.append(snapshot)
        (snapshot / manager.INDEXES[0] / "chroma.sqlite3").write_bytes(b"Chroma internal rewrite")
        return SimpleNamespace(returncode=0, stdout='{"laws":1}', stderr="")
    monkeypatch.setattr(manager.subprocess, "run", inspect)
    assert manager.inspect_in_process(small_data, "expanded") == {"laws": 1}
    assert (small_data / manager.INDEXES[0] / "chroma.sqlite3").read_bytes() == b"original index"
    assert not inspected[0].exists()


@pytest.fixture
def fresh_install(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "ROOT", tmp_path)
    run = tmp_path / "tmp/fresh-run"
    for rel in manager.SCOPES:
        path = run / "stage/data" / rel
        if rel in manager.INDEXES:
            path = path / "chroma.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("payload " + rel)
    (tmp_path / "data/eval").mkdir(parents=True)
    (tmp_path / "data/eval/proof.json").write_text("keep")
    monkeypatch.setattr(manager, "run_step", lambda *args, **kwargs: None)
    return tmp_path, run


def test_first_install_preserves_repository_data(fresh_install):
    root, run = fresh_install
    result = manager.install_empty(run, {"laws": 178, "civil_laws": 26, "cases": 26, "guides": 6})
    assert result["installation"] == "fresh"
    assert manager.payload_hashes(root / "data") == manager.payload_hashes(run / "stage/data")
    assert (root / "data/eval/proof.json").read_text() == "keep"


@pytest.mark.parametrize("failure", ["verify", "save_result"])
def test_first_install_failure_removes_only_installed_payload(fresh_install, monkeypatch, failure):
    root, run = fresh_install
    def fail(*args, **kwargs):
        raise OSError("disk or verification failure")
    monkeypatch.setattr(manager, "run_step" if failure == "verify" else "save_result", fail)
    with pytest.raises(OSError):
        manager.install_empty(run, {})
    assert not any((root / "data" / rel).exists() for rel in manager.SCOPES)
    assert manager.payload_hashes(run / "failed") == manager.payload_hashes(run / "stage/data")
    assert (root / "data/eval/proof.json").read_text() == "keep"


def test_first_install_rejects_existing_data(fresh_install):
    root, run = fresh_install
    existing = root / "data/chunks/chunks.jsonl"
    existing.parent.mkdir()
    existing.write_text("existing")
    with pytest.raises(ValueError, match="덮어쓰지"):
        manager.install_empty(run, {})
    assert existing.read_text() == "existing"


def test_empty_apply_is_preflight_verified_before_install(flow, monkeypatch):
    root, calls = flow
    monkeypatch.setattr(manager, "data_status", lambda _: {"state": "empty"})
    monkeypatch.setattr(manager, "install_empty", lambda *args: calls.append("installed"))
    manager.apply_data(root / "source")
    assert [call[0] for call in calls[:-1]] == ["stage", "verify"]
    assert calls[-1] == "installed"
