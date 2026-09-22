from pathlib import Path

import pytest

from scripts import run_ho30_generation as runner
from src.retrieval.service import Evidence, RetrievalResult


def test_runner_loads_question_only_input():
    rows = runner._load_questions()

    assert len(rows) == 30
    assert len({row["id"] for row in rows}) == 30
    assert all(set(row) == {"id", "question"} for row in rows)


def test_runner_does_not_open_gold_or_review_inputs():
    source = Path(runner.__file__).read_text(encoding="utf-8")

    assert "05_최종기준_구조화.json" not in source
    assert "review/" not in source


def test_runner_uses_the_production_graph_and_hashes_graph_dependencies():
    source = Path(runner.__file__).read_text(encoding="utf-8")

    assert "from src.generation.graph import answer_question" in source
    assert runner.ROOT / "src/generation/graph.py" in runner.CODE_FILES
    assert runner.ROOT / "src/generation/claim_binding.py" in runner.CODE_FILES


def test_completed_discards_only_a_truncated_final_checkpoint(tmp_path):
    results = tmp_path / "results.jsonl"
    results.write_text('{"id":"HO-001"}\n{"id":"HO-', encoding="utf-8")

    assert runner._completed(results) == {"HO-001"}
    assert results.read_text(encoding="utf-8") == '{"id":"HO-001"}\n'


def test_capturing_service_archives_returned_text_and_ids():
    evidence = Evidence(1, "law-1", "law", "법령 제1조", "본문", 1.0, "")

    class Service:
        def search(self, question, **kwargs):
            return RetrievalResult(question=question, laws=[evidence])

    service = runner.CapturingService(Service())
    result = service.search("질문", k_law=3, k_case=2, k_guide=2)

    assert result.laws == [evidence]
    assert service.calls[0]["laws"][0]["chunk_id"] == "law-1"
    assert service.calls[0]["laws"][0]["text"] == "본문"
    assert service.calls[0]["budgets"] == {"k_law": 3, "k_case": 2, "k_guide": 2}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_TEMPERATURE", 0.1),
        ("LLM_MAX_TOKENS", 256),
        ("LLM_NUM_CTX", 4096),
    ],
)
def test_runner_rejects_non_frozen_generation_settings(monkeypatch, name, value):
    monkeypatch.setattr(runner.llm_module, name, value)

    with pytest.raises(RuntimeError, match="고정 생성 설정"):
        runner._assert_frozen_protocol()


def test_runner_rejects_changed_model_identity(monkeypatch):
    monkeypatch.setattr(
        runner,
        "capture_ollama_identity",
        lambda *_args, **_kwargs: {"available": True, "model": "qwen3.8:27b", "digest": "changed"},
    )

    with pytest.raises(RuntimeError, match="모델 digest"):
        runner._capture_required_model_identity(
            expected={"available": True, "model": "qwen3.8:27b", "digest": "original"}
        )
