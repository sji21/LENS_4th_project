"""Explicit, fail-closed loading of a sealed case-focused retrieval bundle."""
import hashlib
import json
import math
import os
import re
import struct
from dataclasses import asdict, replace
from pathlib import Path
from threading import local

from src.retrieval.service import RetrievalService, CASE, _to_evidence
from src.retrieval.case_rerank import rerank_cases

PROFILE_ENV = "LENS_CASE_RETRIEVAL_PROFILE"
DEFAULT_PROFILE = Path(__file__).resolve().parents[2] / "data/case_corpus/runtime-profile.json"
FILES = ("database/knowledge.sqlite3", "chunks/laws.jsonl", "chunks/cases.jsonl", "chunks/guides.jsonl")
CHUNKS = FILES[1:]
TAX_SOURCES = ("국세법령정보시스템", "지방세법령정보시스템")
TAX_SIGNALS = ("세금", "체납", "납세", "과세", "조세", "국세", "지방세", "세무", "법인세", "소득세",
               "취득세", "재산세", "부가가치세", "양도소득", "양도세", "상속세", "증여세", "등록세",
               "종합부동산", "손금", "익금", "가산세", "세액", "세율", "세목", "경정", "공제",
               "비과세", "감면", "원천징수", "종부세")
CASE_CITATION = re.compile(r"\d{2,4}\s*[가-힣]{1,4}\s*\d{2,}")


def case_query_filter(where, question, policy):
    """Keep explicit tax and case-citation queries open to the entire case corpus."""
    compact = re.sub(r"\s+", "", question)
    if (policy != "tax_source_guard" or CASE_CITATION.search(question)
            or any(term in compact for term in TAX_SIGNALS)):
        return where
    return {"$and": [where, {"source_name": {"$nin": list(TAX_SOURCES)}}]}


def configured_case_profile():
    configured = os.getenv(PROFILE_ENV, "").strip()
    if configured:
        return configured
    return str(DEFAULT_PROFILE) if DEFAULT_PROFILE.is_file() else ""


def file_hash(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def index_content_hash(collection):
    """Hash logical records and vectors; Chroma's mutable SQLite bytes are separate."""
    ids = sorted(collection.get(include=[])["ids"])
    digest = hashlib.sha256()
    for start in range(0, len(ids), 128):
        got = collection.get(ids=ids[start:start+128], include=["documents", "metadatas", "embeddings"])
        rows = sorted(zip(got["ids"], got["documents"], got["metadatas"], got["embeddings"]))
        for cid, doc, meta, vector in rows:
            if len(vector) != 1024 or not all(math.isfinite(float(x)) for x in vector):
                raise ValueError("판례 인덱스 벡터 차원 또는 값이 잘못됐습니다.")
            record = json.dumps([cid, doc, meta], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            digest.update(struct.pack("<Q",len(record)))
            digest.update(record)
            digest.update(struct.pack("<1024f",*vector))
    return digest.hexdigest()


def read_case_profile(path):
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    if (profile.get("schema") != "lens-case-retrieval-v1"
            or set(profile.get("files", {})) != set(FILES)
            or profile.get("model_id") != "nlpai-lab/KURE-v1"
            or not re.fullmatch(r"[0-9a-f]{40}", profile.get("model_revision", ""))
            or profile.get("dimension") != 1024
            or profile.get("rerank_policy") not in {"band_3pct", "band_1pct", "exact_tie", "pure_rrf"}
            or type(profile.get("case_rrf_k", 60)) is not int
            or not 1 <= profile.get("case_rrf_k", 60) <= 100
            or profile.get("case_query_expansion", "standard") not in {"standard", "civil_terms"}
            or profile.get("case_field_policy", "all") not in {"all", "tax_source_guard"}
            or type(profile.get("case_bm25_weight", 1)) is not int
            or type(profile.get("case_dense_weight", 1)) is not int
            or profile.get("case_bm25_weight", 1) not in (1, 2)
            or profile.get("case_dense_weight", 1) not in (1, 2)
            or type(profile.get("candidate_depth")) is not int
            or not 20 <= profile["candidate_depth"] <= 500
            or type(profile.get("return_k")) is not int
            or not 1 <= profile["return_k"] <= profile["candidate_depth"]
            or profile.get("fallback_allowed") is not False
            or not re.fullmatch(r"[0-9a-f]{64}", profile.get("index_content_sha256", ""))):
        raise ValueError("판례 검색 프로필의 필수 항목이 잘못됐습니다.")
    root = Path(profile["data_root"])
    if not root.is_absolute():
        raise ValueError("판례 프로필 data_root는 절대 경로여야 합니다.")
    for name, expected in profile["files"].items():
        if file_hash(root/name) != expected:
            raise ValueError("판례 프로필의 파일 해시가 다릅니다: " + name)
    if "index_files" in profile:
        index_root = (root/"index/chroma_kurev1_1024").resolve()
        if not profile["index_files"]:
            raise ValueError("판례 인덱스 물리 해시 목록이 비어 있습니다.")
        actual={p.relative_to(index_root).as_posix() for p in index_root.rglob("*") if p.is_file()}
        if actual!=set(profile["index_files"]):
            raise ValueError("판례 인덱스 물리 파일 목록이 다릅니다.")
        for name, expected in profile["index_files"].items():
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("판례 인덱스 파일은 상대 경로여야 합니다.")
            target=(index_root/name).resolve()
            if not target.is_relative_to(index_root) or file_hash(target)!=expected:
                raise ValueError("판례 인덱스 물리 파일 해시가 다릅니다: " + name)
    return profile


class SupplementalCaseDense:
    """One cosine ranking across the frozen index and verified extra texts.

    Re-encode only the supplement with the very same pinned backend instance;
    a second index's model label is not sufficient proof of vector compatibility.
    """
    def __init__(self, frozen, chunks):
        from src.retrieval.dense import DenseRetriever

        metadata = getattr(getattr(frozen, "collection", None), "metadata", None)
        if metadata is not None and metadata.get("hnsw:space") != "cosine":
            raise ValueError("판례 보충 검색은 코사인 인덱스가 필요합니다.")
        self.frozen = frozen
        self.backend = frozen.backend
        self.supplement = DenseRetriever(chunks, self.backend, use_cache=False)

    def __getattr__(self, name):
        return getattr(self.frozen, name)

    def search(self, query, k, where=None):
        if k <= 0:
            return []
        hits = self.frozen.search(query, k, where)
        hits += self.supplement.search(query, k, where)
        scores = {}
        for cid, score in hits:
            if math.isfinite(score):
                scores[cid] = max(scores.get(cid, -math.inf), score)
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:k]


def _supplemental_cases(chunks, base_service):
    if base_service is None:
        return []
    known_ids = {chunk["chunk_id"] for chunk in chunks}
    known_cases = {chunk["metadata"].get("canonical_case_key") for chunk in chunks}
    supplement = []
    for chunk in base_service._chunks.values():
        meta = chunk["metadata"]
        if (meta.get("doc_type") != "case" or meta.get("status") != "current"
                or meta.get("corpus_role") != "case_supplement"):
            continue
        key = meta.get("canonical_case_key")
        if not key or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("보충 판례의 canonical identity가 없습니다.")
        if chunk["chunk_id"] in known_ids or key in known_cases:
            continue
        supplement.append(chunk)
    return supplement


class CaseCorpusRetrievalService(RetrievalService):
    def __init__(self, chunks, dense, profile, base_service=None):
        from src.retrieval.terms import expand, expand_civil

        case = replace(CASE,
            query_expander=expand_civil if profile.get("case_query_expansion") == "civil_terms" else expand,
            bm25_weight=profile.get("case_bm25_weight", 1),
            dense_weight=profile.get("case_dense_weight", 1))
        self.base_service = base_service
        self._frozen_chunks = list(chunks)
        self._frozen_dense = dense
        super().__init__(chunks, dense, case=case)
        self.case_profile = profile
        self._request_trace = local()
        self.attach_base_service(base_service)

    def attach_base_service(self, base_service):
        """Bind base channels and rebuild the single case candidate corpus.

        Call once during service construction, before concurrent searches.
        Reattachment replaces prior supplements instead of accumulating them.
        """
        supplement = _supplemental_cases(self._frozen_chunks, base_service)
        chunks = self._frozen_chunks + supplement
        dense = self._frozen_dense
        if supplement and dense is not None:
            dense = SupplementalCaseDense(dense, supplement)
        self.base_service = base_service
        self.case_supplement_manifest = [
            {"chunk_id": chunk["chunk_id"],
             "canonical_case_key": chunk["metadata"]["canonical_case_key"],
             "text_sha256": hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest(),
             "source_url": chunk["metadata"].get("source_url", ""),
             "model_id": self.case_profile.get("model_id") if dense is not None else None,
             "model_revision": self.case_profile.get("model_revision") if dense is not None else None}
            for chunk in supplement
        ]
        self._chunks = {chunk["chunk_id"]: chunk for chunk in chunks}
        self.dense = dense
        case = self.corpora[1]
        retriever = self._build(case, [chunk for chunk in chunks
                                     if chunk["metadata"].get("doc_type") in case.doc_types])
        if retriever is not None:
            retriever.rrf_k = self.case_profile.get("case_rrf_k", 60)
        self._retrievers[case.name] = retriever
        return self

    def _warn_if_civil_missing(self, civil, civil_chunks):
        if self.base_service is None:
            RetrievalService._warn_if_civil_missing(civil, civil_chunks)

    def _search_one(self, corpus, question, k):
        if corpus.name != self.corpora[1].name:
            return super()._search_one(corpus, question, k)
        if k <= 0:
            return []
        if k > self.case_profile["candidate_depth"]:
            raise ValueError("반환 K가 봉인된 판례 후보 깊이를 초과합니다.")
        retriever = self._retrievers.get(corpus.name)
        if retriever is None:
            return []
        where = case_query_filter(corpus.where(), question, self.case_profile.get("case_field_policy", "all"))
        hits, members = retriever.search_with_member_hits(question, self.case_profile["candidate_depth"], where)
        policy = self.case_profile["rerank_policy"]
        ranked = (hits[:k] if policy == "pure_rrf" else rerank_cases(
            hits, self._chunks, k, {"band_3pct":.03,"band_1pct":.01,"exact_tie":0}[policy]))
        self._request_trace.case = {"raw_rrf_candidates":hits,"member_hits":members,"where":where}
        return [_to_evidence(rank,self._chunks[cid],score) for rank,(cid,score) in enumerate(ranked,1)]

    def search(self, question, k_law=5, k_case=None, k_guide=2, *, k_civil=None):
        self._request_trace.case = {}
        if self.base_service is not None:
            result = self.base_service.search(question, k_law=k_law, k_case=0, k_guide=k_guide, k_civil=k_civil)
            if not question or not question.strip():
                return result
            return replace(result, cases=self._search_one(self.corpora[1], question,
                self.case_profile["return_k"] if k_case is None else k_case))
        return super().search(question,k_law,
                              self.case_profile["return_k"] if k_case is None else k_case,
                              k_guide,k_civil=k_civil)

    def search_with_trace(self, question, **kwargs):
        result = self.search(question, **kwargs)
        return result, dict(self._request_trace.case)

    def evidence_payload(self, result):
        channels = {}
        for channel in ("cases", "laws", "civil_laws", "guides"):
            channels[channel] = [dict(asdict(e),
                canonical_case_key=self._chunks.get(e.chunk_id, {}).get("metadata", {}).get("canonical_case_key"))
                for e in getattr(result, channel)]
        return {"schema":"lens-retrieval-evidence-v1","profile_version":self.case_profile["version"],
                "question":result.question,"channels":channels,"generation_status":"not_requested"}


def load_case_profile(path, *, attach_base=True):
    header = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if header.get("schema") == "lens-case-internal-v1":
        from src.retrieval.case_internal_profile import load_internal_case_profile
        # Keep the product law/civil/guide channels when overlaying a new case index.
        base_service = RetrievalService._from_index_without_case_profile() if attach_base else None
        return load_internal_case_profile(path, base_service=base_service)
    from src.retrieval.dense import SentenceTransformerEmbedding, ChromaRetriever
    from src.retrieval.retriever import load_chunks

    profile = read_case_profile(path)
    root = Path(profile["data_root"])
    chunks = [c for name in CHUNKS for c in load_chunks(root/name)]
    if (len(chunks) != profile["chunk_count"] or len({c["chunk_id"] for c in chunks}) != len(chunks)
            or sum(c["metadata"].get("doc_type")=="case" for c in chunks) != profile["case_count"]):
        raise ValueError("판례 프로필의 청크 수가 다릅니다.")
    backend = SentenceTransformerEmbedding(profile["model_id"], revision=profile["model_revision"])
    from src.retrieval.portable_index import native_index_path
    dense = ChromaRetriever(backend, native_index_path(root/"index/chroma_kurev1_1024",
                                                     immutable="index_files" in profile))
    if index_content_hash(dense.collection) != profile["index_content_sha256"]:
        raise ValueError("판례 프로필의 실제 인덱스가 다릅니다.")
    if set(dense.collection.get(include=[])["ids"]) != {c["chunk_id"] for c in chunks}:
        raise ValueError("판례 인덱스와 검색 청크의 ID가 다릅니다.")
    base_service = (RetrievalService._from_index_without_case_profile()
                    if attach_base and profile.get("preserve_base_channels") is True else None)
    return CaseCorpusRetrievalService(chunks,dense,profile,base_service=base_service)
