import os

from src.environment import load_project_environment


def test_project_environment_does_not_override_shell_value(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("LENS_TEST_SETTING=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LENS_TEST_SETTING", "from-shell")

    assert load_project_environment(env_file)
    assert os.environ["LENS_TEST_SETTING"] == "from-shell"
