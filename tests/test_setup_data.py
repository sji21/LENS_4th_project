import subprocess
import hashlib
import json
import pytest

import setup_data as setup


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup, "DEFAULT_CASE_RELEASE", tmp_path / "data/case_corpus/release.json")
    monkeypatch.setattr(setup, "DEFAULT_CASE_PROFILE", tmp_path / "data/case_corpus/runtime-profile.json")
    directory = tmp_path / ".venv"
    directory.mkdir()
    (directory / "pyvenv.cfg").touch()
    python = setup.environment_python(venv_dir=directory)
    python.parent.mkdir(exist_ok=True)
    python.touch()
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
    monkeypatch.setattr(setup, "prepare_environment", lambda *_: python)
    def fail(command):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(setup, "run", fail)
    assert setup.main([]) == 1
    assert len(calls) == 1 and calls[0][-1] == "--model-worker"


def test_validation_flow_passes_source_as_one_argument(preparation, tmp_path):
    python, calls = preparation
    source = tmp_path / "자료 data"
    assert setup.main(["--validation-bundle", "--source", str(source)]) == 0
    assert calls[-1] == [str(python), "-X", "utf8", "-m", "scripts.manage_retrieval_data", "--source", str(source.resolve())]


def test_default_setup_builds_from_sources_without_zip(preparation):
    python, calls = preparation
    assert setup.main([]) == 0
    assert calls[-1] == [str(python), "-X", "utf8", "-m", "src.ingestion.server_build"]


def test_validation_bundle_does_not_autodiscover_case_release(preparation):
    _, calls = preparation
    setup.DEFAULT_CASE_RELEASE.parent.mkdir(parents=True)
    setup.DEFAULT_CASE_RELEASE.write_text('{}', encoding='utf-8')
    assert setup.main(["--validation-bundle"]) == 0
    assert not any("--case-release" in cmd or "src.ingestion.case_release" in cmd
                   or "src.ingestion.knowledge_release" in cmd for cmd in calls)


def test_case_release_is_verified_after_base_build_and_writes_runtime_profile(preparation, tmp_path):
    python, calls = preparation
    release = tmp_path / "shared cases/release.json"
    assert setup.main(["--case-release", str(release)]) == 0
    assert calls[-2] == [str(python), "-X", "utf8", "-m", "src.ingestion.server_build"]
    assert calls[-1] == [
        str(python), "-X", "utf8", "-m", "src.ingestion.case_release",
        "--release", str(release.resolve()), "--profile-output", str(setup.DEFAULT_CASE_PROFILE),
    ]


def test_case_release_check_is_read_only_and_checks_actual_index(preparation, tmp_path):
    python, calls = preparation
    release = tmp_path / "shared cases/release.json"
    assert setup.main(["--check", "--case-release", str(release)]) == 0
    assert calls[-1] == [
        str(python), "-X", "utf8", "-m", "src.ingestion.case_release",
        "--release", str(release.resolve()),
    ]
    assert not any("--profile-output" in call for call in calls)


def test_prepare_only_does_not_read_or_write_case_release(preparation, tmp_path):
    _, calls = preparation
    assert setup.main(["--prepare-only", "--case-release", str(tmp_path / "release.json")]) == 0
    assert not any("src.ingestion.case_release" in call for call in calls)


def test_source_requires_explicit_validation_mode(preparation):
    with pytest.raises(SystemExit):
        setup.main(["--source", "data"])
    assert preparation[1] == []


def test_missing_environment_check_is_read_only(preparation, monkeypatch, tmp_path):
    assert setup.main(["--check", "--venv-dir", str(tmp_path / "missing")]) == 1
    assert preparation[1] == []


def test_wrong_python_version_is_rejected(preparation, monkeypatch):
    monkeypatch.setattr(setup.sys, "version_info", (3, 10))
    assert setup.main([]) == 1
    assert preparation[1] == []


@pytest.mark.parametrize("mode", [[], ["--check"], ["--prepare-only"], ["--validation-bundle"]])
def test_external_environment_is_used_for_every_child(preparation, tmp_path, mode):
    default_python, calls = preparation
    directory = tmp_path / "local disk" / "lens-env"
    directory.mkdir(parents=True)
    (directory / "pyvenv.cfg").touch()
    python = setup.environment_python(venv_dir=directory)
    python.parent.mkdir()
    python.touch()
    assert setup.main(["--venv-dir", str(directory), *mode]) == 0
    assert all(cmd[0] == str(python) for cmd in calls)
    assert default_python.is_file()
    assert not any("venv" in cmd for cmd in calls)


def test_new_external_environment_is_created_without_moving_default(preparation, tmp_path):
    default_python, calls = preparation
    directory = tmp_path / "external env"
    assert setup.main(["--venv-dir", str(directory)]) == 0
    assert calls[0] == [setup.sys.executable, "-m", "venv", str(directory)]
    python = setup.environment_python(venv_dir=directory)
    assert all(cmd[0] == str(python) for cmd in calls[1:])
    assert default_python.is_file()
    assert calls[-1][-1] == "src.ingestion.server_build"


def test_relative_environment_path_is_project_relative(preparation, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path.parent)
    assert setup.environment_directory("local env") == tmp_path / "local env"


def test_external_environment_failure_stops_before_model_and_db(preparation, tmp_path, monkeypatch):
    _, calls = preparation
    def fail_install(command):
        calls.append([str(x) for x in command])
        if "install" in command:
            raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(setup, "run", fail_install)
    assert setup.main(["--venv-dir", str(tmp_path / "external")]) == 1
    assert calls[-1][1:4] == ["-m", "pip", "install"]
    assert not any("--model-worker" in cmd for cmd in calls)


@pytest.mark.parametrize("check", [False, True])
def test_unrelated_existing_directory_is_preserved(preparation, tmp_path, check):
    _, calls = preparation
    directory = tmp_path / "unrelated"
    directory.mkdir()
    (directory / "keep.txt").write_text("keep")
    args = ["--venv-dir", str(directory)] + (["--check"] if check else [])
    assert setup.main(args) == 1
    assert calls == []
    assert (directory / "keep.txt").read_text() == "keep"


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


def test_pinned_base_snapshot_preserves_different_main_ref(model_cache):
    cache, downloads = model_cache
    ref = cache / "refs/main"
    ref.parent.mkdir(parents=True)
    ref.write_text("case-corpus-revision")
    setup.prepare_model(pinned_only=True)
    setup.prepare_model(check=True, pinned_only=True)
    assert ref.read_text() == "case-corpus-revision"
    assert len(downloads) == 1


def test_python312_requires_matching_environment_interpreter(preparation,monkeypatch):
    monkeypatch.setattr(setup.sys,"version_info",(3,12))
    assert setup.main(["--prepare-only"])==0
    assert "== (3, 12)" in preparation[1][0][-1]
