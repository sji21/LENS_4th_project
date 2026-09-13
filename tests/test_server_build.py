from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from src.ingestion import server_build as build, server_sources as sources


def test_sources_produce_complete_unique_corpus_without_database():
    laws, cases, guides = sources.source_records()
    assert len(laws) == 204 and len(cases) == 26 and len(guides) == 2
    assert len({(r.law_name, r.article_number) for r in laws}) == 204
    assert len([r for r in laws if r.law_name == "민법"]) == 26


def test_source_manifest_cannot_drop_required_files(tmp_path, monkeypatch):
    manifest = json.loads((sources.SOURCES / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"].pop("guide-records.jsonl")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(sources, "SOURCES", tmp_path)
    with pytest.raises(ValueError, match="필수 원천"):
        sources.checked_sources()


def test_source_tampering_fails_before_parse(tmp_path, monkeypatch):
    import shutil
    shutil.copytree(sources.SOURCES, tmp_path / "sources")
    (tmp_path / "sources/guide-records.jsonl").write_text("tampered")
    monkeypatch.setattr(sources, "SOURCES", tmp_path / "sources")
    with pytest.raises(ValueError, match="검증 실패"):
        sources.source_records()


class Vector(list):
    def tolist(self):
        return list(self)


class Collection:
    def __init__(self):
        self.rows = {}
    def get(self, **kwargs):
        return {"ids": list(self.rows), "documents": [r[0] for r in self.rows.values()],
                "metadatas": [r[1] for r in self.rows.values()], "embeddings": [Vector(r[2]) for r in self.rows.values()]}
    def upsert(self, ids, documents, metadatas, embeddings):
        for row in zip(ids, documents, metadatas, embeddings):
            self.rows[row[0]] = row[1:]
    def delete(self, ids):
        for cid in ids:
            del self.rows[cid]


@pytest.fixture
def index(monkeypatch):
    import chromadb
    collection = Collection()
    monkeypatch.setattr(chromadb, "PersistentClient", lambda **kwargs: SimpleNamespace(get_or_create_collection=lambda *a, **k: collection))
    texts = []
    def embed(values):
        texts.extend(values)
        return [[1., 2.] for _ in values]
    return collection, texts, lambda: SimpleNamespace(embed=embed)


def chunk(cid="a", text="body", title="법"):
    return {"chunk_id": cid, "text": text, "metadata": {"title": title, "doc_type": "law"}}


def test_repeat_and_metadata_update_reuse_vectors(index, tmp_path):
    collection, texts, backend = index
    build.sync_index([chunk()], tmp_path, backend)
    assert texts == ["body"]
    result = build.sync_index([chunk(title="수정 제목")], tmp_path, backend)
    assert texts == ["body"] and result["embedded"] == 0
    assert collection.rows["a"][1]["title"] == "수정 제목"


def test_changed_text_embeds_only_changed_rows_and_removes_stale(index, tmp_path):
    collection, texts, backend = index
    build.sync_index([chunk(), chunk("b", "old"), chunk("c", "remove")], tmp_path, backend)
    texts.clear()
    result = build.sync_index([chunk(), chunk("b", "new"), chunk("d", "added")], tmp_path, backend)
    assert texts == ["new", "added"]
    assert result == {"embedded": 2, "reused": 1, "removed": 1}
    assert set(collection.rows) == {"a", "b", "d"}


@pytest.mark.parametrize("failure", ["verify", "receipt"])
def test_install_failure_restores_previous_data_and_preserves_web_db(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(build, "SCOPES", ("chunks/laws", "index/base"))
    staged, target, run = tmp_path / "stage", tmp_path / "data", tmp_path / "run"
    for root, content in ((staged, "new"), (target, "old")):
        for rel in build.SCOPES:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    web = target / "database/web.sqlite3"
    web.parent.mkdir()
    web.write_text("sessions")
    def fail(*args, **kwargs):
        raise ValueError("injected failure")
    monkeypatch.setattr(build, "run_worker", fail if failure == "verify" else lambda *a: {})
    if failure == "receipt":
        monkeypatch.setattr(build, "write", fail)
    with pytest.raises(ValueError, match="injected"):
        build.promote(staged, target, run)
    assert all((target / rel).read_text() == "old" for rel in build.SCOPES)
    assert web.read_text() == "sessions"


def test_partial_existing_data_requires_explicit_rebuild(tmp_path, monkeypatch):
    import setup_data
    monkeypatch.setattr(setup_data, "prepare_model", lambda **kwargs: None)
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "source_records", lambda: ([], [], []))
    monkeypatch.setattr(build, "recipe", lambda: {})
    monkeypatch.setattr(build, "model_identity", lambda: {})
    target = tmp_path / "data/chunks/chunks.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("old")
    with pytest.raises(ValueError, match="--rebuild"):
        build.prepare()
    assert target.read_text() == "old"


def test_django_command_calls_builder_without_llm(monkeypatch):
    from django.core.management import call_command
    import chat.management.commands.prepare_retrieval as command
    calls = []
    monkeypatch.setattr(command, "prepare", lambda *args: calls.append(args) or {"state": "unchanged"})
    call_command("prepare_retrieval", data_root="/server/data", rebuild=True)
    assert calls == [("/server/data", True)]
