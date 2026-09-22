from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from src.ingestion import server_build as build, server_sources as sources

REAL_PIPELINE = build.embedding_pipeline


def test_base_smoke_verification_does_not_attach_case_overlay(tmp_path, monkeypatch):
    from src.retrieval import retriever, dense, profile, service
    from src.retrieval.index import clean_metadata
    row = chunk()
    rows = {"ids": [row["chunk_id"]], "documents": [row["text"]],
            "metadatas": [clean_metadata(row["metadata"])]}
    empty = {"ids": [], "documents": [], "metadatas": []}
    for rel in build.INDEXES:
        path = tmp_path / rel / "chroma.sqlite3"
        path.parent.mkdir(parents=True)
        path.touch()
    monkeypatch.setattr(retriever, "load_chunks", lambda path: [row] if path == tmp_path / build.CHUNKS[0] else [])
    monkeypatch.setattr(build, "check_duplicates", lambda path: build.EXPECTED_COUNTS)
    monkeypatch.setattr(dense, "ChromaRetriever", lambda backend, path: SimpleNamespace(
        collection=SimpleNamespace(get=lambda **kw: rows if path == tmp_path / build.INDEXES[0] else empty)))
    monkeypatch.setattr(profile, "read_profile", lambda *a, **kw: {"index_hashes": ["hash", "hash"]})
    monkeypatch.setattr(build, "index_hash", lambda index: "hash")
    calls = []
    result = SimpleNamespace(laws=[1], civil_laws=[1], cases=[1], guides=[1])
    def base(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(search=lambda question: result)
    def overlay(**kwargs):
        raise AssertionError("base validation must not attach the released overlay")
    monkeypatch.setattr(service.RetrievalService, "_from_index_without_case_profile", base)
    monkeypatch.setattr(service.RetrievalService, "from_index", overlay)
    build.verify(tmp_path, smoke=True)
    assert len(calls) == 1
    assert calls[0]["index_path"] == tmp_path / build.INDEXES[0]


def test_sources_produce_complete_unique_corpus_without_database():
    laws, cases, guides = sources.source_records()
    assert len(laws) == 216 and len(cases) == 28 and len(guides) == 3
    assert len({(r.law_name, r.article_number) for r in laws}) == 216
    assert len([r for r in laws if r.law_name == "민법"]) == 31


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
@pytest.mark.parametrize("error", [ValueError, KeyboardInterrupt])
def test_install_failure_restores_previous_data_and_preserves_web_db(tmp_path, monkeypatch, failure, error):
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
        raise error("injected failure")
    monkeypatch.setattr(build, "run_worker", fail if failure == "verify" else lambda *a: {})
    if failure == "receipt":
        monkeypatch.setattr(build, "write", fail)
    with pytest.raises(error, match="injected"):
        build.promote(staged, target, run)
    assert all((target / rel).read_text(encoding="utf-8") == "old" for rel in build.SCOPES)
    assert web.read_text(encoding="utf-8") == "sessions"


@pytest.mark.parametrize("phase", ["before", "after"])
def test_interrupt_at_original_rename_restores_source_db(owned_build, monkeypatch, phase):
    data, _, _ = owned_build
    before = build.payload_hashes(data)
    original = Path.rename
    interrupted = False
    def rename(path, destination):
        nonlocal interrupted
        if path == data / "chunks/laws" and not interrupted:
            interrupted = True
            if phase == "after":
                original(path, destination)
            raise KeyboardInterrupt()
        return original(path, destination)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(KeyboardInterrupt):
        build.prepare(rebuild=True)
    assert build.payload_hashes(data) == before


def test_partial_existing_data_requires_explicit_rebuild(tmp_path, monkeypatch):
    import setup_data
    monkeypatch.setattr(setup_data, "prepare_model", lambda **kwargs: None)
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "source_records", lambda: ([], [], []))
    monkeypatch.setattr(build, "recipe", lambda: {})
    monkeypatch.setattr(build, "model_identity", lambda: {})
    monkeypatch.setattr(build, "embedding_pipeline", lambda: {})
    target = tmp_path / "data/chunks/chunks.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("old")
    with pytest.raises(ValueError, match="--rebuild"):
        build.prepare()
    assert target.read_text(encoding="utf-8") == "old"


@pytest.fixture
def owned_build(tmp_path, monkeypatch):
    import setup_data
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "SCOPES", ("chunks/laws", "index/base", build.BUILD))
    monkeypatch.setattr(setup_data, "prepare_model", lambda **k: None)
    monkeypatch.setattr(build, "source_records", lambda: ([], [], []))
    monkeypatch.setattr(build, "recipe", lambda: {"schema": "v1"})
    monkeypatch.setattr(build, "model_identity", lambda: {"weights": "same"})
    monkeypatch.setattr(build, "embedding_pipeline", lambda: {"pipeline": "v1"})
    def manifest():
        return {"version": 1, "source_fingerprint": build.fingerprint(([], [], [])),
                "recipe": build.recipe(), "model": build.MODEL,
                "model_identity": build.model_identity(), "embedding_pipeline": build.embedding_pipeline()}
    def populate(data, content):
        for rel in ("chunks/laws", "index/base/chroma.sqlite3"):
            p = data / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        build.write(data / build.BUILD, manifest())
    data = tmp_path / "data"
    populate(data, "old")
    calls = []
    def worker(action, target, run, previous=None):
        calls.append((action, previous))
        if action == "build":
            populate(target, "new")
        return {"counts": {}}
    monkeypatch.setattr(build, "run_worker", worker)
    return data, calls, worker


def test_owned_unchanged_build_is_skipped(owned_build):
    data, calls, _ = owned_build
    before = build.payload_hashes(data)
    assert build.prepare()["state"] == "unchanged"
    assert [action for action, _ in calls] == ["inspect"]
    assert build.payload_hashes(data) == before


@pytest.mark.parametrize("pipeline", ["same", "changed", "missing"])
def test_rebuild_reuses_vectors_only_with_matching_pipeline(owned_build, monkeypatch, pipeline):
    data, calls, _ = owned_build
    if pipeline == "changed":
        monkeypatch.setattr(build, "embedding_pipeline", lambda: {"pipeline": "v2"})
    elif pipeline == "missing":
        prior = build.read(data / build.BUILD)
        prior.pop("embedding_pipeline")
        build.write(data / build.BUILD, prior)
    assert build.prepare(rebuild=pipeline == "same")["state"] == "ready"
    previous = next(previous for action, previous in calls if action == "build")
    assert (previous is not None) == (pipeline == "same")
    assert build.read(data / build.BUILD)["embedding_pipeline"] == build.embedding_pipeline()


def test_schema_only_change_triggers_rebuild_and_keeps_compatible_vectors(owned_build, monkeypatch):
    _, calls, _ = owned_build
    monkeypatch.setattr(build, "recipe", lambda: {"schema": "v2"})
    assert build.prepare()["state"] == "ready"
    assert next(previous for action, previous in calls if action == "build") is not None


@pytest.mark.parametrize("damage", ["missing", "manifest", "inspect"])
@pytest.mark.parametrize("rebuild", [False, True])
def test_damaged_owned_data_requires_explicit_recovery(owned_build, monkeypatch, damage, rebuild):
    data, calls, worker = owned_build
    if damage == "missing":
        (data / "chunks/laws").unlink()
    elif damage == "manifest":
        (data / build.BUILD).write_text("not json")
    else:
        def broken(action, *args, **kwargs):
            if action == "inspect":
                raise ValueError("broken index")
            return worker(action, *args, **kwargs)
        monkeypatch.setattr(build, "run_worker", broken)
    before = build.payload_hashes(data)
    if rebuild:
        result = build.prepare(rebuild=True)
        assert result["state"] == "ready"
        assert ("build", None) in calls
        assert build.payload_hashes(Path(result["run"]) / "backup") == before
    else:
        with pytest.raises(ValueError, match="--rebuild"):
            build.prepare()
        assert build.payload_hashes(data) == before
        assert not any(action == "build" for action, _ in calls)


@pytest.mark.parametrize("phase", ["build", "verify"])
def test_failed_recovery_preserves_damaged_original(owned_build, monkeypatch, phase):
    data, _, worker = owned_build
    (data / "chunks/laws").unlink()
    before = build.payload_hashes(data)
    def failed(action, *args, **kwargs):
        if action == phase:
            raise ValueError("recovery failed")
        return worker(action, *args, **kwargs)
    monkeypatch.setattr(build, "run_worker", failed)
    with pytest.raises(ValueError, match="recovery failed"):
        build.prepare(rebuild=True)
    assert build.payload_hashes(data) == before


def test_schema_and_embedding_inputs_are_fingerprinted(tmp_path, monkeypatch):
    names = list(build.recipe())
    for name in names:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("initial")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    initial = build.recipe()
    (tmp_path / "src/database/schema.sql").write_text("changed schema")
    assert build.recipe() != initial
    for name in ("src/retrieval/dense.py", "src/retrieval/index.py",
                 "src/ingestion/server_build.py", "requirements.txt"):
        previous = build.embedding_pipeline()
        (tmp_path / name).write_text("new pipeline")
        assert build.embedding_pipeline() != previous


@pytest.mark.parametrize("package", ["torch", "chromadb", "sentence-transformers", "transformers", "tokenizers"])
def test_installed_version_change_forces_reembedding(owned_build, monkeypatch, package):
    data, calls, _ = owned_build
    for name in ("src/retrieval/dense.py", "src/retrieval/index.py",
                 "src/ingestion/server_build.py", "requirements.txt"):
        p = data.parent / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("unchanged code")
    monkeypatch.setattr(build, "embedding_pipeline", REAL_PIPELINE)
    monkeypatch.setattr(build.metadata, "version", lambda name: "1.0")
    prior = build.read(data / build.BUILD)
    prior["embedding_pipeline"] = build.embedding_pipeline()
    build.write(data / build.BUILD, prior)
    assert build.prepare()["state"] == "unchanged"
    monkeypatch.setattr(build.metadata, "version", lambda name: "2.0" if name == package else "1.0")
    assert build.prepare()["state"] == "ready"
    assert ("build", None) in calls
    assert build.read(data / build.BUILD)["embedding_pipeline"]["packages"][package] == "2.0"


def test_base_prepare_checks_pinned_snapshot_without_main_ref(owned_build, monkeypatch):
    import setup_data
    calls = []
    monkeypatch.setattr(setup_data, "prepare_model", lambda **kwargs: calls.append(kwargs))
    assert build.prepare()["state"] == "unchanged"
    assert calls == [{"check": True, "pinned_only": True}]


def test_missing_dependency_version_is_not_recorded_as_compatible(monkeypatch):
    def missing(name):
        raise build.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(build.metadata, "version", missing)
    with pytest.raises(ValueError, match="패키지 버전"):
        build.embedding_pipeline()


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
    assert (tmp_path / "build.log").read_text(encoding="utf-8") == "progress\n"
