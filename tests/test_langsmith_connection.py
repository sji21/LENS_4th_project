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


def _configured_api_key():
    if os.getenv("LANGSMITH_TRACING", "").lower() != "true":
        pytest.skip("LANGSMITH_TRACING이 활성화되어 있지 않습니다.")
    api_key = os.getenv("LANGSMITH_API_KEY", "").strip()
    assert api_key, "추적을 활성화하면 LANGSMITH_API_KEY가 필요합니다."
    assert os.getenv("LANGSMITH_PROJECT", "").strip(), "추적을 활성화하면 LANGSMITH_PROJECT가 필요합니다."
    return api_key


def test_langsmith_environment_is_configured():
    """선택 기능이 켜져 있을 때만 필수 설정을 확인한다."""
    _configured_api_key()


@pytest.mark.integration
def test_langsmith_client_connection():
    """LangSmith API에 실제로 연결 가능한지 확인한다."""

    api_key = _configured_api_key()

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


@pytest.mark.parametrize("tracing", [None, "", "false"])
def test_disabled_tracing_skips_configuration_check(monkeypatch, tracing):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    if tracing is None:
        monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    else:
        monkeypatch.setenv("LANGSMITH_TRACING", tracing)
    with pytest.raises(pytest.skip.Exception):
        _configured_api_key()


@pytest.mark.parametrize("missing", ["LANGSMITH_API_KEY", "LANGSMITH_PROJECT"])
def test_enabled_tracing_requires_configuration(monkeypatch, missing):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-placeholder")
    monkeypatch.setenv("LANGSMITH_PROJECT", "test-project")
    monkeypatch.setenv(missing, " ")
    with pytest.raises(AssertionError, match=missing):
        _configured_api_key()


def test_enabled_tracing_accepts_configuration(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "TRUE")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-placeholder")
    monkeypatch.setenv("LANGSMITH_PROJECT", "test-project")
    assert _configured_api_key() == "test-placeholder"
