"""Release boundaries: mixed versions, corruption, activation and app routing."""
import json
from pathlib import Path

import pytest

from src.ingestion import mysql_search as builder
from src.retrieval import mysql_release as runtime
from src.retrieval.expanded import CIVIL_IDS, LEGACY_CIVIL_IDS, LEGACY_POLICY, POLICY


def chunk(cid, kind="law", title="민법"):
    return {"chunk_id": cid, "doc_id": "doc", "chunk_index": 0,
            "text": "보증금 반환과 임대차 계약 " + cid,
            "metadata": {"doc_type": kind, "title": title, "article_id": cid,
                         "source_url": "https://example.org/source"}}


def export(root, corpus, rows):
    root.mkdir(parents=True)
    files = {}
    for stream, items in rows.items():
        p = root / (stream + ".jsonl")
        p.write_bytes(b"".join(runtime.encoded(r) + b"\n" for r in items))
        files[p.name] = runtime.file_hash(p)
    snap = {"schema": "lens-mysql-snapshot-v1", "corpus": corpus, "version": "test-v1",
            "tables": {}, "streams": {s: {"count": len(r), "sha256": runtime.digest(r)}
                                       for s, r in rows.items()}}
    manifest = {"schema": "lens-mysql-chunks-v1", "snapshot": snap,
                "snapshot_id": runtime.digest(snap), "files": files, "index_status": "not_built"}
    builder.write_json(root / "manifest.json", manifest)
    return manifest


def make_bundle(tmp_path, civil_ids=CIVIL_IDS, law_policy=POLICY,
                base_cases=None, release_cases=None):
    root = tmp_path / "release"
    civil = [chunk(cid) for cid in civil_ids]
    base_cases = [chunk("old-case", "case", "판례")] if base_cases is None else base_cases
    release_cases = [chunk("new-case", "case", "판례")] if release_cases is None else release_cases
    data = {"base": {"laws": [chunk("law-1", title="주택임대차보호법")] + civil,
                     "cases": base_cases,
                     "guides": [chunk("guide", "guide", "안내")]},
            "civil": {"civil": civil},
            "cases": {"laws": [chunk("old-law", title="주택임대차보호법")],
                      "cases": release_cases, "guides": []}}
    snapshots = {c: export(root / "exports" / c, c, rows)["snapshot_id"]
                 for c, rows in data.items()}
    rows = runtime.channel_rows(data, law_policy)
    indexes = {}
    for channel in runtime.STREAMS:
        path = root / "indexes" / channel
        path.mkdir(parents=True)
        (path / "index.bin").write_bytes(channel.encode())
        indexes[channel] = {"path": "indexes/" + channel, "count": len(rows[channel]),
                            "logical_sha256": "0" * 64}
    release = {"schema": runtime.SCHEMA, "index_status": "ready", "fallback_allowed": False,
               "snapshots": snapshots, "indexes": indexes, "case_policy": runtime.CASE_POLICY,
               "law_policy": law_policy, "code_files": runtime.code_files(),
               "runtime_versions": runtime.runtime_versions(),
               "model": {"model_id": "nlpai-lab/KURE-v1", "dimension": 1024,
                         "revision": "a" * 40, "files": {n: "0" * 64 for n in runtime.MODEL_FILES}},
               "files": {p.relative_to(root).as_posix(): runtime.file_hash(p)
                         for p in root.rglob("*") if p.is_file()}}
    seal(root / "release.json", release)
    return root / "release.json", data


@pytest.fixture
def bundle(tmp_path):
    return make_bundle(tmp_path)


def seal(path, release):
    release.pop("release_id", None)
    release["release_id"] = runtime.digest(release)
    builder.write_json(path, release)


def test_snapshot_selection_preserves_channels_and_rejects_mixed_civil(bundle):
    path, data = bundle
    _, _, rows = runtime.read_release(path)
    assert [r["chunk_id"] for r in rows["base"]] == ["law-1", "old-case", "guide"]
    assert [r["chunk_id"] for r in rows["cases"]] == ["old-law", "new-case"]
    assert len(rows["civil"]) == len(CIVIL_IDS)
    data["civil"]["civil"] = [dict(r, text="different version") for r in data["civil"]["civil"]]
    with pytest.raises(ValueError, match="민법 청크"):
        runtime.channel_rows(data)


@pytest.mark.parametrize("law_policy,civil_ids", [
    (LEGACY_POLICY, LEGACY_CIVIL_IDS),
    (POLICY, CIVIL_IDS),
])
def test_release_loads_the_civil_scope_declared_by_its_policy(
        tmp_path, monkeypatch, law_policy, civil_ids):
    from src.retrieval import dense
    path, _ = make_bundle(tmp_path, civil_ids, law_policy)
    _, release, rows = runtime.read_release(path)
    assert release["law_policy"] == law_policy
    assert tuple(r["metadata"]["article_id"] for r in rows["civil"]) == civil_ids

    monkeypatch.setattr(runtime, "verify_model", lambda directory, spec: Path(directory))
    monkeypatch.setattr(dense, "SentenceTransformerEmbedding", lambda *a, **k: object())
    monkeypatch.setattr(runtime, "open_indexes", lambda *a: dict.fromkeys(runtime.STREAMS))
    service = runtime.load_service(path, "test-model")
    assert service.base_service.profile_name == law_policy
    assert service.base_service.civil.include_ids == civil_ids


@pytest.mark.parametrize("civil_ids,law_policy", [
    (CIVIL_IDS, LEGACY_POLICY),
    (LEGACY_CIVIL_IDS, POLICY),
])
def test_policy_with_the_other_civil_scope_is_rejected(tmp_path, civil_ids, law_policy):
    path, data = make_bundle(tmp_path, civil_ids,
                             POLICY if civil_ids == CIVIL_IDS else LEGACY_POLICY)
    with pytest.raises(ValueError, match="정책과 민법 조문"):
        runtime.channel_rows(data, law_policy)
    release = json.loads(path.read_text())
    release["law_policy"] = law_policy
    seal(path, release)
    with pytest.raises(ValueError, match="정책과 민법 조문"):
        runtime.read_release(path)


def test_duplicate_civil_article_is_rejected_even_with_a_different_chunk_id(bundle):
    _, data = bundle
    duplicate = dict(data["civil"]["civil"][0],
                     chunk_id=data["civil"]["civil"][0]["chunk_id"] + "-duplicate")
    data["civil"]["civil"].append(duplicate)
    data["base"]["laws"].append(duplicate)
    with pytest.raises(ValueError, match="중복"):
        runtime.channel_rows(data, POLICY)


@pytest.mark.parametrize("law_policy,civil_ids", [
    (LEGACY_POLICY, LEGACY_CIVIL_IDS),
    (POLICY, CIVIL_IDS),
])
def test_builder_records_the_policy_matching_its_civil_export(
        tmp_path, monkeypatch, law_policy, civil_ids):
    from src.ingestion import knowledge_release
    from src.retrieval import dense, index
    staging = tmp_path / "staging"
    civil = [chunk(cid) for cid in civil_ids]
    data = {"base": {"laws": [chunk("law-1", title="주택임대차보호법")] + civil,
                     "cases": [], "guides": []},
            "civil": {"civil": civil},
            "cases": {"laws": [], "cases": [chunk("case", "case", "판례")],
                      "guides": []}}
    for corpus, rows in data.items():
        export(staging / "exports" / corpus, corpus, rows)

    case_root = tmp_path / "case-release"
    case_index = case_root / "index"
    case_index.mkdir(parents=True)
    (case_index / "index.bin").write_bytes(b"case-index")
    case_release = case_root / "release.json"
    case_release.write_text("{}")
    audit = json.loads((runtime.ROOT / "data/eval/patch027-full/capture/audit.json").read_text())
    model_files = {"/".join(Path(name).parts[2:]): value
                   for name, value in audit["model_files"].items()}
    model = {"model_id": "nlpai-lab/KURE-v1", "revision": "a" * 40,
             "dimension": 1024, "files": model_files}
    source = {"embedding_model": model, "retrieval_policy": runtime.CASE_POLICY,
              "index": {"path": "index", "logical_sha256": "1" * 64}}
    monkeypatch.setattr(knowledge_release, "read_release", lambda path: source)
    monkeypatch.setattr(builder, "verify_model", lambda *args: None)
    monkeypatch.setattr(builder, "audit_tokens", lambda rows, model_dir: {"checked": len(rows)})
    monkeypatch.setattr(builder, "verify_collection", lambda *args: "2" * 64)
    monkeypatch.setattr(builder, "write_vector_reference",
                        lambda collection, path: (Path(path).parent.mkdir(parents=True, exist_ok=True),
                                                  Path(path).write_bytes(b"vectors")))
    monkeypatch.setattr(dense, "SentenceTransformerEmbedding", lambda *args, **kwargs: object())

    class Dense:
        def __init__(self, backend, path):
            self.collection = object()

    def build_index(rows, backend, path, **kwargs):
        Path(path).mkdir(parents=True)
        (Path(path) / "index.bin").write_bytes(b"built-index")

    monkeypatch.setattr(dense, "ChromaRetriever", Dense)
    monkeypatch.setattr(index, "build_index", build_index)
    builder.build_worker(staging, tmp_path / "model", case_release=case_release)
    result = json.loads((staging / "worker-result.json").read_text())
    assert result["law_policy"] == law_policy
    for channel in runtime.STREAMS:
        assert result["indexes"][channel]["vector_reference"] == f"references/{channel}.f32"
        assert (staging / "references" / f"{channel}.f32").is_file()


@pytest.mark.parametrize("file", ["exports/base/laws.jsonl", "indexes/cases/index.bin",
                                 "exports/civil/manifest.json"])
def test_corruption_is_rejected_before_search(bundle, file):
    path, _ = bundle
    (path.parent / file).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="해시"):
        runtime.read_release(path)


def test_incomplete_or_changed_policy_release_is_rejected(bundle):
    path, _ = bundle
    release = json.loads(path.read_text())
    del release["files"]["indexes/cases/index.bin"]
    seal(path, release)
    with pytest.raises(ValueError, match="목록"):
        runtime.read_release(path)
    release["case_policy"]["return_k"] = 2
    seal(path, release)
    with pytest.raises(ValueError, match="정책"):
        runtime.read_release(path)


def test_vector_reference_must_be_listed_at_its_fixed_path(bundle):
    path, _ = bundle
    release = json.loads(path.read_text())
    reference = path.parent / "references" / "cases.f32"
    reference.parent.mkdir()
    reference.write_bytes(b"vectors")
    release["indexes"]["cases"]["vector_reference"] = "references/cases.f32"
    seal(path, release)
    with pytest.raises(ValueError, match="목록"):
        runtime.read_release(path)
    release["files"]["references/cases.f32"] = runtime.file_hash(reference)
    seal(path, release)
    runtime.read_release(path)
    release["indexes"]["cases"]["vector_reference"] = "indexes/cases/index.bin"
    seal(path, release)
    with pytest.raises(ValueError, match="기준 벡터 경로"):
        runtime.read_release(path)


class VectorCollection:
    """Minimal Chroma stand-in whose stored vectors can differ in their last bits."""
    configuration = {"hnsw": {"space": "cosine"}}

    def __init__(self, rows, vectors):
        self.rows, self.vectors = rows, vectors

    def count(self):
        return len(self.rows)

    def get(self, ids=None, include=()):
        ids = [r["chunk_id"] for r in self.rows] if ids is None else ids
        rows = {r["chunk_id"]: r for r in self.rows}
        return {"ids": ids, "documents": [rows[i]["text"] for i in ids],
                "metadatas": [rows[i]["metadata"] for i in ids],
                "embeddings": [self.vectors[i] for i in ids]}


@pytest.mark.parametrize("shift, accepted", [(0.0, True), (1.5e-8, True), (1e-4, False)])
def test_cross_cpu_vector_bits_are_accepted_only_within_tolerance(
        tmp_path, monkeypatch, shift, accepted):
    import numpy as np
    from src.retrieval import case_profile
    rows = [chunk("b-law"), chunk("a-law")]
    built = {r["chunk_id"]: np.full(1024, 0.03125, dtype="<f4") * (i + 1)
             for i, r in enumerate(rows)}
    reference = tmp_path / "cases.f32"
    runtime.write_vector_reference(VectorCollection(rows, built), reference)
    opened = {cid: (v + np.float32(shift)).astype("<f4") for cid, v in built.items()}
    monkeypatch.setattr(case_profile, "index_content_hash", lambda c: "other-cpu-hash")
    collection = VectorCollection(rows, opened)
    with pytest.raises(ValueError, match="논리 해시"):
        runtime.verify_collection(collection, rows, "build-cpu-hash")
    if accepted:
        assert runtime.verify_collection(collection, rows, "build-cpu-hash",
                                         reference=reference) == "other-cpu-hash"
    else:
        with pytest.raises(ValueError, match="허용 오차"):
            runtime.verify_collection(collection, rows, "build-cpu-hash", reference=reference)


def test_vector_reference_of_another_index_is_rejected(tmp_path, monkeypatch):
    import numpy as np
    from src.retrieval import case_profile
    rows = [chunk("law")]
    reference = tmp_path / "base.f32"
    runtime.write_vector_reference(
        VectorCollection(rows + [chunk("extra")], {"law": np.zeros(1024), "extra": np.zeros(1024)}),
        reference)
    monkeypatch.setattr(case_profile, "index_content_hash", lambda c: "other-cpu-hash")
    with pytest.raises(ValueError, match="크기"):
        runtime.verify_collection(VectorCollection(rows, {"law": np.zeros(1024)}), rows,
                                  "build-cpu-hash", reference=reference)


def test_activation_verifies_before_atomic_pointer_replacement(bundle, tmp_path, monkeypatch):
    path, _ = bundle
    pointer = tmp_path / "active.json"
    pointer.write_text("previous version")
    def broken(*args):
        raise ValueError("bad stored vectors")
    monkeypatch.setattr(builder, "verify_release", broken)
    with pytest.raises(ValueError, match="vectors"):
        builder.activate(path, pointer)
    assert pointer.read_text() == "previous version"
    monkeypatch.setattr(builder, "verify_release", lambda path: {"verified": True})
    builder.activate(path, pointer)
    assert runtime.resolve_release(pointer) == path.resolve()
    path.write_text("corrupted after activation")
    with pytest.raises(ValueError, match="해시"):
        runtime.resolve_release(pointer)


def test_explicit_mysql_release_never_falls_back_to_another_corpus(monkeypatch):
    from src.retrieval.service import RetrievalService
    from src.generation.chain import _build_service
    monkeypatch.setenv(runtime.RELEASE_ENV, "missing-release.json")
    monkeypatch.delenv("LENS_CASE_RETRIEVAL_PROFILE", raising=False)
    calls = []
    def fail(path):
        calls.append(path)
        raise ValueError("invalid MySQL release")
    monkeypatch.setattr(runtime, "load_service", fail)
    with pytest.raises(ValueError, match="invalid MySQL"):
        _build_service()
    assert calls == ["missing-release.json"]
    with pytest.raises(ValueError, match="동시에"):
        RetrievalService.from_index(index_path="custom")
    monkeypatch.setenv("LENS_CASE_RETRIEVAL_PROFILE", "old-profile")
    with pytest.raises(ValueError, match="동시에"):
        RetrievalService.from_index()


def test_same_ids_with_changed_index_text_or_metadata_are_rejected():
    row = chunk("law")
    class Collection:
        configuration = {"hnsw": {"space": "cosine"}}
        def count(self):
            return 1
        def get(self, **kwargs):
            return {"ids": [row["chunk_id"]], "documents": ["stale text"],
                    "metadatas": [row["metadata"]]}
    with pytest.raises(ValueError, match="본문"):
        runtime.verify_collection(Collection(), [row])


def test_versioned_evidence_reaches_existing_generation_chain(bundle, monkeypatch):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from scripts.case_corpus_llm import generate
    from src.retrieval import dense
    from src.retrieval.service import Evidence, RetrievalResult
    path, _ = bundle
    monkeypatch.setattr(runtime, "verify_model", lambda directory, spec: Path(directory))
    monkeypatch.setattr(dense, "SentenceTransformerEmbedding", lambda *a, **k: object())
    monkeypatch.setattr(runtime, "open_indexes", lambda *a: dict.fromkeys(runtime.STREAMS))
    service = runtime.load_service(path, "test-model")
    channels = {name: [Evidence(rank=1, chunk_id=cid, doc_type=kind, citation=name,
                               text="검증 본문 " + name, score=0.1,
                               source_url="https://example.org/" + cid)]
                for name, cid, kind in [("laws", "law-1", "law"), ("cases", "new-case", "case"),
                                        ("civil_laws", CIVIL_IDS[0], "law"), ("guides", "guide", "guide")]}
    result = RetrievalResult(question="임대차 보증금 반환은?", **channels)
    payload = service.evidence_payload(result)
    snaps = service.mysql_release["snapshots"]
    for channel, corpus in [("laws", "base"), ("guides", "base"),
                            ("cases", "cases"), ("civil_laws", "civil")]:
        assert payload["channels"][channel][0]["snapshot_id"] == snaps[corpus]
    captured = []
    def respond(value):
        captured.append(value.to_string())
        return AIMessage(content="연결 검증 응답")
    generated = generate(payload, RunnableLambda(respond))
    for name in channels:
        assert "검증 본문 " + name in captured[0]
    assert generated["evidence"]["release_id"] == payload["release_id"]
    assert generated["application_quality_verified"] is False


def test_case_evidence_uses_the_snapshot_of_its_selected_source(tmp_path, monkeypatch):
    from src.retrieval import dense
    from src.retrieval.service import Evidence, RetrievalResult

    def supplement(cid, key):
        row = chunk(cid, "case", "판례")
        row["metadata"].update(status="current", corpus_role="case_supplement",
                               canonical_case_key=key)
        return row

    base_case = supplement("base-supplement", "a" * 64)
    duplicate = supplement("frozen-case", "b" * 64)
    path, _ = make_bundle(tmp_path, base_cases=[base_case, duplicate],
                          release_cases=[chunk("frozen-case", "case", "판례")])
    monkeypatch.setattr(runtime, "verify_model", lambda directory, spec: Path(directory))
    monkeypatch.setattr(dense, "SentenceTransformerEmbedding", lambda *a, **k: object())
    monkeypatch.setattr(runtime, "open_indexes", lambda *a: dict.fromkeys(runtime.STREAMS))
    service = runtime.load_service(path, "test-model")
    assert [item["chunk_id"] for item in service.case_supplement_manifest] == ["base-supplement"]

    cases = [Evidence(rank=rank, chunk_id=cid, doc_type="case", citation=cid,
                      text="판례 본문", score=0.1, source_url="https://example.org/" + cid)
             for rank, cid in enumerate(("frozen-case", "base-supplement"), 1)]
    payload = service.evidence_payload(RetrievalResult(
        question="판례 원천은?", laws=[], cases=cases, civil_laws=[], guides=[]))
    snapshots = service.mysql_release["snapshots"]
    assert [item["snapshot_id"] for item in payload["channels"]["cases"]] == [
        snapshots["cases"], snapshots["base"]]


def test_reused_index_embedding_provenance_is_validated(monkeypatch):
    from src.retrieval import case_profile
    from hashlib import sha256
    row = chunk("case", "case", "판례")
    model = {"model_id": "nlpai-lab/KURE-v1", "revision": "a" * 40}
    meta = dict(row["metadata"], embedding_fingerprint=model["model_id"] + "@" + model["revision"],
                embedding_input_hash=sha256(row["text"].encode()).hexdigest())
    class Collection:
        configuration = {"hnsw": {"space": "cosine"}}
        def count(self):
            return 1
        def get(self, **kwargs):
            return {"ids": [row["chunk_id"]], "documents": [row["text"]], "metadatas": [meta]}
    monkeypatch.setattr(case_profile, "index_content_hash", lambda c: "verified-vector-hash")
    assert runtime.verify_collection(Collection(), [row], model=model) == "verified-vector-hash"
    meta["embedding_input_hash"] = "0" * 64
    with pytest.raises(ValueError, match="메타데이터"):
        runtime.verify_collection(Collection(), [row], model=model)


def test_runtime_package_drift_is_rejected(bundle, monkeypatch):
    path, _ = bundle
    monkeypatch.setattr(runtime, "runtime_versions", lambda: {"chromadb": "different"})
    with pytest.raises(ValueError):
        runtime.read_release(path)


def test_dependency_bootstrap_does_not_require_already_matching_runtime(bundle, monkeypatch):
    import sys
    path, _ = bundle
    # A fresh virtual environment has only pip; bootstrap must not need packaging.
    monkeypatch.setitem(sys.modules, "packaging", None)
    monkeypatch.setitem(sys.modules, "packaging.version", None)
    expected = json.loads(path.read_text())["runtime_versions"]
    monkeypatch.setattr(runtime, "runtime_versions", lambda: {"chromadb": "different"})
    pins = builder.dependency_requirements(path)
    for package, version in expected.items():
        assert package + "==" + version + "\n" in pins
    release = json.loads(path.read_text())
    release["runtime_versions"]["torch"] = "2.14.0\n--extra-index-url https://example.org"
    seal(path, release)
    with pytest.raises(ValueError):
        builder.dependency_requirements(path)


def test_incremental_index_update_reuses_only_identical_text_vectors():
    import hashlib
    model = {"model_id": "model", "revision": "fixed"}
    keep, edit, remove, new = [chunk(cid) for cid in ("keep", "edit", "remove", "new")]
    original_vector = [0.25] * 1024
    class Collection:
        def __init__(self):
            self.rows = {r["chunk_id"]: (r["text"], dict(r["metadata"],
                embedding_fingerprint="model@fixed", embedding_input_hash=hashlib.sha256(r["text"].encode()).hexdigest()),
                original_vector[:]) for r in (keep, edit, remove)}
        def get(self, **kwargs):
            return {"ids": list(self.rows), "documents": [r[0] for r in self.rows.values()],
                    "metadatas": [r[1] for r in self.rows.values()], "embeddings": [r[2] for r in self.rows.values()]}
        def upsert(self, ids, documents, metadatas, embeddings):
            self.rows.update({cid: (text, meta, vec) for cid, text, meta, vec in zip(ids, documents, metadatas, embeddings)})
        def delete(self, ids):
            for cid in ids:
                del self.rows[cid]
    collection = Collection()
    calls = []
    class Backend:
        def embed(self, texts):
            calls.extend(texts)
            return [[0.5] * 1024 for _ in texts]
    keep["metadata"]["status"] = "historical"
    edit["text"] = "수정된 본문"
    stats = builder.sync_collection(collection, [keep, edit, new], Backend, model)
    assert calls == [edit["text"], new["text"]]
    assert stats == {"embedded": 2, "reused": 1, "removed": 1, "upserted": 3}
    assert set(collection.rows) == {"keep", "edit", "new"}
    assert collection.rows["keep"][2] == original_vector
    assert collection.rows["keep"][1]["status"] == "historical"
    def forbidden():
        raise AssertionError("unchanged input must not load the embedding model")
    assert builder.sync_collection(collection, [keep, edit, new], forbidden, model) == {
        "embedded": 0, "reused": 3, "removed": 0, "upserted": 0}


def test_model_token_limit_is_checked_before_silent_truncation(tmp_path, monkeypatch):
    from transformers import AutoTokenizer
    (tmp_path / "sentence_bert_config.json").write_text('{"max_seq_length": 5}')
    calls = []
    def tokenize(texts, **kwargs):
        calls.append(kwargs)
        return {"input_ids": [[1] * len(t) for t in texts]}
    monkeypatch.setattr(AutoTokenizer, "from_pretrained", lambda *a, **k: tokenize)
    assert builder.audit_tokens([dict(chunk("ok"), text="abcd")], tmp_path) == {
        "limit": 5, "maximum": 4, "checked": 1}
    with pytest.raises(ValueError, match="입력 한도"):
        builder.audit_tokens([dict(chunk("long"), text="abcdef")], tmp_path)
    assert all(c == {"truncation": False, "add_special_tokens": True} for c in calls)


def test_incremental_update_with_real_chroma():
    import subprocess
    import sys
    import tempfile
    # Exit the child before cleaning up native SQLite/HNSW handles on Windows.
    program = '''
import sys, chromadb
from src.ingestion.mysql_search import sync_collection
from src.retrieval.mysql_release import verify_collection
def vector(n):
    values=[0.0]*1024
    values[n]=1.0
    return values
def row(cid,text):
    return {"chunk_id":cid,"text":text,"metadata":{"doc_type":"case","source_url":"https://example.test/"+cid}}
old=[row("keep","same"),row("edit","old"),row("delete","remove")]
c=chromadb.PersistentClient(path=sys.argv[1]).create_collection("knowledge_chunks",metadata={"hnsw:space":"cosine"})
c.add(ids=[r["chunk_id"] for r in old],documents=[r["text"] for r in old],metadatas=[r["metadata"] for r in old],embeddings=[vector(i) for i in range(3)])
new=[old[0],row("edit","changed"),row("new","added")]
class Backend:
    def embed(self,texts):
        assert texts==["changed","added"]
        return [vector(3),vector(4)]
model={"model_id":"fixture","revision":"fixed"}
stats=sync_collection(c,new,Backend,model)
assert stats["embedded"]==2 and stats["reused"]==1 and stats["removed"]==1
verify_collection(c,new,model=model)
assert c.query(query_embeddings=[vector(4)],n_results=1)["ids"]==[["new"]]
assert list(c.get(ids=["keep"],include=["embeddings"])["embeddings"][0])==vector(0)
'''
    with tempfile.TemporaryDirectory(prefix="lens-chroma-update-test-") as directory:
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", program, directory], capture_output=True, encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
