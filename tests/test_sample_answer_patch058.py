from pathlib import Path

from scripts import sample_answer_test_patch058 as sample


def test_runpod_probe_sets_a_user_agent(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"models":[]}'

    def urlopen(request, timeout):
        seen["user_agent"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(sample.urllib.request, "urlopen", urlopen)

    assert sample._json_get("https://example.test/api/tags", timeout=7) == {"models": []}
    assert seen == {"user_agent": "LENS-PATCH058/1.0", "timeout": 7}


def test_no_think_flag_uses_the_runtime_environment_name():
    source = Path(sample.__file__).read_text(encoding="utf-8")

    assert 'os.environ["JEONSEON_LLM_NO_THINK"] = "1"' in source
    assert "JEONSEON_LLM_THINK_OFF" not in source
