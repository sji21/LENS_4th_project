"""Read-only identities for an already-loaded retrieval service and Ollama.

No model is loaded or embedded here. File hashes cover on-disk artifacts while
the canonical chunk hash identifies the text actually held by the service.
"""

from collections.abc import Mapping
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
import urllib.request

from src.security.secret_filter import redact_secrets

ROOT = Path(__file__).resolve().parents[2]
_BLOCK_SIZE = 1024 * 1024


def streaming_sha256(path):
    """Hash bounded blocks; reject a file changed during the read."""
    path = Path(path)
    before = path.stat()
    digest = sha256()
    with path.open("rb") as source:
        while block := source.read(_BLOCK_SIZE):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise RuntimeError("Artifact changed during fingerprinting")
    return digest.hexdigest()


def _label(path, root, fallback):
    try:
        label = Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return fallback
    return redact_secrets(label).text


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,199}", value):
        return None
    if "://" in value or redact_secrets(value).contains_secret:
        return None
    return value


def _artifact(path):
    if path.is_symlink():
        raise ValueError("Symlink is not a reproducible artifact")
    return {"sha256": streaming_sha256(path), "size_bytes": path.stat().st_size}


def _embedding_identity(backend):
    result = {"model": _identity(getattr(backend, "name", None)), "revision": None, "dimensions": None}
    model = getattr(backend, "_model", None)
    if model is not None:
        try:
            config = model._first_module().auto_model.config
            revision = getattr(config, "_commit_hash", None)
            if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40,64}", revision):
                result["revision"] = revision
            dimension = model.get_sentence_embedding_dimension()
            if type(dimension) is int and dimension > 0:
                result["dimensions"] = dimension
        except (AttributeError, IndexError, TypeError):
            # Some embedding backends expose only their public model identifier.
            pass
    return result


def _index_manifest(retriever, role, root, issues):
    if retriever is None:
        issues.append(f"{role}_dense_missing")
        return {"available": False}
    result = {"available": True, "embedding": _embedding_identity(getattr(retriever, "backend", None))}
    collection = getattr(retriever, "collection", None)
    result["collection"] = _identity(getattr(collection, "name", None))
    try:
        metadata = getattr(collection, "metadata", None)
        result["collection_metadata_sha256"] = sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        result["collection_metadata_sha256"] = None
        issues.append(f"{role}_collection_metadata_unavailable")
    try:
        count = collection.count()
        if type(count) is not int or count <= 0:
            raise ValueError("Empty or invalid collection")
        result["count"] = count
    except Exception:
        result["count"] = None
        issues.append(f"{role}_collection_unavailable_or_empty")
    if result["embedding"]["model"] is None:
        issues.append(f"{role}_embedding_identity_unavailable")
    raw_path = getattr(retriever, "path", None)
    if not isinstance(raw_path, (str, Path)):
        issues.append(f"{role}_index_path_unavailable")
        return result
    path = Path(raw_path)
    if not path.is_absolute():
        path = root / path
    result["path"] = _label(path, root, f"external_{role}_index")
    result["files"] = {}
    try:
        if path.is_symlink() or not (path / "chroma.sqlite3").is_file():
            raise ValueError("Chroma database missing")
        for file in sorted(path.rglob("*")):
            if file.is_symlink():
                raise ValueError("Symlink in index")
            if file.is_file():
                result["files"][redact_secrets(file.relative_to(path).as_posix()).text] = _artifact(file)
    except Exception:
        issues.append(f"{role}_index_fingerprint_failed")
    return result


def _collection_content(retriever, role, chunks, expected_count, expected_dimension, issues):
    """Read stored vectors/text/metadata without search, embedding, or disclosure."""
    from src.retrieval.index import clean_metadata

    if retriever is None:
        return {"available": False}, set(), set()
    try:
        result = retriever.collection.get(include=["documents", "metadatas", "embeddings"])
        ids, documents, metadata = result["ids"], result["documents"], result["metadatas"]
        vectors = result["embeddings"]
        if hasattr(vectors, "tolist"):
            vectors = vectors.tolist()
        if not all(isinstance(values, list) for values in (ids, documents, metadata)):
            raise ValueError("Invalid collection result")
        if not isinstance(vectors, (list, tuple)) or len(ids) != len(vectors) or len(ids) != len(documents) or len(ids) != len(metadata):
            raise ValueError("Unaligned collection contents")
        if not all(isinstance(value, str) for value in ids):
            raise ValueError("Invalid collection identity")
        if not all(isinstance(value, str) for value in documents) or not all(isinstance(value, dict) for value in metadata):
            raise ValueError("Missing collection contents")
        unique_ids = set(ids)
        if len(unique_ids) != len(ids):
            issues.append(f"{role}_duplicate_index_ids")
        if len(ids) != expected_count:
            issues.append(f"{role}_collection_count_changed")
        digest = sha256()
        vector_digest = sha256()
        vector_dimensions = set()
        text_mismatches = metadata_mismatches = unknown_chunks = 0
        articles = set()
        entries = sorted(zip(ids, documents, metadata, vectors), key=lambda entry: entry[0])
        for chunk_id, document, meta, vector in entries:
            if hasattr(vector, "tolist"):
                vector = vector.tolist()
            if not isinstance(vector, (list, tuple)) or not vector:
                raise ValueError("Missing embedding vector")
            if not all(type(number) in (int, float) and isfinite(number) for number in vector):
                raise ValueError("Embedding vector contains invalid values")
            vector_dimensions.add(len(vector))
            vector_digest.update(json.dumps([chunk_id, [float(number) for number in vector]], ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
            vector_digest.update(b"\n")
            digest.update(json.dumps([chunk_id, document, meta], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
            digest.update(b"\n")
            article = meta.get("article_id")
            if isinstance(article, str):
                articles.add(article)
            chunk = chunks.get(chunk_id)
            if chunk is None:
                unknown_chunks += 1
                continue
            text_mismatches += document != chunk.get("text")
            metadata_mismatches += meta != clean_metadata(chunk.get("metadata", {}))
        if text_mismatches:
            issues.append(f"{role}_document_text_mismatch")
        if metadata_mismatches:
            issues.append(f"{role}_metadata_mismatch")
        if unknown_chunks:
            issues.append(f"{role}_unknown_index_chunks")
        if len(vector_dimensions) != 1 or expected_dimension is not None and vector_dimensions != {expected_dimension}:
            issues.append(f"{role}_embedding_dimension_mismatch")
        return {
            "available": True,
            "entry_count": len(ids),
            "unique_id_count": len(unique_ids),
            "sha256": digest.hexdigest(),
            "vectors_sha256": vector_digest.hexdigest(),
            "vector_dimensions": sorted(vector_dimensions),
            "text_mismatch_count": text_mismatches,
            "metadata_mismatch_count": metadata_mismatches,
            "unknown_chunk_count": unknown_chunks,
        }, unique_ids, articles
    except Exception:
        issues.append(f"{role}_collection_content_unavailable")
        return {"available": False}, set(), set()


def _verify_index_coverage(service, chunks, indexes, issues):
    ids_by_role, articles_by_role = {}, {}
    for role, attribute in (("base", "dense"), ("civil", "civil_dense")):
        content, ids, articles = _collection_content(
            getattr(service, attribute, None), role, chunks, indexes[role].get("count"),
            indexes[role].get("embedding", {}).get("dimensions"), issues,
        )
        indexes[role]["content"] = content
        ids_by_role[role], articles_by_role[role] = ids, articles
    overlap = ids_by_role["base"] & ids_by_role["civil"]
    indexed = ids_by_role["base"] | ids_by_role["civil"]
    if overlap:
        issues.append("base_civil_index_ids_overlap")
    if indexed != set(chunks):
        issues.append("index_ids_do_not_match_loaded_chunks")
    raw_expected = getattr(getattr(service, "civil", None), "include_ids", None)
    if not isinstance(raw_expected, (list, tuple)) or not raw_expected or not all(isinstance(value, str) for value in raw_expected):
        expected = set()
        issues.append("required_civil_article_ids_unavailable")
    else:
        expected = set(raw_expected)
    loaded_articles = {chunk.get("metadata", {}).get("article_id") for chunk in chunks.values()}
    missing_loaded = expected - loaded_articles
    missing_indexed = expected - articles_by_role["civil"]
    if missing_loaded:
        issues.append("required_civil_articles_missing_from_loaded_chunks")
    if missing_indexed:
        issues.append("required_civil_articles_missing_from_civil_index")
    return {
        "indexed_unique_chunk_count": len(indexed),
        "cross_index_duplicate_count": len(overlap),
        "loaded_chunk_ids_missing_from_indexes": len(set(chunks) - indexed),
        "index_chunk_ids_absent_from_memory": len(indexed - set(chunks)),
        "required_civil_article_count": len(expected),
        "missing_loaded_civil_articles": [redact_secrets(value).text for value in sorted(missing_loaded)],
        "missing_indexed_civil_articles": [redact_secrets(value).text for value in sorted(missing_indexed)],
    }


def capture_retrieval_provenance(service, *, root=ROOT, source_paths=None):
    """Return evidence plus ``ready``; caller must reject ``ready=False``.

    This evaluation requires both base and civil Chroma channels. Source files
    are matched by exact parsed chunk content, since RetrievalService does not
    retain input filenames. No original chunk text is included in the report.
    Compare the complete return value before and after a measurement to detect
    changed source/index contents without relying on file modification times.
    """
    root = Path(root)
    issues = []
    chunks = getattr(service, "_chunks", None)
    if not isinstance(chunks, Mapping) or not chunks:
        issues.append("loaded_chunks_missing")
        chunks = {}
    digest = sha256()
    try:
        for chunk_id in sorted(chunks):
            digest.update(json.dumps(chunks[chunk_id], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
            digest.update(b"\n")
    except (TypeError, ValueError):
        issues.append("loaded_chunks_fingerprint_failed")
    if source_paths is None:
        source_paths = ("data/chunks/chunks.jsonl", "data/chunks/cases.jsonl", "data/chunks/guides.jsonl", "data/sample/chunks_expanded.jsonl")
    sources = {}
    matched_ids = set()
    for index, raw_path in enumerate(source_paths):
        path = Path(raw_path)
        if not path.is_absolute():
            path = root / path
        label = _label(path, root, f"external_source_{index}")
        source = {"exists": path.exists()}
        sources[label] = source
        if not source["exists"]:
            continue
        try:
            source.update(_artifact(path))
            count = 0
            matching = set()
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    count += 1
                    if isinstance(chunk, dict) and chunk.get("chunk_id") in chunks and chunks[chunk["chunk_id"]] == chunk:
                        matching.add(chunk["chunk_id"])
            if source["sha256"] != streaming_sha256(path):
                raise RuntimeError("Source changed during content matching")
            source["chunk_count"] = count
            source["matching_loaded_chunk_count"] = len(matching)
            matched_ids.update(matching)
        except Exception:
            issues.append(f"source_{index}_fingerprint_or_parse_failed")
    if set(chunks) != matched_ids:
        issues.append("loaded_chunks_without_matching_source")
    indexes = {
        role: _index_manifest(getattr(service, attribute, None), role, root, issues)
        for role, attribute in (("base", "dense"), ("civil", "civil_dense"))
    }
    coverage = _verify_index_coverage(service, chunks, indexes, issues)
    return {
        "ready": not issues,
        "issues": issues,
        "mode": "hybrid" if getattr(service, "dense", None) is not None and getattr(service, "civil_dense", None) is not None else "incomplete_hybrid",
        "chunks": {"count": len(chunks), "sha256": digest.hexdigest(), "source_matched_count": len(matched_ids)},
        "source_attribution": "Exact content matches; the loader does not retain original source filenames.",
        "sources": sources,
        "indexes": indexes,
        "coverage": coverage,
    }


def retrieval_semantically_equal(before, after):
    """Compare ready logical snapshots, ignoring physical index serialization.

    Chroma may rewrite its SQLite/HNSW files while opening an existing index.
    Logical identity includes every stored vector, document and metadata entry,
    the active source/chunk coverage, and the declared embedding model. Callers
    should retain both physical manifests and warn on file-only changes.
    Missing vector fingerprints or failed preparation never count as equal.
    """
    if not all(isinstance(snapshot, Mapping) and snapshot.get("ready") is True for snapshot in (before, after)):
        return False
    for key in ("mode", "chunks", "sources", "coverage"):
        if key not in before or key not in after or before[key] != after[key]:
            return False
    for role in ("base", "civil"):
        left = before.get("indexes", {}).get(role)
        right = after.get("indexes", {}).get(role)
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            return False
        for key in ("available", "collection", "collection_metadata_sha256", "embedding", "count", "content"):
            if key not in left or key not in right or left[key] != right[key]:
                return False
        content = left["content"]
        if not isinstance(content, Mapping) or content.get("available") is not True or not content.get("vectors_sha256"):
            return False
    return True


def _read_json(url, timeout):
    # RunPod HTTP proxy rejects urllib's default Python user agent with 403.
    # This is a read-only Ollama metadata request; use an explicit application
    # identity just like the production Ollama client does.
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "LENS-Evaluation/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(4 * 1024 * 1024 + 1)
    if len(body) > 4 * 1024 * 1024:
        raise ValueError("Model metadata too large")
    return json.loads(body)


def capture_ollama_identity(model, *, base_url=None, timeout=5):
    """Read model digest/version without reporting endpoints or error contents.

    Pass the route actually selected for generation when available. Otherwise
    reuse the existing client's cached route selector. This does not run a model
    or alter its tag. Call before and after evaluation to detect retagging.
    """
    result = {"available": False, "model": _identity(model)}
    try:
        if base_url is None:
            from src.generation.llm import _select_ollama_base
            base_url = _select_ollama_base(model, timeout=timeout)
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        payload = _read_json(f"{base}/api/tags", timeout)
        aliases = {model, f"{model}:latest"} if ":" not in model else {model}
        entries = [item for item in payload.get("models", []) if isinstance(item, dict) and item.get("name") in aliases]
        if len(entries) != 1:
            raise ValueError("Model identity not unique")
        entry = entries[0]
        digest = entry.get("digest", "")
        if not isinstance(digest, str) or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", digest):
            raise ValueError("Model digest missing")
        details = entry.get("details") or {}
        result.update({
            "available": True, "model": _identity(entry["name"]), "digest": digest,
            "size_bytes": entry.get("size") if type(entry.get("size")) is int else None,
            "family": _identity(details.get("family")),
            "quantization": _identity(details.get("quantization_level")),
        })
        try:
            result["ollama_version"] = _identity(_read_json(f"{base}/api/version", timeout).get("version"))
        except Exception:
            result["ollama_version"] = None
    except Exception:
        result["reason"] = "model_identity_unavailable"
    return result
