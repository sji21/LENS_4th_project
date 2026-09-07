"""SentenceTransformer 로컬 캐시 우선 로딩 테스트."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from src.retrieval.dense import SentenceTransformerEmbedding


def _install_fake_sentence_transformer(monkeypatch, fake_class) -> None:
    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = fake_class
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)


def test_embedding_uses_local_cache_without_network_probe(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeSentenceTransformer:
        def __init__(self, _model_id, **kwargs):
            calls.append(kwargs)

    _install_fake_sentence_transformer(monkeypatch, FakeSentenceTransformer)
    SentenceTransformerEmbedding("nlpai-lab/KURE-v1")

    assert calls == [{"device": None, "local_files_only": True}]


def test_embedding_downloads_only_when_local_cache_is_missing(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeSentenceTransformer:
        def __init__(self, _model_id, **kwargs):
            calls.append(kwargs)
            if kwargs["local_files_only"]:
                raise OSError("not cached")

    _install_fake_sentence_transformer(monkeypatch, FakeSentenceTransformer)
    SentenceTransformerEmbedding("nlpai-lab/KURE-v1")

    assert calls == [
        {"device": None, "local_files_only": True},
        {"device": None, "local_files_only": False},
    ]


def test_embedding_does_not_hide_non_cache_initialization_errors(monkeypatch) -> None:
    class FakeSentenceTransformer:
        def __init__(self, _model_id, **_kwargs):
            raise RuntimeError("invalid model")

    _install_fake_sentence_transformer(monkeypatch, FakeSentenceTransformer)
    with pytest.raises(RuntimeError, match="invalid model"):
        SentenceTransformerEmbedding("nlpai-lab/KURE-v1")
