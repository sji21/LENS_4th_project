"""Product policy parity, opt-in compatibility and reversible data activation."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import patch027_context_tuning as trial
from scripts import patch027_final_policy as final
from scripts import patch027_rollout as rollout
from src.evaluation.baseline import settings
from src.retrieval import context_policy as policy
from src.retrieval import profile as profiles
from src.retrieval.expanded import CIVIL_IDS, ExpandedLawRetrievalService, RequestEmbeddingCache
from src.retrieval.service import RetrievalService


def chunk(article, title="민법", text="임대차 계약 비용 반환"):
    return {"chunk_id": article, "text": text, "metadata": {
        "doc_type": "law", "title": title, "article_id": article,
        "article_no": article.split("-")[-1], "article_title": "준용규정" if "654" in article else "",
        "status": "current", "version": "법률 제1호", "effective_date": "2026-01-01"}}


def test_product_pure_policy_matches_frozen_trial_for_all_235_inputs():
    queries = rollout.read(rollout.ROOT / "data/eval/patch015-baseline/capture/results.json")
    assert len(queries) == 235
    for row in queries:
        q = row["query"]
        assert policy.final_law_terms(q) == final.final_law_terms(q)
        assert policy.final_dense_query(q) == final.final_dense_query(q)
        assert policy.context_terms(q, "civil") == trial.context_terms(q, "civil")
        assert policy.dense_context_query(q, "civil") == trial.dense_context_query(q, "civil")


@pytest.mark.parametrize("body,expected", [
    ("[민법 제654조] 제615조의 규정은 임대차에 준용한다.", True),
    ("[민법 제654조] 다른법 제615조의 규정은 임대차에 준용한다.", False),
    ("[민법 제654조] 제615조와 표현만 함께 나온다.", False),
])
def test_reference_pairs_preserve_verified_source_relationship(body, expected):
    chunks = [chunk("민법-제615조"), chunk("민법-제654조", text=body)]
    graph, evidence = policy._reference_graph(chunks, CIVIL_IDS)
    assert ("민법-제654조" in graph["민법-제615조"]) is expected
    assert (graph, evidence) == trial._reference_graph(chunks)


@pytest.fixture
def payload(tmp_path):
    data = tmp_path / "data"
    chunks = [chunk(a) for a in CIVIL_IDS] + [chunk("주택임대차보호법-제3조", "주택임대차보호법")]
    for rel in profiles.FILES:
        dest = data / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("" if rel.endswith(".jsonl") else "synthetic database", encoding="utf-8")
    (data / profiles.CHUNKS[0]).write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks), encoding="utf-8")
    profile = {"version": 1, "policy": profiles.POLICY, "model": "nlpai-lab/KURE-v1",
        "civil_ids": list(CIVIL_IDS), "files": {p: profiles.file_hash(data / p) for p in profiles.FILES},
        "index_hashes": ["a" * 64, "b" * 64]}
    (data / profiles.PROFILE).parent.mkdir(parents=True, exist_ok=True)
    rollout.write(data / profiles.PROFILE, profile)
    return data, chunks, profile


def test_profile_opt_in_and_legacy_data_compatibility(payload):
    data, chunks, _ = payload
    paths = tuple(data / p for p in profiles.CHUNKS)
    service = RetrievalService.from_local_chunks(chunks, paths)
    assert isinstance(service, ExpandedLawRetrievalService)
    assert service.civil.include_ids == CIVIL_IDS
    result = service.search("계약 비용 반환", k_case=0, k_guide=0)
    assert len(result.civil_laws) <= 3
    assert all(not e.citation.startswith("민법") for e in result.laws)
    assert service.search("계약", k_law=0, k_civil=0, k_case=0, k_guide=0).is_empty()
    (data / profiles.PROFILE).unlink()
    legacy = RetrievalService.from_local_chunks(chunks, paths)
    assert type(legacy) is RetrievalService
    assert len(legacy.civil.include_ids) == 10


@pytest.mark.parametrize("change", ["files", "bytes", "civil_ids", "policy", "boolean_version", "malformed", "loaded_chunks", "model", "paths"])
def test_profile_rejects_mismatched_payload(payload, change):
    data, chunks, profile = payload
    paths = tuple(data / p for p in profiles.CHUNKS)
    model = profile["model"]
    if change == "files":
        profile["files"].pop("database/civil.sqlite3")
    elif change == "bytes":
        (data / "database/civil.sqlite3").write_text("changed", encoding="utf-8")
    elif change == "civil_ids":
        profile["civil_ids"].pop()
    elif change == "policy":
        profile["policy"] = "unknown"
    elif change == "boolean_version":
        profile["version"] = True
    elif change == "malformed":
        profile = None
    elif change == "loaded_chunks":
        chunks = chunks[:-1]
    elif change == "model":
        model = "different-model"
    else:
        paths = paths[:1]
    rollout.write(data / profiles.PROFILE, profile)
    with pytest.raises(profiles.RetrievalProfileError):
        profiles.read_profile(data, chunks, paths, model=model)


def test_indexes_must_match_profile_and_cannot_mix_roots(payload, monkeypatch):
    data, chunks, _ = payload
    paths = tuple(data / p for p in profiles.CHUNKS)
    indexes = tuple(data / p for p in profiles.INDEXES)
    monkeypatch.setattr(profiles, "index_hash", lambda _: "c" * 64)
    with pytest.raises(profiles.RetrievalProfileError, match="인덱스"):
        profiles.build_profiled_service(chunks, paths, object(), object(), index_paths=indexes)
    with pytest.raises(profiles.RetrievalProfileError):
        profiles.build_profiled_service(chunks, (data.parent / "old/chunks/chunks.jsonl",), index_paths=indexes)
    with pytest.raises(profiles.RetrievalProfileError):
        profiles.build_profiled_service(chunks, (), index_paths=indexes)


def test_product_factory_and_generation_fallback_use_activated_profile(payload, monkeypatch):
    from src.generation import chain
    from src.retrieval import dense as dense_module, service as service_module

    data, chunks, profile = payload
    paths = tuple(data / p for p in profiles.CHUNKS)
    indexes = tuple(data / p for p in profiles.INDEXES)
    for path in indexes:
        path.mkdir(parents=True, exist_ok=True)
        (path / "chroma.sqlite3").write_bytes(b"synthetic index")
    backend = SimpleNamespace(name=profile["model"], embed=lambda texts: [[1]])
    monkeypatch.setattr(service_module, "SentenceTransformerEmbedding", lambda _: backend)
    def fake_dense(backend, path):
        return SimpleNamespace(backend=backend, path=path, collection=SimpleNamespace(get=lambda **kw: {"ids": []}))
    monkeypatch.setattr(dense_module, "ChromaRetriever", fake_dense)
    monkeypatch.setattr(profiles, "index_hash", lambda r: profile["index_hashes"][indexes.index(r.path)])
    built = RetrievalService.from_index(paths, indexes[0], civil_index_path=indexes[1])
    assert isinstance(built, ExpandedLawRetrievalService)
    def unavailable(*args, **kwargs):
        raise OSError("embedding unavailable")
    monkeypatch.setattr(RetrievalService, "from_index", unavailable)
    monkeypatch.setattr(chain, "fallback_chunk_paths", lambda: paths)
    fallback = chain._build_service()
    assert isinstance(fallback, ExpandedLawRetrievalService)
    assert fallback.civil.include_ids == CIVIL_IDS and fallback.dense is None
    (data / "database/civil.sqlite3").write_bytes(b"corrupt")
    with pytest.raises(profiles.RetrievalProfileError):
        chain._build_service()


def test_settings_report_actual_active_retriever(payload):
    _, chunks, _ = payload
    service = ExpandedLawRetrievalService(chunks)
    service._context_law.depth = 31
    report = settings(service)
    assert report["corpora"]["law"]["retriever"]["depth"] == 31
    assert report["corpora"]["civil"]["include_ids"] == CIVIL_IDS
    assert report["corpora"]["law"]["query_expander"].endswith("final_law_terms")
    assert report["corpora"]["civil"]["seed_retriever"] is not None
    assert "profile" not in settings(RetrievalService(chunks))


def test_embedding_cache_is_per_context_and_request():
    context = ContextVar("test_cache", default=None)
    calls = []
    backend = SimpleNamespace(name="fake", embed=lambda texts: calls.append(texts) or [[1]])
    first, second = RequestEmbeddingCache(backend, context), RequestEmbeddingCache(backend, context)
    def request(_):
        token = context.set({})
        try:
            assert first.embed(["same"]) == second.embed(["same"])
        finally:
            context.reset(token)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(request, range(4)))
    assert len(calls) == 4
    first.embed(["same"])
    first.embed(["same"])
    assert len(calls) == 6


def test_service_cache_restores_context_on_exception_without_mutating_shared_dense(payload, monkeypatch):
    _, chunks, _ = payload
    backend = SimpleNamespace(name="fake", embed=lambda texts: [[1]])
    dense = SimpleNamespace(backend=backend)
    service = ExpandedLawRetrievalService(chunks, dense, dense)
    assert dense.backend is backend
    assert service.dense is not dense
    assert service.dense.backend.delegate is service.civil_dense.backend.delegate
    def fail(*args, **kwargs):
        assert service._embedding_cache.get() == {}
        raise RuntimeError("injected")
    monkeypatch.setattr(RetrievalService, "search", fail)
    with pytest.raises(RuntimeError, match="injected"):
        service.search("q")
    assert service._embedding_cache.get() is None


def small_payload(root, prefix, profile=True):
    for rel in rollout.SCOPES:
        if rel == profiles.PROFILE and not profile:
            continue
        dest = root / rel
        if rel in profiles.INDEXES:
            dest = dest / "vectors.bin"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(prefix + rel, encoding="utf-8")


def test_install_and_restore_preserve_all_old_bytes_including_absent_profile(tmp_path):
    old, staged, backup = (tmp_path / p for p in ("old", "staged", "backup"))
    small_payload(old, "before", profile=False)
    small_payload(staged, "after")
    before = rollout.payload_hashes(old)
    assert rollout.install_payload(staged, old, backup) == before
    assert rollout.payload_hashes(old) == rollout.payload_hashes(staged)
    assert rollout.payload_hashes(backup / "snapshot") == before
    rollout.install_payload(backup / "snapshot", old, tmp_path / "restore")
    assert rollout.payload_hashes(old) == before


def test_failed_swap_restores_every_previously_replaced_path(tmp_path, monkeypatch):
    old, staged, backup = (tmp_path / p for p in ("old", "staged", "backup"))
    small_payload(old, "before", profile=False)
    small_payload(staged, "after")
    before = rollout.payload_hashes(old)
    original = Path.rename
    def fail_one(path, dest):
        if path == backup / "incoming" / profiles.INDEXES[1]:
            raise OSError("injected locked file")
        return original(path, dest)
    monkeypatch.setattr(Path, "rename", fail_one)
    with pytest.raises(OSError, match="locked"):
        rollout.install_payload(staged, old, backup)
    assert rollout.payload_hashes(old) == before


def test_rollout_rejects_paths_outside_intended_directory(tmp_path):
    with pytest.raises(ValueError):
        rollout.child(tmp_path, "../outside")
    with pytest.raises(ValueError):
        rollout.child(tmp_path, ".")


def test_execution_source_hash_handles_windows_mixed_line_endings_only():
    expected = rollout.source_hash(b"first\nsecond\n")
    assert rollout.source_hash(b"first\r\nsecond\n") == expected
    assert rollout.source_hash(b"first\r\nsecond\r\n") == expected
    assert rollout.source_hash(b"first\nmodified\n") != expected
