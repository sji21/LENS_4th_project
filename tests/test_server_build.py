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


def test_source_url_pins_version_article_and_branch():
    from urllib.parse import urlparse, parse_qs
    fields = parse_qs(urlparse(sources.article_url("276291", "제3조의3")).query)
    assert fields["lsiSeq"] == ["276291"]
    assert fields["joNo"] == ["0003"] and fields["joBrNo"] == ["03"]
    assert "joBrNo=00" in sources.article_url("284415", "제114조")


def test_source_url_rejects_mixed_article_reference():
    with pytest.raises(ValueError, match="번호 형식"):
        sources.article_url("276291", "제3조 제5항")


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


def test_failed_inspection_log_survives_temporary_snapshot_cleanup(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(build, "ROOT", tmp_path)
    popen = build.subprocess.Popen
    def failed(command, **kwargs):
        return popen([build.sys.executable, "-u", "-X", "utf8", "-c",
                      "import sys; sys.stderr.write('index mismatch'); sys.exit(1)"], **kwargs)
    monkeypatch.setattr(build.subprocess, "Popen", failed)
    with tempfile.TemporaryDirectory(dir=tmp_path) as directory:
        with pytest.raises(ValueError, match="실패"):
            build.run_worker("inspect", tmp_path / "data", Path(directory))
    saved = list((tmp_path / "tmp/server-build/errors").glob("*.log"))
    assert len(saved) == 1 and saved[0].read_bytes() == b"index mismatch"


def test_worker_stdout_and_stderr_reach_console_and_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build, "ROOT", tmp_path)
    popen = build.subprocess.Popen
    def success(command, **kwargs):
        assert "-u" in command
        script = ("import sys; from pathlib import Path; "
                  "print('임베딩 16/210', flush=True); "
                  "print('worker warning', file=sys.stderr, flush=True); "
                  "Path('build-result.json').write_text('{\"ready\": true}')")
        return popen([build.sys.executable, "-u", "-X", "utf8", "-c", script], **kwargs)
    monkeypatch.setattr(build.subprocess, "Popen", success)
    assert build.run_worker("build", tmp_path / "data", tmp_path) == {"ready": True}
    output = capsys.readouterr().out
    assert "임베딩 16/210" in output and "worker warning" in output
    assert (tmp_path / "build.log").read_text(encoding="utf-8") == "임베딩 16/210\nworker warning\n"


def test_worker_progress_is_flushed_before_worker_finishes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build, "ROOT", tmp_path)
    def lines():
        yield "임베딩 16/210\n"
        assert "임베딩 16/210" in capsys.readouterr().out
        assert (tmp_path / "build.log").read_text(encoding="utf-8") == "임베딩 16/210\n"
        yield "임베딩 32/210\n"
    class Process:
        stdout = lines()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def wait(self):
            build.write(tmp_path / "build-result.json", {"ready": True})
            return 0
    monkeypatch.setattr(build.subprocess, "Popen", lambda *a, **k: Process())
    assert build.run_worker("build", tmp_path / "data", tmp_path) == {"ready": True}


@pytest.mark.parametrize("error", [KeyboardInterrupt, OSError])
def test_interrupted_output_stops_worker_before_releasing_control(tmp_path, monkeypatch, error):
    monkeypatch.setattr(build, "ROOT", tmp_path)
    events = []
    class Process:
        stdout = iter(["progress\n"])
        def __enter__(self):
            return self
        def __exit__(self, *args):
            events.append("exit")
        def poll(self):
            return None
        def kill(self):
            events.append("kill")
        def wait(self):
            events.append("wait")
            return -1
    def interrupted(text, **kwargs):
        if text == "progress\n":
            raise error()
    monkeypatch.setattr(build, "print", interrupted, raising=False)
    monkeypatch.setattr(build.subprocess, "Popen", lambda *a, **k: Process())
    with pytest.raises(error):
        build.run_worker("build", tmp_path / "data", tmp_path)
    assert events == ["kill", "wait", "exit"]
    assert (tmp_path / "build.log").read_text() == "progress\n"
