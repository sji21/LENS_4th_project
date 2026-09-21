"""Product policy parity, opt-in compatibility and reversible data activation."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import patch027_context_tuning as trial
from scripts import patch027_final_policy as final
from scripts import patch027_rollout as rollout
from src.evaluation.baseline import settings
from src.retrieval import context_policy as policy
from src.retrieval import profile as profiles
from src.retrieval.expanded import (CIVIL_IDS, LEGACY_CIVIL_IDS, LEGACY_POLICY,
                                    ExpandedLawRetrievalService, RequestEmbeddingCache)
from src.retrieval.service import RetrievalService


def chunk(article, title="민법", text="임대차 계약 비용 반환"):
    return {"chunk_id": article, "text": text, "metadata": {
        "doc_type": "law", "title": title, "article_id": article,
        "article_no": article.split("-")[-1], "article_title": "준용규정" if "654" in article else "",
        "status": "current", "version": "법률 제1호", "effective_date": "2026-01-01"}}


def test_product_policy_retains_prior_terms_except_documented_semantic_extensions():
    # PATCH-058 expands record eligibility and handover vocabulary. The old
    # pure-function equality is no longer the v2 contract; frozen queries and
    # captures remain untouched and live retrieval regression is checked separately.
    queries = rollout.read(rollout.ROOT / "data/eval/patch015-baseline/capture/results.json")
    assert len(queries) == 235
    record_extension = "정보제공 요청 이해관계인 임차인 범위"
    for row in queries:
        q = row["query"]
        expected = final.final_law_terms(q)
        actual = policy.final_law_terms(q)
        assert [term for term in actual if term != record_extension] == expected
        expected_query = final.final_dense_query(q)
        assert policy.final_dense_query(q) == (expected_query + "; " + record_extension
                                                if record_extension in actual else expected_query)
        # The historical 235 questions contain no newly supported key-handover
        # wording, so their existing civil expansion is still identical.
        assert policy.context_terms(q, "civil") == trial.context_terms(q, "civil")
        assert policy.dense_context_query(q, "civil") == trial.dense_context_query(q, "civil")


@pytest.fixture(autouse=True)
def isolate_product_profile_from_installed_case_overlay(monkeypatch):
    monkeypatch.setattr("src.retrieval.case_profile.configured_case_profile", lambda: "")


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
    # A local .env set up for the team MySQL release must not leak into this path.
    monkeypatch.setenv("LENS_MYSQL_RELEASE", "")
    paths = tuple(data / p for p in profiles.CHUNKS)
    indexes = tuple(data / p for p in profiles.INDEXES)
    for path in indexes:
        path.mkdir(parents=True, exist_ok=True)
        (path / "chroma.sqlite3").write_bytes(b"synthetic index")
    backend = SimpleNamespace(name=profile["model"], embed=lambda texts: [[1]])
    revisions = []
    def fake_embedding(model, *, revision=None):
        revisions.append(revision)
        return backend
    monkeypatch.setattr(service_module, "SentenceTransformerEmbedding", fake_embedding)
    def fake_dense(backend, path):
        return SimpleNamespace(backend=backend, path=path, collection=SimpleNamespace(get=lambda **kw: {"ids": []}))
    monkeypatch.setattr(dense_module, "ChromaRetriever", fake_dense)
    monkeypatch.setattr(profiles, "index_hash", lambda r: profile["index_hashes"][indexes.index(r.path)])
    built = RetrievalService.from_index(paths, indexes[0], civil_index_path=indexes[1])
    assert isinstance(built, ExpandedLawRetrievalService)
    assert revisions == ["4ed4540949c70b7da2c74004a915e1f2d5e46e4f"]
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


@pytest.mark.parametrize("phase", ["before", "after"])
def test_interrupted_bundle_rename_restores_all_bytes(tmp_path, monkeypatch, phase):
    old, staged, backup = (tmp_path / p for p in ("old", "staged", "backup"))
    small_payload(old, "before", profile=False)
    small_payload(staged, "after")
    before = rollout.payload_hashes(old)
    rename = Path.rename
    interrupted = False
    def interrupt(path, dest):
        nonlocal interrupted
        if path == old / profiles.INDEXES[1] and not interrupted:
            interrupted = True
            if phase == "after":
                rename(path, dest)
            raise KeyboardInterrupt()
        return rename(path, dest)
    monkeypatch.setattr(Path, "rename", interrupt)
    with pytest.raises(KeyboardInterrupt):
        rollout.install_payload(staged, old, backup)
    assert rollout.payload_hashes(old) == before


@pytest.fixture
def apply_fixture(tmp_path, monkeypatch):
    """Isolate apply's filesystem transaction from expensive preflight/model checks."""
    target, staged, backup = tmp_path / "data", tmp_path / "staged", tmp_path / "tmp/backup"
    small_payload(target, "before", profile=False)
    small_payload(staged, "after")
    rollout.write(staged / profiles.PROFILE, {"policy": "synthetic"})
    verification = tmp_path / "verification"
    verification.mkdir()
    rollout.write(verification / "manifest.json", {})
    baseline = tmp_path / "data/eval/patch027-full/capture/audit.json"
    baseline.parent.mkdir(parents=True)
    rollout.write(baseline, {"data_hashes": {"data/" + p: rollout.sha(target / p) for p in profiles.FILES}})
    audit = {"source_hashes": {}, "profile": rollout.read(staged / profiles.PROFILE),
             "file_hashes": {p: rollout.sha(staged / p) for p in profiles.FILES | {profiles.PROFILE}}}
    monkeypatch.setattr(rollout, "ROOT", tmp_path)
    monkeypatch.setattr(rollout, "check_verification", lambda _: audit)
    monkeypatch.setattr(rollout, "check_final", lambda: None)
    monkeypatch.setattr(rollout, "code_snapshot", lambda: {})
    monkeypatch.setattr(rollout.subprocess, "run", lambda *a, **kw: None)
    return target, staged, backup, verification


@pytest.mark.parametrize("failure", ["before_write", "partial_write", "invalid_write"])
def test_apply_receipt_failure_restores_original_without_receipt(apply_fixture, monkeypatch, failure):
    target, staged, backup, verification = apply_fixture
    before = rollout.payload_hashes(target)
    def fail_receipt(path, value):
        assert path == backup / "receipt.json"
        assert rollout.payload_hashes(target) == rollout.payload_hashes(staged)
        if failure != "before_write":
            path.write_text("{" if failure == "partial_write" else "{}", encoding="utf-8")
        if failure != "invalid_write":
            raise OSError("injected disk full")
    monkeypatch.setattr(rollout, "write", fail_receipt)
    with pytest.raises((OSError, ValueError)):
        rollout.apply(staged, verification, backup)
    assert rollout.payload_hashes(target) == before
    assert rollout.payload_hashes(backup / "snapshot") == before
    assert not (target / profiles.PROFILE).exists()
    assert rollout.payload_hashes(backup / "failed") == rollout.payload_hashes(staged)


def test_successful_apply_writes_receipt_accepted_by_restore(apply_fixture):
    target, staged, backup, verification = apply_fixture
    before = rollout.payload_hashes(target)
    rollout.apply(staged, verification, backup)
    assert rollout.payload_hashes(target) == rollout.payload_hashes(staged)
    assert rollout.read(backup / "receipt.json")["before"] == before
    rollout.restore(backup, backup.parent / "restore-preserved")
    assert rollout.payload_hashes(target) == before


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


def test_published_preflight_and_adopted_results_replay_with_backup_receipt():
    bundle = rollout.ROOT / "data/eval/patch027-rollout"
    manifest = rollout.read(bundle / "manifest.json")
    assert set(manifest) == {"preflight/manifest.json", "adopted/manifest.json", "receipt.json"}
    assert all(rollout.sha(bundle / p) == h for p, h in manifest.items())
    for name, is_default in (("preflight", False), ("adopted", True)):
        audit = rollout.check_verification(bundle / name)
        assert audit["default_data_path"] is is_default
        assert audit["model_calls"] == 375
        assert audit["runtime_settings"]["corpora"]["civil"]["include_ids"] == list(LEGACY_CIVIL_IDS)
    receipt = rollout.read(bundle / "receipt.json")
    baseline = rollout.read(rollout.ROOT / "data/eval/patch027-full/capture/audit.json")
    assert receipt["target"] == "data"
    assert receipt["profile"] == rollout.expected_profile()
    assert receipt["verification_manifest_sha256"] == rollout.sha(bundle / "preflight/manifest.json")
    assert all(receipt["before"][p.removeprefix("data/")] == h for p, h in baseline["data_hashes"].items())
    assert receipt["before"][profiles.PROFILE] is None


@pytest.mark.parametrize("change", ["evidence", "profile", "source", "missing"])
def test_rehashed_rollout_capture_tampering_is_rejected(tmp_path, change):
    dest = tmp_path / "capture"
    shutil.copytree(rollout.ROOT / "data/eval/patch027-rollout/adopted", dest)
    manifest = rollout.read(dest / "manifest.json")
    if change == "missing":
        (dest / "rows.json").unlink()
        manifest.pop("rows.json")
    else:
        filename = "rows.json" if change == "evidence" else "audit.json"
        value = rollout.read(dest / filename)
        if change == "evidence":
            value[0]["result"]["laws"][0]["source_url"] = "https://example.invalid/other-law"
        elif change == "profile":
            value["profile"]["civil_ids"].pop()
        else:
            value["source_hashes"]["src/retrieval/expanded.py"] = "0" * 64
        rollout.write(dest / filename, value)
        manifest[filename] = rollout.sha(dest / filename)
    rollout.write(dest / "manifest.json", manifest)
    with pytest.raises(ValueError):
        rollout.check_verification(dest)
