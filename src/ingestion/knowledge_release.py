"""Portable Git release for the existing knowledge.sqlite3 corpus layout.

Unlike the separate case-internal experiment, this keeps cases/documents/chunks
and the reviewed lens-case-retrieval-v1 policy unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 중복 키: " + key)
            result[key] = value
        return result
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("JSON 최상위 값은 객체여야 합니다.")
    return value


def _sha(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(label + " SHA-256 형식 오류")
    return value

SCHEMA = "lens-knowledge-corpus-release-v1"
CASE_COUNT = 8377
REVISION = "8b418a58414668e75532ed045c22d9ca018ae2b2"
FILES = ("database/knowledge.sqlite3", "chunks/laws.jsonl", "chunks/cases.jsonl", "chunks/guides.jsonl")
INDEX = "index/chroma_kurev1_1024"
MODEL_FILES = {"config.json", "config_sentence_transformers.json", "model.safetensors", "modules.json",
               "sentence_bert_config.json", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
               "1_Pooling/config.json"}
POLICY_KEYS = ("candidate_depth", "return_k", "rerank_policy", "case_query_expansion",
               "case_rrf_k", "case_bm25_weight", "case_dense_weight", "case_field_policy")


def read_release(path, *, expected_case_count=CASE_COUNT):
    path = Path(path).resolve()
    value = _read_json(path)
    if (value.get("schema") != SCHEMA or value.get("case_count") != expected_case_count
            or value.get("fallback_allowed") is not False or not value.get("version")):
        raise ValueError("knowledge 판례 배포 스키마·건수·버전이 다릅니다.")
    if set(value.get("files", {})) != set(FILES):
        raise ValueError("knowledge 배포 파일 목록이 불완전합니다.")
    for name, digest in value["files"].items():
        _sha(digest, name)
        target = (path.parent / name).resolve()
        if not target.is_relative_to(path.parent) or not target.is_file() or file_hash(target) != digest:
            raise ValueError("knowledge 배포 파일 해시 불일치: " + name)
    model = value.get("embedding_model", {})
    if (model.get("model_id") != "nlpai-lab/KURE-v1" or model.get("revision") != REVISION
            or model.get("dimension") != 1024 or set(model.get("files", {})) != MODEL_FILES):
        raise ValueError("knowledge 임베딩 모델 고정 정보가 다릅니다.")
    for name, digest in model["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("임베딩 파일은 안전한 상대 경로여야 합니다.")
        _sha(digest, name)
    index = value.get("index", {})
    if index.get("path") != INDEX or index.get("document_count") != value.get("chunk_count"):
        raise ValueError("knowledge 인덱스 경로·건수가 다릅니다.")
    _sha(index.get("logical_sha256"), "index.logical_sha256")
    if not (path.parent / INDEX).is_dir():
        raise ValueError("knowledge 인덱스가 없습니다.")
    index_files = index.get("files", {})
    actual = {p.relative_to(path.parent/INDEX).as_posix() for p in (path.parent/INDEX).rglob("*") if p.is_file()}
    if not index_files or set(index_files) != actual:
        raise ValueError("Chroma 물리 파일 목록이 다릅니다.")
    for name, digest in index_files.items():
        target = (path.parent/INDEX/name).resolve()
        if not target.is_relative_to((path.parent/INDEX).resolve()) or file_hash(target) != _sha(digest, name):
            raise ValueError("Chroma 물리 파일 해시 불일치: " + name)
    policy = value.get("retrieval_policy", {})
    if (set(policy) != set(POLICY_KEYS) or policy["candidate_depth"] != 80
            or policy["return_k"] != 20 or policy["rerank_policy"] != "band_3pct"
            or policy["case_query_expansion"] != "civil_terms" or policy["case_rrf_k"] != 60
            or policy["case_bm25_weight"] != 2 or policy["case_dense_weight"] != 1
            or policy["case_field_policy"] != "tax_source_guard"):
        raise ValueError("기존 data_dev_v2 검색 정책과 다릅니다.")
    return value


def load_rows(root):
    rows = {}
    for name in FILES[1:]:
        with (root / name).open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if (not isinstance(row.get("chunk_id"), str) or not row["chunk_id"]
                        or not row.get("text") or not isinstance(row.get("metadata"), dict)
                        or row["chunk_id"] in rows):
                    raise ValueError(f"청크 형식 또는 ID 중복: {name}:{number}")
                expected_types = {"chunks/laws.jsonl": {"law", "decree", "rule"},
                                  "chunks/cases.jsonl": {"case"}, "chunks/guides.jsonl": {"guide"}}
                if row["metadata"].get("doc_type") not in expected_types[name]:
                    raise ValueError("청크 파일의 doc_type이 다릅니다: " + name)
                rows[row["chunk_id"]] = row
    return rows


def audit_database(root, rows, expected_case_count=CASE_COUNT):
    db = sqlite3.connect((root / FILES[0]).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    try:
        if tuple(db.execute("PRAGMA integrity_check").fetchone()) != ("ok",):
            raise ValueError("SQLite integrity_check 실패")
        cases = list(db.execute("SELECT c.*,d.source_url,d.checksum AS source_checksum,d.status "
                                "FROM cases c LEFT JOIN documents d ON c.document_id=d.document_id"))
        if len(cases) != expected_case_count or len({c["canonical_case_key"] for c in cases}) != expected_case_count:
            raise ValueError("판례 DB 원천·고유 키 건수가 다릅니다.")
        case_map = {}
        for c in cases:
            for key in ("case_id", "document_id", "canonical_case_key", "court_name", "case_number",
                        "decision_date", "case_name", "full_text", "source_url", "source_name"):
                if not isinstance(c[key], str) or not c[key].strip():
                    raise ValueError(f"판례 DB 원문·출처 필수값 누락: {c['case_id']}:{key}")
            if hashlib.sha256(c["full_text"].encode()).hexdigest() != c["source_checksum"]:
                raise ValueError("판례 DB 원문 SHA-256 불일치: " + c["case_id"])
            case_map[c["case_id"]] = c
        stored = {r["chunk_id"]: r for r in db.execute("SELECT * FROM chunks")}
        if set(stored) != set(rows):
            raise ValueError("DB/JSONL 청크 ID 불일치")
        seen_cases = set()
        for cid, row in rows.items():
            s, m = stored[cid], row["metadata"]
            if (s["content"] != row["text"] or s["document_id"] != row["doc_id"]
                    or s["chunk_index"] != row["chunk_index"] or s["source_type"] != m["doc_type"]
                    or s["checksum"] != hashlib.sha256(row["text"].encode()).hexdigest()
                    or m.get("checksum") != s["checksum"] or m.get("token_count") != s["token_count"]):
                raise ValueError("DB/JSONL 청크 본문·연결 불일치: " + cid)
            if m["doc_type"] == "case":
                case = case_map.get(s["case_id"])
                if case is None or s["document_id"] != case["document_id"]:
                    raise ValueError("판례 청크 원천 연결 실패: " + cid)
                for field in ("case_id", "canonical_case_key", "source_url", "source_name", "case_number",
                              "court_name", "decision_date", "status", "scope_tier", "court_level"):
                    if m.get(field) != case[field]:
                        raise ValueError(f"판례 DB/JSONL 메타데이터 불일치: {cid}:{field}")
                # The 11 supplemental records did not export observed_at. It
                # is an observation timestamp, not case identity/source text.
                if "observed_at" in m and m["observed_at"] != case["observed_at"]:
                    raise ValueError("판례 관찰 시각 불일치: " + cid)
                if "corpus_active" in m and bool(m["corpus_active"]) != bool(case["corpus_active"]):
                    raise ValueError("판례 활성 상태 불일치: " + cid)
                if m.get("version_checksum") != case["current_version_checksum"]:
                    raise ValueError("판례 버전 불일치: " + cid)
                seen_cases.add(case["case_id"])
        if seen_cases != set(case_map):
            raise ValueError("청크 없는 판례가 있습니다.")
        return {"case_count": len(cases), "chunk_count": len(rows), "integrity_check": "ok",
                "full_text_source_hashes_verified": True, "db_jsonl_identity_verified": True,
                "case_chunks_without_observed_at": sum(r["metadata"].get("doc_type") == "case"
                    and "observed_at" not in r["metadata"] for r in rows.values()),
                "case_chunks_without_corpus_active": sum(r["metadata"].get("doc_type") == "case"
                    and "corpus_active" not in r["metadata"] for r in rows.values())}
    finally:
        db.close()


def verify_release(path, *, verify_index=True, expected_case_count=CASE_COUNT):
    path = Path(path).resolve()
    release = read_release(path, expected_case_count=expected_case_count)
    rows = load_rows(path.parent)
    report = audit_database(path.parent, rows, expected_case_count)
    if len(rows) != release["chunk_count"]:
        raise ValueError("release.json 청크 수 불일치")
    if verify_index:
        import chromadb
        from src.retrieval.case_profile import index_content_hash
        from src.retrieval.portable_index import native_index_path
        collection = chromadb.PersistentClient(path=str(native_index_path(path.parent / INDEX, immutable=True))).get_collection("knowledge_chunks")
        if collection.count() != len(rows) or index_content_hash(collection) != release["index"]["logical_sha256"]:
            raise ValueError("실제 Chroma 논리 해시·건수 불일치")
        if collection.configuration.get("hnsw", {}).get("space") != "cosine":
            raise ValueError("Chroma 거리 설정은 cosine이어야 합니다.")
        got = collection.get(include=["documents", "metadatas"])
        if set(got["ids"]) != set(rows):
            raise ValueError("Chroma/JSONL ID 불일치")
        for cid, text, meta in zip(got["ids"], got["documents"], got["metadatas"]):
            row = rows[cid]
            if text != row["text"] or any(meta.get(k) != v for k, v in row["metadata"].items()):
                raise ValueError("Chroma/JSONL 본문·메타데이터 불일치: " + cid)
    return {**report, "schema": "lens-knowledge-corpus-verification-v1", "version": release["version"],
            "index_verified": verify_index, "pass": True}


def write_runtime_profile(path, output, *, expected_case_count=CASE_COUNT):
    # A runtime profile is activation authority; never generate it from an
    # unchecked manifest or a --skip-index verification.
    verify_release(path, expected_case_count=expected_case_count)
    path = Path(path).resolve()
    release = read_release(path, expected_case_count=expected_case_count)
    model = release["embedding_model"]
    profile = {"schema": "lens-case-retrieval-v1", "version": release["version"],
               "data_root": str(path.parent), "files": release["files"],
               "case_count": release["case_count"], "chunk_count": release["chunk_count"],
               "model_id": model["model_id"], "model_revision": model["revision"], "dimension": 1024,
               "index_content_sha256": release["index"]["logical_sha256"],
               "index_files": release["index"]["files"],
               "fallback_allowed": False, "preserve_base_channels": True, **release["retrieval_policy"]}
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return output


def prepare_model(path, *, check=False):
    from huggingface_hub import snapshot_download
    from huggingface_hub.constants import HF_HUB_CACHE
    release = read_release(path)
    model = release["embedding_model"]
    snapshot = Path(HF_HUB_CACHE) / "models--nlpai-lab--KURE-v1" / "snapshots" / model["revision"]
    if not check and any(not (snapshot / f).is_file() for f in model["files"]):
        snapshot_download(model["model_id"], revision=model["revision"], allow_patterns=list(model["files"]))
    for name, digest in model["files"].items():
        target = (snapshot / name).resolve()
        if not target.is_file() or file_hash(target) != digest:
            raise ValueError("배포 KURE 모델 파일 누락·해시 불일치: " + name)
    # Never update refs/main: the existing base corpus owns its own revision.
    return snapshot


def reexport_case_chunks(path, output):
    """Reproduce DB chunk content with the sealed JSONL metadata and order.

    This is replay, not new segmentation of full_text. Supplemental page hashes
    are only in the existing JSONL, so DB-only export cannot reproduce them.
    """
    verify_release(path)
    root = Path(path).resolve().parent
    output = Path(output)
    if output.exists():
        raise FileExistsError("기존 청크 출력 파일을 덮어쓰지 않습니다.")
    db = sqlite3.connect((root/FILES[0]).as_uri()+"?mode=ro&immutable=1",uri=True)
    stored = {r[0]:(r[1],r[2],r[3]) for r in db.execute(
        "SELECT chunk_id,document_id,chunk_index,content FROM chunks WHERE source_type='case'")}
    db.close()
    output.parent.mkdir(parents=True,exist_ok=True)
    # Preserve the sealed representation (including CRLF and JSON spacing),
    # after checking every exported content field against the DB.
    original = (root/"chunks/cases.jsonl").read_bytes()
    for line in original.decode("utf-8").splitlines():
        if not line.strip():continue
        row=json.loads(line)
        if (row["doc_id"],row["chunk_index"],row["text"]) != stored[row["chunk_id"]]:
            raise ValueError("DB와 재출력 청크 내용이 다릅니다.")
    with output.open("xb") as out:
        out.write(original)
    if file_hash(output)!=file_hash(root/"chunks/cases.jsonl"):
        output.unlink()
        raise ValueError("청크 재출력 바이트 해시가 원본과 다릅니다.")
    return output


def export_sample(path, output, case_id):
    """Export one complete existing DB record and its actual exported chunks."""
    verify_release(path)
    root=Path(path).resolve().parent
    db=sqlite3.connect((root/FILES[0]).as_uri()+"?mode=ro&immutable=1",uri=True)
    db.row_factory=sqlite3.Row
    try:
        case=db.execute("SELECT * FROM cases WHERE case_id=?",(case_id,)).fetchone()
        if case is None:raise ValueError("샘플 판례 ID가 DB에 없습니다.")
        document=db.execute("SELECT * FROM documents WHERE document_id=?",(case["document_id"],)).fetchone()
        payload={"case":dict(case),"document":dict(document),"chunks":[row for row in load_rows(root).values()
                      if row["metadata"].get("doc_type")=="case" and row["metadata"].get("case_id")==case_id]}
    finally:
        db.close()
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("x",encoding="utf-8") as stream:
        stream.write(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    return output


def create_release(root, profile_path, model_audit_path, *, version, expected_case_count=CASE_COUNT):
    root = Path(root).resolve()
    output = root / "release.json"
    if output.exists():
        raise FileExistsError("기존 release.json을 덮어쓰지 않습니다.")
    profile = _read_json(Path(profile_path))
    audit = _read_json(Path(model_audit_path))
    if profile.get("schema") != "lens-case-retrieval-v1" or profile.get("case_count") != expected_case_count:
        raise ValueError("입력 프로필이 대상 코퍼스가 아닙니다.")
    model_files = {}
    for absolute, digest in audit["model_files"].items():
        marker = "/snapshots/" + REVISION + "/"
        parts = absolute.replace("\\", "/").split(marker)
        if len(parts) != 2:
            raise ValueError("모델 감사 기록 revision 불일치")
        model_files[parts[1]] = digest
    release = {"schema": SCHEMA, "version": version, "case_count": profile["case_count"],
               "chunk_count": profile["chunk_count"], "fallback_allowed": False,
               "files": profile["files"],
               "index": {"path": INDEX, "document_count": profile["chunk_count"],
                         "logical_sha256": profile["index_content_sha256"],
                         "files": {p.relative_to(root/INDEX).as_posix():file_hash(p)
                                   for p in sorted((root/INDEX).rglob("*")) if p.is_file()}},
               "embedding_model": {"model_id": profile["model_id"], "revision": profile["model_revision"],
                                   "dimension": profile["dimension"], "files": model_files},
               "retrieval_policy": {key: profile[key] for key in POLICY_KEYS},
               "provenance": {"dataset": "data_dev_v2", "source_profile_sha256": file_hash(Path(profile_path)),
                              "model_audit_sha256": file_hash(Path(model_audit_path))}}
    output.write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        report = verify_release(output, expected_case_count=expected_case_count)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output, report


def main(argv=None):
    parser = argparse.ArgumentParser(description="기존 knowledge.sqlite3 코퍼스 배포·검증")
    parser.add_argument("--release", type=Path)
    parser.add_argument("--build-root", type=Path)
    parser.add_argument("--source-profile", type=Path)
    parser.add_argument("--model-audit", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--prepare-model", action="store_true")
    parser.add_argument("--check-model", action="store_true")
    parser.add_argument("--profile-output", type=Path)
    parser.add_argument("--reexport-cases", type=Path, help="DB 본문과 봉인 JSONL 메타데이터로 청크 재출력")
    parser.add_argument("--sample-output", type=Path)
    parser.add_argument("--sample-case-id", default="211709")
    parser.add_argument("--report-output", type=Path)
    args = parser.parse_args(argv)
    if args.build_root:
        if args.release or not all((args.source_profile, args.model_audit, args.version)):
            parser.error("--build-root에는 --source-profile, --model-audit, --version이 필요합니다.")
        path, report = create_release(args.build_root, args.source_profile, args.model_audit, version=args.version)
    else:
        if not args.release:
            parser.error("--release 또는 --build-root가 필요합니다.")
        path = args.release
        report = verify_release(path)
    if args.prepare_model or args.check_model:
        report["model_snapshot"] = str(prepare_model(path, check=args.check_model))
    if args.profile_output:
        report["profile"] = str(write_runtime_profile(path, args.profile_output))
    if args.reexport_cases:
        report["reexported_cases"] = str(reexport_case_chunks(path,args.reexport_cases).resolve())
    if args.sample_output:
        report["sample_record"] = str(export_sample(path,args.sample_output,args.sample_case_id).resolve())
    if args.report_output:
        args.report_output.parent.mkdir(parents=True,exist_ok=True)
        with args.report_output.open("x",encoding="utf-8") as stream:
            stream.write(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
