import subprocess
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
