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


def test_django_is_the_only_product_web_entrypoint() -> None:
    urls = (ROOT / "config" / "urls.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert 'include("chat.urls")' in urls
    assert (ROOT / "manage.py").is_file()
    assert not (ROOT / "app" / "streamlit_app.py").exists()
    assert not (ROOT / ".streamlit" / "config.toml").exists()
    assert "웹 서비스는 Django만 사용합니다." in readme


def test_patch043_is_excluded_from_product_runtime() -> None:
    case_profile = (ROOT / "src" / "retrieval" / "case_profile.py").read_text(encoding="utf-8")

    assert (ROOT / "experiments" / "patch043_case_internal" / "README.md").is_file()
    assert "PATCH-043 내부 판례 프로필은 제품 검색에서 지원하지 않습니다." in case_profile
    assert "case_internal_profile" not in case_profile
