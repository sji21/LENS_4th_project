"""Load a portable search release built from immutable MySQL exports.

No database credentials or source SQLite files are needed by search consumers.
An explicit release fails closed if its data, index, model, or policy differs.
"""
from __future__ import annotations

import hashlib
import json
import os
from importlib.metadata import version
from pathlib import Path
import re

from src.ingestion.knowledge_release import _read_json, file_hash, MODEL_FILES

RELEASE_ENV = "LENS_MYSQL_RELEASE"
MODEL_ENV = "LENS_MYSQL_MODEL_DIR"
SCHEMA = "lens-mysql-search-v1"
ROOT = Path(__file__).resolve().parents[2]
CASE_POLICY = {"candidate_depth": 80, "return_k": 20, "rerank_policy": "band_3pct",
               "case_query_expansion": "civil_terms", "case_rrf_k": 60,
               "case_bm25_weight": 2, "case_dense_weight": 1,
               "case_field_policy": "tax_source_guard"}
STREAMS = {"base": {"laws", "cases", "guides"}, "civil": {"civil"},
           "cases": {"laws", "cases", "guides"}}


def runtime_versions():
    return {p: version(p) for p in ("chromadb", "numpy", "sentence-transformers",
                                    "torch", "transformers", "tokenizers")}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def safe_file(root, name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or Path(name).is_absolute() or ".." in Path(name).parts):
        raise ValueError("배포 파일 경로가 잘못됐습니다.")
    root = Path(root).resolve()
    path = root / name
    if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
        raise ValueError("배포 파일에 심볼릭 링크를 사용할 수 없습니다.")
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError("배포 파일이 없습니다: " + name)
    return path


def read_export(root, corpus):
    root = Path(root)
    manifest = _read_json(root / "manifest.json")
    snap = manifest.get("snapshot", {})
    if (manifest.get("schema") != "lens-mysql-chunks-v1"
            or snap.get("schema") != "lens-mysql-snapshot-v1"
            or snap.get("corpus") != corpus or not snap.get("version")
            or digest(snap) != manifest.get("snapshot_id")
            or set(snap.get("streams", {})) != STREAMS[corpus]
            or set(manifest.get("files", {})) != {s + ".jsonl" for s in STREAMS[corpus]}):
        raise ValueError("MySQL 내보내기 스냅샷·stream이 다릅니다: " + corpus)
    rows, ids = {}, set()
    for stream in sorted(STREAMS[corpus]):
        name = stream + ".jsonl"
        path = safe_file(root, name)
        if file_hash(path) != manifest["files"][name]:
            raise ValueError("MySQL 청크 파일 해시가 다릅니다: " + name)
        items = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                raise ValueError("MySQL 청크에 빈 행이 있습니다.")
            row = json.loads(line)
            cid, meta = row.get("chunk_id"), row.get("metadata")
            types = {"laws": {"law", "decree", "rule"}, "civil": {"law"},
                     "cases": {"case"}, "guides": {"guide"}}[stream]
            if (not isinstance(cid, str) or not cid or cid in ids
                    or not isinstance(row.get("text"), str) or not row["text"].strip()
                    or not isinstance(meta, dict) or meta.get("doc_type") not in types):
                raise ValueError("MySQL 청크 형식·ID·종류가 잘못됐습니다.")
            ids.add(cid)
            items.append(row)
        if {"count": len(items), "sha256": digest(items)} != snap["streams"][stream]:
            raise ValueError("MySQL 검색 stream 내용이 다릅니다: " + stream)
        rows[stream] = items
    return manifest, rows


def _law_policies():
    from src.retrieval.expanded import CIVIL_IDS, LEGACY_CIVIL_IDS, LEGACY_POLICY, POLICY
    return {LEGACY_POLICY: LEGACY_CIVIL_IDS, POLICY: CIVIL_IDS}


def civil_ids_for_policy(law_policy):
    policies = _law_policies()
    if law_policy not in policies:
        raise ValueError("MySQL 검색 배포의 법령 검색 정책이 잘못됐습니다.")
    return policies[law_policy]


def detect_law_policy(exports):
    articles = [r.get("metadata", {}).get("article_id")
                for r in exports["civil"]["civil"]]
    matches = [law_policy for law_policy, civil_ids in _law_policies().items()
               if len(articles) == len(civil_ids) and set(articles) == set(civil_ids)]
    if len(matches) != 1:
        raise ValueError("민법 조문 목록에 맞는 법령 검색 정책이 없습니다.")
    return matches[0]


def channel_rows(exports, law_policy=None):
    base, civil, cases = exports["base"], exports["civil"], exports["cases"]
    # The base snapshot also preserves civil rows. The dedicated civil export
    # owns that channel; prove they agree before removing the duplicate copy.
    base_rows = [r for r in base["laws"] if r["metadata"].get("title") == "민법"]
    dedicated_rows = civil["civil"]
    base_civil = {r["chunk_id"]: r for r in base_rows}
    dedicated = {r["chunk_id"]: r for r in dedicated_rows}
    base_articles = [r["metadata"].get("article_id") for r in base_rows]
    dedicated_articles = [r["metadata"].get("article_id") for r in dedicated_rows]
    if (len(base_civil) != len(base_rows) or len(dedicated) != len(dedicated_rows)
            or len(set(base_articles)) != len(base_articles)
            or len(set(dedicated_articles)) != len(dedicated_articles)):
        raise ValueError("민법 청크 ID 또는 조문이 중복됐습니다.")
    if base_civil != dedicated:
        raise ValueError("기본/민법 스냅샷의 민법 청크가 다릅니다.")
    law_policy = detect_law_policy(exports) if law_policy is None else law_policy
    expected_articles = civil_ids_for_policy(law_policy)
    if (len(dedicated_articles) != len(expected_articles)
            or set(dedicated_articles) != set(expected_articles)):
        raise ValueError("법령 검색 정책과 민법 조문 범위가 다릅니다.")
    return {
        "base": [r for stream in ("laws", "cases", "guides") for r in base[stream]
                 if r["metadata"].get("title") != "민법"],
        "civil": dedicated_rows,
        "cases": [r for stream in ("laws", "cases", "guides") for r in cases[stream]],
    }


def verify_collection(collection, rows, expected_hash=None, model=None):
    from src.retrieval.case_profile import index_content_hash
    from src.retrieval.index import clean_metadata
    expected = {r["chunk_id"]: r for r in rows}
    if (collection.count() != len(expected)
            or collection.configuration.get("hnsw", {}).get("space") != "cosine"):
        raise ValueError("Chroma 건수 또는 거리 설정이 다릅니다.")
    got = collection.get(include=["documents", "metadatas"])
    if set(got["ids"]) != set(expected):
        raise ValueError("Chroma/MySQL 청크 ID가 다릅니다.")
    for cid, text, meta in zip(got["ids"], got["documents"], got["metadatas"]):
        row = expected[cid]
        metadata = clean_metadata(row["metadata"])
        # The existing case index records embedding provenance in addition to
        # exported source metadata. Validate it, rather than ignoring extras.
        if "embedding_fingerprint" in meta or "embedding_input_hash" in meta:
            if model is None:
                raise ValueError("색인 임베딩 출처를 검증할 모델 정보가 없습니다.")
            metadata.update(embedding_fingerprint=model["model_id"] + "@" + model["revision"],
                            embedding_input_hash=hashlib.sha256(row["text"].encode("utf-8")).hexdigest())
        if text != row["text"] or meta != metadata:
            raise ValueError("Chroma/MySQL 본문·검색 메타데이터가 다릅니다: " + cid)
    logical = index_content_hash(collection)
    if expected_hash is not None and logical != expected_hash:
        raise ValueError("Chroma 논리 해시가 다릅니다.")
    return logical


def code_files():
    return {p.relative_to(ROOT).as_posix(): file_hash(p)
            for p in sorted((ROOT / "src/retrieval").glob("*.py"))}


def resolve_release(path):
    path = Path(path).resolve()
    value = _read_json(path)
    if value.get("schema") == "lens-mysql-active-v1":
        target = Path(value["release"])
        path = (path.parent / target).resolve()
        if file_hash(path) != value.get("sha256"):
            raise ValueError("활성 배포 manifest 해시가 다릅니다.")
    return path


def read_release(path):
    path = resolve_release(path)
    value = _read_json(path)
    if (value.get("schema") != SCHEMA or value.get("index_status") != "ready"
            or value.get("fallback_allowed") is not False
            or value.get("case_policy") != CASE_POLICY
            or value.get("law_policy") not in _law_policies()
            or set(value.get("snapshots", {})) != set(STREAMS)
            or set(value.get("indexes", {})) != set(STREAMS)
            or value.get("runtime_versions") != runtime_versions()
            or value.get("code_files") != code_files()):
        raise ValueError("MySQL 검색 배포의 정책·코드·필수 항목이 다릅니다.")
    if digest({k: v for k, v in value.items() if k != "release_id"}) != value.get("release_id"):
        raise ValueError("검색 배포 버전 해시가 다릅니다.")
    for name, expected in value.get("files", {}).items():
        if file_hash(safe_file(path.parent, name)) != expected:
            raise ValueError("검색 배포 파일 해시가 다릅니다: " + name)
    expected_names = {f"exports/{c}/manifest.json" for c in STREAMS}
    expected_names.update(f"exports/{c}/{s}.jsonl" for c in STREAMS for s in STREAMS[c])
    for channel in STREAMS:
        index = value["indexes"][channel]
        names = {p.relative_to(path.parent).as_posix()
                 for p in (path.parent / "indexes" / channel).rglob("*") if p.is_file()}
        if not names or index.get("path") != "indexes/" + channel:
            raise ValueError("검색 배포 인덱스가 없습니다.")
        expected_names.update(names)
    if set(value.get("files", {})) != expected_names:
        raise ValueError("검색 배포 파일 목록이 불완전합니다.")
    exports = {}
    for corpus in STREAMS:
        manifest, exports[corpus] = read_export(path.parent / "exports" / corpus, corpus)
        if manifest["snapshot_id"] != value["snapshots"][corpus]:
            raise ValueError("검색 배포 스냅샷 버전이 다릅니다.")
    model = value.get("model", {})
    if (model.get("model_id") != "nlpai-lab/KURE-v1" or model.get("dimension") != 1024
            or not re.fullmatch(r"[0-9a-f]{40}", model.get("revision", ""))
            or set(model.get("files", {})) != MODEL_FILES):
        raise ValueError("검색 배포 모델 정보가 잘못됐습니다.")
    return path, value, channel_rows(exports, value.get("law_policy"))


def verify_model(directory, spec):
    for name, expected in spec["files"].items():
        if file_hash(safe_file(directory, name)) != expected:
            raise ValueError("KURE 모델 파일 해시가 다릅니다: " + name)
    return Path(directory).resolve()


def open_indexes(path, release, rows, backend):
    from src.retrieval.dense import ChromaRetriever
    from src.retrieval.portable_index import native_index_path
    indexes = {}
    for channel, spec in release["indexes"].items():
        dense = ChromaRetriever(backend, native_index_path(path.parent / spec["path"], immutable=True))
        verify_collection(dense.collection, rows[channel], spec["logical_sha256"], release["model"])
        if spec["count"] != len(rows[channel]):
            raise ValueError("배포 인덱스 건수가 다릅니다.")
        indexes[channel] = dense
    return indexes


def load_service(path, model_dir=None):
    from src.retrieval.dense import SentenceTransformerEmbedding
    from src.retrieval.expanded import ExpandedLawRetrievalService
    from src.retrieval.case_profile import CaseCorpusRetrievalService
    path, release, rows = read_release(path)
    directory = model_dir or os.getenv(MODEL_ENV, "")
    if not directory:
        raise ValueError("LENS_MYSQL_MODEL_DIR에 검증된 KURE 모델 폴더를 지정하세요.")
    model = verify_model(directory, release["model"])
    backend = SentenceTransformerEmbedding(str(model), device="cpu", batch=2)
    indexes = open_indexes(path, release, rows, backend)
    civil_ids = civil_ids_for_policy(release["law_policy"])
    base = ExpandedLawRetrievalService(rows["base"] + rows["civil"],
                                      indexes["base"], indexes["civil"],
                                      civil_ids=civil_ids, policy=release["law_policy"])

    class MySQLRetrievalService(CaseCorpusRetrievalService):
        def evidence_payload(self, result):
            payload = super().evidence_payload(result)
            payload["release_id"] = release["release_id"]
            for channel, evidences in payload["channels"].items():
                corpus = {"cases": "cases", "civil_laws": "civil"}.get(channel, "base")
                for evidence in evidences:
                    evidence["snapshot_id"] = release["snapshots"][corpus]
            return payload

    service = MySQLRetrievalService(rows["cases"], indexes["cases"],
        dict(release["case_policy"], version=release["release_id"]), base_service=base)
    service.mysql_release = release
    return service
