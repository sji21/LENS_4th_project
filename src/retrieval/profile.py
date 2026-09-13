"""Bind an opt-in retrieval policy to its complete local corpus and indexes."""
import hashlib
import json
import re
from pathlib import Path

from src.retrieval.expanded import CIVIL_IDS, POLICY, ExpandedLawRetrievalService
from src.retrieval.retriever import load_chunks

PROFILE = "index/retrieval-profile.json"
FILES = {"chunks/chunks.jsonl", "chunks/civil.jsonl", "chunks/cases.jsonl", "chunks/guides.jsonl",
         "database/knowledge.sqlite3", "database/civil.sqlite3"}
CHUNKS = ("chunks/chunks.jsonl", "chunks/cases.jsonl", "chunks/guides.jsonl")
INDEXES = ("index/chroma_kurev1_1024", "index/chroma_civil_kurev1_1024")


class RetrievalProfileError(ValueError):
    pass


def file_hash(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def index_hash(retriever):
    data = retriever.collection.get(include=["documents", "metadatas", "embeddings"])
    entries = sorted((cid, data["documents"][i], data["metadatas"][i], data["embeddings"][i].tolist())
                     for i, cid in enumerate(data["ids"]))
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read_profile(data_root, chunks, chunk_paths, *, index_paths=None, model=None):
    root = Path(data_root).resolve()
    path = root / PROFILE
    if not path.exists():
        return None
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
        if (set(profile) != {"version", "policy", "model", "civil_ids", "files", "index_hashes"}
                or type(profile["version"]) is not int or profile["version"] != 1 or profile["policy"] != POLICY
                or profile["civil_ids"] != list(CIVIL_IDS)
                or profile["model"] != "nlpai-lab/KURE-v1"
                or (model is not None and model != profile["model"])
                or set(profile["files"]) != FILES or not isinstance(profile["index_hashes"], list)
                or len(profile["index_hashes"]) != 2
                or any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h) for h in profile["index_hashes"])):
            raise RetrievalProfileError("지원하지 않거나 불완전한 검색 프로필입니다.")
        if tuple(Path(p).resolve() for p in chunk_paths) != tuple(root / p for p in CHUNKS):
            raise RetrievalProfileError("프로필과 청크 경로가 다릅니다.")
        if index_paths is not None and tuple(Path(p).resolve() for p in index_paths) != tuple(root / p for p in INDEXES):
            raise RetrievalProfileError("프로필과 인덱스 경로가 다릅니다.")
        if any(file_hash(root / p) != digest for p, digest in profile["files"].items()):
            raise RetrievalProfileError("프로필과 데이터 해시가 다릅니다. 일치하는 백업을 복구하세요.")
        expected = [c for p in CHUNKS for c in load_chunks(root / p)]
        if chunks != expected or len({c["chunk_id"] for c in chunks}) != len(chunks):
            raise RetrievalProfileError("실제 로딩한 청크가 프로필과 다릅니다.")
        civil = [c["metadata"].get("article_id") for c in chunks if c["metadata"].get("title") == "민법"]
        if set(civil) != set(CIVIL_IDS) or len(civil) != len(CIVIL_IDS):
            raise RetrievalProfileError("확대 프로필에 필요한 민법 조문이 일치하지 않습니다.")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RetrievalProfileError("검색 프로필을 읽거나 검증할 수 없습니다.") from error
    return profile


def build_profiled_service(chunks, chunk_paths, dense=None, civil_dense=None, *, index_paths=None, model=None):
    from src.retrieval.service import RetrievalService

    index_roots = [Path(p).resolve().parent.parent for p in (index_paths or ())]
    if not chunk_paths:
        if any((r / PROFILE).exists() for r in index_roots):
            raise RetrievalProfileError("프로필이 지정한 청크 경로가 필요합니다.")
        return RetrievalService(chunks, dense, civil_dense=civil_dense)
    root = Path(chunk_paths[0]).resolve().parent.parent
    if any(r != root and (r / PROFILE).exists() for r in index_roots):
        raise RetrievalProfileError("인덱스 프로필과 청크의 데이터 경로가 다릅니다.")
    profile = read_profile(root, chunks, chunk_paths, index_paths=index_paths, model=model)
    if profile is None:
        return RetrievalService(chunks, dense, civil_dense=civil_dense)
    if index_paths is not None:
        if dense is None or civil_dense is None or [index_hash(r) for r in (dense, civil_dense)] != profile["index_hashes"]:
            raise RetrievalProfileError("프로필과 실제 인덱스가 다릅니다.")
    return ExpandedLawRetrievalService(chunks, dense, civil_dense)
