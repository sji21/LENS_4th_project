from pathlib import Path

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
