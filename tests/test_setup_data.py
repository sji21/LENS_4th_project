import subprocess
import hashlib
import json
import pytest

import setup_data as setup


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setattr(setup, "environment_python", lambda: python)
    monkeypatch.setattr(setup.sys, "version_info", (3, 11))
    calls = []
    monkeypatch.setattr(setup, "run", lambda command: calls.append([str(x) for x in command]))
    return python, calls


def test_check_never_installs_or_launches_db(preparation):
    _, calls = preparation
    assert setup.main(["--check"]) == 0
    assert not any("install" in cmd or "venv" in cmd for cmd in calls)
    assert calls[-1][-2:] == ["--model-worker", "--check"]
    assert not any("scripts.manage_retrieval_data" in cmd for cmd in calls)


def test_prepare_only_downloads_but_does_not_modify_db(preparation):
    _, calls = preparation
    assert setup.main(["--prepare-only"]) == 0
    assert any(cmd[1:4] == ["-m", "pip", "install"] for cmd in calls)
    assert any(cmd[1:] == ["-m", "pip", "check"] for cmd in calls)
    assert calls[-1][-1] == "--model-worker"


def test_failed_dependencies_stop_before_model_and_db(preparation, monkeypatch):
    _, calls = preparation
    def failed(command):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(setup, "run", failed)
    assert setup.main([]) == 1
    assert len(calls) == 1


def test_model_failure_stops_before_db(preparation, monkeypatch):
    python, calls = preparation
    monkeypatch.setattr(setup, "prepare_environment", lambda _: python)
    def fail(command):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(setup, "run", fail)
    assert setup.main([]) == 1
    assert len(calls) == 1 and calls[0][-1] == "--model-worker"


def test_prepared_flow_passes_source_as_one_argument(preparation, tmp_path):
    python, calls = preparation
    source = tmp_path / "자료 data"
    assert setup.main(["--source", str(source)]) == 0
    assert calls[-1] == [str(python), "-X", "utf8", "-m", "scripts.manage_retrieval_data", "--source", str(source.resolve())]


def test_missing_environment_check_is_read_only(preparation, monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "environment_python", lambda: tmp_path / "missing")
    assert setup.main(["--check"]) == 1
    assert preparation[1] == []


def test_wrong_python_version_is_rejected(preparation, monkeypatch):
    monkeypatch.setattr(setup.sys, "version_info", (3, 12))
    assert setup.main([]) == 1
    assert preparation[1] == []


@pytest.fixture
def model_cache(tmp_path, monkeypatch):
    import huggingface_hub
    import huggingface_hub.constants as constants
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup.Path, "home", classmethod(lambda cls: tmp_path))
    hub = tmp_path / ".cache/huggingface/hub"
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(hub))
    cache = hub / "models--nlpai-lab--KURE-v1"
    audit = tmp_path / "data/eval/patch027-full/capture/audit.json"
    audit.parent.mkdir(parents=True)
    relative = "snapshots/reviewed-version/config.json"
    audit.write_text(json.dumps({"model_files": {relative: hashlib.sha256(b"reviewed").hexdigest()}}))
    downloads = []
    def download(repo, **kwargs):
        downloads.append((repo, kwargs))
        target = cache / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"reviewed")
    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    return cache, downloads


def test_model_download_uses_reviewed_revision_then_check_is_offline(model_cache):
    cache, downloads = model_cache
    setup.prepare_model()
    assert downloads[0][1]["revision"] == "reviewed-version"
    assert (cache / "refs/main").read_text() == "reviewed-version"
    setup.prepare_model(check=True)
    assert len(downloads) == 1


def test_different_model_ref_is_preserved_and_no_download(model_cache):
    cache, downloads = model_cache
    ref = cache / "refs/main"
    ref.parent.mkdir(parents=True)
    ref.write_text("different")
    with pytest.raises(ValueError, match="자동 교체하지"):
        setup.prepare_model()
    assert ref.read_text() == "different" and downloads == []


def test_corrupt_model_fails_hash_check(model_cache):
    cache, _ = model_cache
    setup.prepare_model()
    (cache / "snapshots/reviewed-version/config.json").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="파일 검증 실패"):
        setup.prepare_model(check=True)


def test_missing_model_check_never_downloads(model_cache):
    _, downloads = model_cache
    with pytest.raises(ValueError, match="파일이 없습니다"):
        setup.prepare_model(check=True)
    assert downloads == []
