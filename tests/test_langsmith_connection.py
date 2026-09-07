"""LangSmith 연결 설정 확인용 테스트.

기존 Generation, Retrieval, Streamlit 로직은 건드리지 않는다.
로컬 .env의 LangSmith 설정과 LangSmith 클라이언트 연결 가능 여부만 확인한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from langsmith import Client


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def test_langsmith_environment_is_configured():
    """LangSmith tracing에 필요한 로컬 환경변수가 설정되어 있는지 확인한다."""

    assert os.getenv("LANGSMITH_TRACING", "").lower() == "true"
    assert os.getenv("LANGSMITH_API_KEY", "").strip()

    project = os.getenv("LANGSMITH_PROJECT", "").strip()
    assert project


@pytest.mark.integration
def test_langsmith_client_connection():
    """LangSmith API에 실제로 연결 가능한지 확인한다."""

    if os.getenv("LANGSMITH_TRACING", "").lower() != "true":
        pytest.skip("LANGSMITH_TRACING이 활성화되어 있지 않습니다.")

    api_key = os.getenv("LANGSMITH_API_KEY", "").strip()
    if not api_key:
        pytest.skip("LANGSMITH_API_KEY가 설정되어 있지 않습니다.")

    client = Client(
        api_key=api_key,
    )

    # 서버 연결 및 인증 여부만 확인한다.
    # 기존 RAG/LLM 호출은 하지 않는다.
    list(
        client.list_projects(
            limit=1,
        )
    )
