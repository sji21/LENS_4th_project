from pathlib import Path

from src.evaluation import run_generation_eval
from src.generation import graph


def test_generation_evaluator_uses_production_graph_entrypoint():
    assert run_generation_eval.answer_question is graph.answer_question
    assert run_generation_eval.RUNNER_NAME == "langgraph"


def test_generation_evaluator_loads_environment_before_generation_imports():
    source = Path(run_generation_eval.__file__).read_text(encoding="utf-8")

    assert source.index("load_project_environment()") < source.index(
        "from src.generation.chain import"
    )
