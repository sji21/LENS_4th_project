"""PATCH-008 README 프로젝트 구조 회귀 테스트."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_planning_files_follow_document_structure() -> None:
    assert (ROOT / "docs" / "planning" / "project-plan.md").is_file()
    assert (ROOT / "docs" / "planning" / "jeonseon-project-plan.pdf").is_file()
    assert (ROOT / "docs" / "planning" / "assets" / "pipeline.png").is_file()
    assert (ROOT / "docs" / "planning" / "assets" / "project-stages.png").is_file()
    assert (ROOT / "docs" / "planning" / "reference" / "source-project-plan.pdf").is_file()
    assert (ROOT / "scripts" / "build_project_plan_pdf.py").is_file()


def test_readme_documents_added_directories_and_artifacts() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for expected in (
        "scripts/",
        "docs/planning/",
        "docs/planning/project-plan.md",
        "docs/planning/jeonseon-project-plan.pdf",
        "scripts/build_project_plan_pdf.py",
    ):
        assert expected in readme
