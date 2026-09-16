"""The dialogue evaluation boundary must never execute a legal backend."""
from copy import deepcopy
import json

import pytest

from src.evaluation import chatting_dialogue_run as runner
from src.evaluation.chatting_scenarios import load_suite
from tests.test_chatting_routing import runtime


def test_capture_labels_fixed_backend_and_never_invokes_real_legal_graph(tmp_path, monkeypatch, runtime):
    suite = deepcopy(load_suite("dev"))
    suite["cases"] = [suite["cases"][1]]
    monkeypatch.setattr(runner, "load_suite", lambda split: suite)
    monkeypatch.setattr(runner, "capture_ollama_identity", lambda model: {"available": True, "digest": "fixture"})
    monkeypatch.setattr(runner, "fingerprints", lambda: {"fixture": "unchanged"})
    output = tmp_path / "capture.json"
    report = runner.run("dev", output)
    assert report["complete"]
    assert report["mode"] == "dialogue_only"
    assert report["legal_backend"] == "fixed_test_double"
    assert report["rows"][0]["boundary"]["query"].endswith(suite["cases"][0]["turns"][0]["user"])
    assert json.loads(output.read_text()) == report
    runtime.official.assert_not_called()
    runtime.document.assert_not_called()
    runtime.legacy.assert_not_called()
    runtime.loader.result.assert_not_called()
    with pytest.raises(ValueError, match="exists"):
        runner.run("dev", output)
