from hashlib import sha256
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.evaluation.chatting_provenance import capture_ollama_identity, capture_retrieval_provenance, retrieval_semantically_equal, streaming_sha256


@pytest.fixture
def ready_service(tmp_path):
    chunk = {"chunk_id": "law-1", "text": "평가용 근거", "metadata": {"source": "synthetic"}}
    civil_chunk = {"chunk_id": "civil-1", "text": "가상 민법", "metadata": {"article_id": "민법-제623조", "title": "민법", "source": "synthetic"}}
    source = tmp_path / "data/chunks/chunks.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(chunk) + "\n" + json.dumps(civil_chunk) + "\n")
    def retriever(role):
        path = tmp_path / f"data/index/{role}"
        (path / "segment").mkdir(parents=True)
        (path / "chroma.sqlite3").write_bytes(b"synthetic sqlite")
        (path / "segment/data_level0.bin").write_bytes(b"synthetic vectors")
        indexed = deepcopy(chunk if role == "base" else civil_chunk)
        payload = {"ids": [indexed["chunk_id"]], "documents": [indexed["text"]], "metadatas": [indexed["metadata"]], "embeddings": [[0.25, -0.5]]}
        collection = SimpleNamespace(name="knowledge_chunks", metadata={"hnsw:space": "cosine"}, count=lambda: len(payload["ids"]), get=lambda **kwargs: deepcopy(payload), payload=payload)
        backend = SimpleNamespace(name="nlpai-lab/KURE-v1")
        return SimpleNamespace(path=path, collection=collection, backend=backend)
    return SimpleNamespace(_chunks={"law-1": chunk, "civil-1": civil_chunk}, civil=SimpleNamespace(include_ids=("민법-제623조",)), dense=retriever("base"), civil_dense=retriever("civil"))


def test_streaming_digest_matches_sha256_and_does_not_use_read_bytes(tmp_path, monkeypatch):
    data = b"sample" * 300000
    path = tmp_path / "large.bin"
    path.write_bytes(data)
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("Unbounded read"))
    assert streaming_sha256(path) == sha256(data).hexdigest()


def test_loaded_hybrid_artifacts_and_sources_are_recorded(ready_service, tmp_path):
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert result["ready"] is True
    assert result["issues"] == []
    assert result["mode"] == "hybrid"
    assert result["chunks"]["count"] == result["chunks"]["source_matched_count"] == 2
    assert result["sources"]["data/chunks/chunks.jsonl"]["matching_loaded_chunk_count"] == 2
    assert result["indexes"]["base"]["embedding"]["model"] == "nlpai-lab/KURE-v1"
    assert result["indexes"]["base"]["count"] == 1
    assert set(result["indexes"]["base"]["files"]) == {"chroma.sqlite3", "segment/data_level0.bin"}
    assert "평가용 근거" not in json.dumps(result, ensure_ascii=False)
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("attribute,issue", [("dense", "base_dense_missing"), ("civil_dense", "civil_dense_missing")])
def test_lexical_fallback_cannot_pass_hybrid_readiness(ready_service, tmp_path, attribute, issue):
    setattr(ready_service, attribute, None)
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert result["ready"] is False
    assert issue in result["issues"]


def test_loaded_chunk_changes_cannot_be_hidden_by_identical_disk_files(ready_service, tmp_path):
    before = capture_retrieval_provenance(ready_service, root=tmp_path)
    ready_service._chunks["law-1"]["text"] = "다른 근거"
    after = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert before["chunks"]["sha256"] != after["chunks"]["sha256"]
    assert "loaded_chunks_without_matching_source" in after["issues"]


def test_changed_vector_file_changes_provenance(ready_service, tmp_path):
    before = capture_retrieval_provenance(ready_service, root=tmp_path)
    (ready_service.dense.path / "segment/data_level0.bin").write_bytes(b"new vectors")
    after = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert before["indexes"]["base"]["files"] != after["indexes"]["base"]["files"]


def test_repeat_capture_is_read_only_and_stable(ready_service, tmp_path):
    files_before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    first = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert first == capture_retrieval_provenance(ready_service, root=tmp_path)
    assert files_before == {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}


def test_empty_collection_fails_preparation(ready_service, tmp_path):
    ready_service.dense.collection.count = lambda: 0
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert "base_collection_unavailable_or_empty" in result["issues"]
    assert result["ready"] is False


def test_missing_sqlite_fails_even_when_collection_is_cached(ready_service, tmp_path):
    (ready_service.dense.path / "chroma.sqlite3").unlink()
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert "base_index_fingerprint_failed" in result["issues"]


def test_exception_messages_and_backend_secrets_are_not_recorded(ready_service, tmp_path):
    secret = "sk-proj-" + "s" * 30
    ready_service.dense.backend.name = secret
    def fail():
        raise OSError(f"https://private-host/?token={secret}")
    ready_service.dense.collection.count = fail
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    serialized = json.dumps(result)
    assert secret not in serialized
    assert "private-host" not in serialized
    assert result["ready"] is False


def test_embedding_revision_uses_loaded_model_without_embedding(ready_service, tmp_path):
    config = SimpleNamespace(_commit_hash="a" * 40)
    loaded = SimpleNamespace(_first_module=lambda: SimpleNamespace(auto_model=SimpleNamespace(config=config)), get_sentence_embedding_dimension=lambda: 2)
    ready_service.dense.backend._model = loaded
    ready_service.dense.backend.embed = lambda _: pytest.fail("Embedding must not run")
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert result["indexes"]["base"]["embedding"] == {"model": "nlpai-lab/KURE-v1", "revision": "a" * 40, "dimensions": 2}


def test_model_identity_records_digest_without_endpoints_or_extra_fields(monkeypatch):
    from src.evaluation import chatting_provenance
    def metadata(url, timeout):
        if url.endswith("/api/version"):
            return {"version": "0.13.0", "secret": "do not persist"}
        return {"models": [{"name": "qwen3:8b", "digest": "f" * 64, "size": 1234, "endpoint": "private", "password": "do not persist", "details": {"family": "qwen3", "quantization_level": "Q4_K_M"}}]}
    monkeypatch.setattr(chatting_provenance, "_read_json", metadata)
    result = capture_ollama_identity("qwen3:8b", base_url="http://private-host/v1")
    assert result["available"] is True
    assert result["digest"] == "f" * 64
    assert result["ollama_version"] == "0.13.0"
    assert "private" not in json.dumps(result)
    assert "do not persist" not in json.dumps(result)


def test_model_identity_failure_never_records_exception_contents(monkeypatch):
    from src.evaluation import chatting_provenance
    def fail(*args):
        raise OSError("http://private-host/?token=secret")
    monkeypatch.setattr(chatting_provenance, "_read_json", fail)
    result = capture_ollama_identity("qwen3:8b", base_url="http://private-host")
    assert result == {"available": False, "model": "qwen3:8b", "reason": "model_identity_unavailable"}


def test_similar_model_prefix_is_not_the_requested_identity(monkeypatch):
    from src.evaluation import chatting_provenance
    monkeypatch.setattr(chatting_provenance, "_read_json", lambda *_: {"models": [{"name": "qwen3:8b-other", "digest": "a" * 64}]})
    assert capture_ollama_identity("qwen3:8b", base_url="http://localhost")["available"] is False


def test_three_required_civil_articles_missing_from_both_memory_and_index(ready_service, tmp_path):
    missing = ("민법-제105조", "민법-제114조", "민법-제357조")
    ready_service.civil.include_ids += missing
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert result["ready"] is False
    assert "required_civil_articles_missing_from_loaded_chunks" in result["issues"]
    assert "required_civil_articles_missing_from_civil_index" in result["issues"]
    assert result["coverage"]["missing_loaded_civil_articles"] == sorted(missing)
    assert result["coverage"]["missing_indexed_civil_articles"] == sorted(missing)


def test_wrong_index_text_is_distinguished_from_missing_civil_articles(ready_service, tmp_path):
    ready_service.civil_dense.collection.payload["documents"][0] = "오래된 다른 조문"
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert result["ready"] is False
    assert "civil_document_text_mismatch" in result["issues"]
    assert result["indexes"]["civil"]["content"]["text_mismatch_count"] == 1
    assert result["coverage"]["missing_indexed_civil_articles"] == []


def test_wrong_metadata_is_reported_independently(ready_service, tmp_path):
    ready_service.civil_dense.collection.payload["metadatas"][0]["source"] = "old source"
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert "civil_metadata_mismatch" in result["issues"]
    assert "civil_document_text_mismatch" not in result["issues"]


def test_index_metadata_uses_same_cleaning_as_ingestion(ready_service, tmp_path):
    ready_service._chunks["law-1"]["metadata"].update(tags=["one", "two"], optional=None)
    ready_service.dense.collection.payload["metadatas"][0].update(tags="one|two", optional="")
    source = tmp_path / "data/chunks/chunks.jsonl"
    source.write_text("\n".join(json.dumps(chunk) for chunk in ready_service._chunks.values()))
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert result["ready"] is True


def test_cross_index_duplicate_ids_fail_even_when_union_matches_memory(ready_service, tmp_path):
    base = ready_service.dense.collection.payload
    civil = ready_service.civil_dense.collection.payload
    for key in ("ids", "documents", "metadatas", "embeddings"):
        civil[key] += deepcopy(base[key])
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert "base_civil_index_ids_overlap" in result["issues"]
    assert result["coverage"]["cross_index_duplicate_count"] == 1
    assert result["coverage"]["loaded_chunk_ids_missing_from_indexes"] == 0


def test_unknown_index_chunk_and_missing_loaded_chunk_are_distinguished(ready_service, tmp_path):
    ready_service.dense.collection.payload["ids"][0] = "unknown-stale-id"
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert "base_unknown_index_chunks" in result["issues"]
    assert "index_ids_do_not_match_loaded_chunks" in result["issues"]
    assert result["coverage"]["index_chunk_ids_absent_from_memory"] == 1
    assert result["coverage"]["loaded_chunk_ids_missing_from_indexes"] == 1


def test_collection_get_reads_stored_vectors_without_querying_or_embedding(ready_service, tmp_path):
    calls = []
    original = ready_service.civil_dense.collection.get
    def get(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    ready_service.civil_dense.collection.get = get
    ready_service.civil_dense.collection.query = lambda **kwargs: pytest.fail("No vector query")
    ready_service.civil_dense.backend.embed = lambda _: pytest.fail("No embedding")
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert result["ready"] is True
    assert calls == [{"include": ["documents", "metadatas", "embeddings"]}]


def test_collection_read_failure_never_exposes_error_contents(ready_service, tmp_path):
    def fail(**kwargs):
        raise RuntimeError("endpoint/private?api_key=hidden-value")
    ready_service.civil_dense.collection.get = fail
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert result["ready"] is False
    assert "civil_collection_content_unavailable" in result["issues"]
    assert "hidden-value" not in json.dumps(result)


def test_required_article_present_in_memory_but_missing_from_index(ready_service, tmp_path):
    ready_service.civil_dense.collection.payload["metadatas"][0]["article_id"] = "민법-제1조"
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert result["coverage"]["missing_loaded_civil_articles"] == []
    assert result["coverage"]["missing_indexed_civil_articles"] == ["민법-제623조"]
    assert "required_civil_articles_missing_from_civil_index" in result["issues"]
    assert "required_civil_articles_missing_from_loaded_chunks" not in result["issues"]


def test_duplicate_ids_within_one_collection_fail_readiness(ready_service, tmp_path):
    payload = ready_service.dense.collection.payload
    for key in ("ids", "documents", "metadatas", "embeddings"):
        payload[key] += deepcopy(payload[key])
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert result["ready"] is False
    assert "base_duplicate_index_ids" in result["issues"]
    assert result["coverage"]["loaded_chunk_ids_missing_from_indexes"] == 0


def test_physical_index_rewrite_preserves_logical_identity(ready_service, tmp_path):
    before = capture_retrieval_provenance(ready_service, root=tmp_path)
    (ready_service.dense.path / "chroma.sqlite3").write_bytes(b"database housekeeping")
    (ready_service.dense.path / "segment/data_level0.bin").write_bytes(b"reserialized hnsw")
    after = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert before["indexes"]["base"]["files"] != after["indexes"]["base"]["files"]
    assert retrieval_semantically_equal(before, after)


def test_changed_vector_is_not_hidden_by_same_documents_and_metadata(ready_service, tmp_path):
    before = capture_retrieval_provenance(ready_service, root=tmp_path)
    ready_service.dense.collection.payload["embeddings"][0][0] += 0.001
    after = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert before["indexes"]["base"]["content"]["sha256"] == after["indexes"]["base"]["content"]["sha256"]
    assert before["indexes"]["base"]["content"]["vectors_sha256"] != after["indexes"]["base"]["content"]["vectors_sha256"]
    assert not retrieval_semantically_equal(before, after)


def test_metadata_change_fails_logical_equality(ready_service, tmp_path):
    before = capture_retrieval_provenance(ready_service, root=tmp_path)
    ready_service.dense.collection.payload["metadatas"][0]["source"] = "changed"
    after = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert not retrieval_semantically_equal(before, after)


def test_matching_preparation_failures_never_count_as_equal(ready_service, tmp_path):
    ready_service.civil_dense.collection.payload["embeddings"] = None
    before = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert before["ready"] is False
    assert not retrieval_semantically_equal(before, deepcopy(before))


def test_old_snapshot_without_vector_identity_is_not_comparable(ready_service, tmp_path):
    before = capture_retrieval_provenance(ready_service, root=tmp_path)
    before["indexes"]["base"]["content"].pop("vectors_sha256")
    assert not retrieval_semantically_equal(before, deepcopy(before))


@pytest.mark.parametrize("vector", [[float("nan"), 0.5], [float("inf"), 0.5], [], [True, 0.5]])
def test_invalid_vectors_fail_preparation(ready_service, tmp_path, vector):
    ready_service.dense.collection.payload["embeddings"] = [vector]
    result = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert result["ready"] is False
    assert "base_collection_content_unavailable" in result["issues"]


def test_array_like_vectors_hash_the_same_as_plain_lists(ready_service, tmp_path):
    before = capture_retrieval_provenance(ready_service, root=tmp_path)
    vector_rows = deepcopy(ready_service.dense.collection.payload["embeddings"])
    ready_service.dense.collection.payload["embeddings"] = SimpleNamespace(tolist=lambda: vector_rows)
    after = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert retrieval_semantically_equal(before, after)


def test_collection_distance_metadata_change_is_not_physical_only(ready_service, tmp_path):
    before = capture_retrieval_provenance(ready_service, root=tmp_path)
    ready_service.dense.collection.metadata["hnsw:space"] = "l2"
    after = capture_retrieval_provenance(ready_service, root=tmp_path)
    assert before["indexes"]["base"]["content"] == after["indexes"]["base"]["content"]
    assert not retrieval_semantically_equal(before, after)
