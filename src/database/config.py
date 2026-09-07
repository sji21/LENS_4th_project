"""운영체제에 독립적인 로컬 DB 경로 설정."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DatabasePaths:
    relational: Path
    chroma: Path


def _configured_path(variable: str, default: Path) -> Path:
    value = os.getenv(variable, "").strip()
    return Path(value).expanduser().resolve() if value else default


def resolve_database_paths() -> DatabasePaths:
    """환경 변수가 없으면 프로젝트의 재생성 가능 데이터 경로를 사용한다."""

    return DatabasePaths(
        relational=_configured_path(
            "JEONSEON_DATABASE_PATH",
            PROJECT_ROOT / "data" / "database" / "knowledge.sqlite3",
        ),
        chroma=_configured_path(
            "JEONSEON_CHROMA_PATH",
            # 인덱스 디렉터리 이름에는 임베딩 모델과 차원이 붙는다.
            # 컬렉션당 차원은 하나뿐이라 모델을 바꾸면 새 컬렉션을 만들어야 한다.
            # 명명 규칙은 src/retrieval/index.py 의 index_dir_for 와 같다.
            PROJECT_ROOT / "data" / "index" / "chroma_kurev1_1024",
        ),
    )
