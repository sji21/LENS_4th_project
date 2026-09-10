"""프로젝트 진입점에서 공통으로 사용하는 환경 변수 로더."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_project_environment(env_path: Path | None = None) -> bool:
    """프로젝트 ``.env``를 읽되 이미 설정된 셸 환경 변수는 보존한다."""

    target = Path(env_path) if env_path is not None else PROJECT_ROOT / ".env"
    return load_dotenv(target, override=False)
