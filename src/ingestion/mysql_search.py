"""Build, verify, activate, and query one MySQL search release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from src.ingestion.knowledge_release import _read_json, file_hash
from src.retrieval.mysql_release import (
    CASE_POLICY, ROOT, SCHEMA, STREAMS, channel_rows, code_files, digest, encoded,
    detect_law_policy, load_service, open_indexes, read_export, read_release, resolve_release,
    verify_collection, verify_model, runtime_versions, write_vector_reference,
)


def write_json(path, value):
    Path(path).write_bytes(encoded(value) + b"\n")


def audit_tokens(rows, model_dir):
    """Reject silent model truncation; source text remains intact in MySQL."""
    from transformers import AutoTokenizer
    limit = _read_json(Path(model_dir) / "sentence_bert_config.json")["max_seq_length"]
    if type(limit) is not int or limit < 1:
        raise ValueError("임베딩 모델의 최대 입력 길이가 잘못됐습니다.")
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    maximum = 0
    for start in range(0, len(rows), 64):
        window = rows[start:start + 64]
        tokens = tokenizer([r["text"] for r in window], truncation=False, add_special_tokens=True)["input_ids"]
        for row, ids in zip(window, tokens, strict=True):
            maximum = max(maximum, len(ids))
            if len(ids) > limit:
                raise ValueError(f"임베딩 입력 한도 초과: {row['chunk_id']} ({len(ids)} > {limit}). "
                                 "원문을 보존하고 새 청킹 버전을 검토하세요.")
    return {"limit": limit, "maximum": maximum, "checked": len(rows)}


def sync_collection(collection, rows, backend_factory, model):
    """Update a private index copy after its previous contents were verified."""
    from src.retrieval.index import clean_metadata
    got = collection.get(include=["documents", "metadatas", "embeddings"])
    previous = {cid: (got["documents"][i], got["metadatas"][i], got["embeddings"][i])
                for i, cid in enumerate(got["ids"])}
    incoming = {row["chunk_id"] for row in rows}
    if len(incoming) != len(rows):
        raise ValueError("검색 청크 ID가 중복됐습니다.")
    changed = [r for r in rows if r["chunk_id"] not in previous or previous[r["chunk_id"]][0] != r["text"]]
    vectors = {}
    if changed:
        backend = backend_factory()
        for start in range(0, len(changed), 8):
            window = changed[start:start + 8]
            vectors.update(zip((r["chunk_id"] for r in window), backend.embed([r["text"] for r in window]), strict=True))
            print(f"새 본문 임베딩: {min(start + 8, len(changed))}/{len(changed)}", flush=True)
    pending = []
    for row in rows:
        cid = row["chunk_id"]
        metadata = clean_metadata(row["metadata"])
        metadata.update(embedding_input_hash=hashlib.sha256(row["text"].encode("utf-8")).hexdigest(),
                        embedding_fingerprint=model["model_id"] + "@" + model["revision"])
        if cid not in vectors and previous[cid][1] == metadata:
            continue
        vector = vectors[cid] if cid in vectors else previous[cid][2]
        pending.append((cid, row["text"], metadata, vector))
    for start in range(0, len(pending), 128):
        window = pending[start:start + 128]
        collection.upsert(ids=[r[0] for r in window], documents=[r[1] for r in window],
                          metadatas=[r[2] for r in window],
                          embeddings=[[float(v) for v in r[3]] for r in window])
    stale = sorted(set(previous) - incoming)
    for start in range(0, len(stale), 128):
        collection.delete(ids=stale[start:start + 128])
    return {"embedded": len(changed), "reused": len(rows) - len(changed),
            "removed": len(stale), "upserted": len(pending)}


def build_worker(staging, model_dir, case_release=None, previous_release=None):
    """Child process owns all Chroma handles until it exits before sealing."""
    import torch
    from src.ingestion.knowledge_release import read_release as read_case_release
    from src.retrieval.dense import SentenceTransformerEmbedding, ChromaRetriever
    from src.retrieval.index import build_index
    torch.set_num_threads(4)
    staging = Path(staging)
    previous = None
    if previous_release:
        previous_path, previous, previous_rows = read_release(previous_release)
        model = previous["model"]
        provenance = {"previous_release_id": previous["release_id"],
                      "previous_manifest_sha256": file_hash(previous_path)}
    else:
        case_release = Path(case_release)
        source = read_case_release(case_release)
        model = source["embedding_model"]
        # Reuse one model only if both historical revisions have identical
        # inference files. A subsequent release carries this checked identity.
        audit = _read_json(ROOT / "data/eval/patch027-full/capture/audit.json")["model_files"]
        base_files = {"/".join(Path(n).parts[2:]): h for n, h in audit.items()}
        if any(base_files.get(n) != h for n, h in model["files"].items()):
            raise ValueError("법령/판례 임베딩 모델 파일이 달라 별도 재색인이 필요합니다.")
        if source["retrieval_policy"] != CASE_POLICY:
            raise ValueError("판례 검색 정책이 다릅니다.")
        provenance = {"case_release_sha256": file_hash(case_release),
                      "base_model_revisions": sorted({Path(n).parts[1] for n in audit}),
                      "model_inference_files_identical": True}
    verify_model(model_dir, model)
    manifests, exports = {}, {}
    for corpus in STREAMS:
        manifests[corpus], exports[corpus] = read_export(staging / "exports" / corpus, corpus)
    law_policy = detect_law_policy(exports)
    rows = channel_rows(exports, law_policy)
    indexes = {}
    backend = None
    def get_backend():
        nonlocal backend
        if backend is None:
            backend = SentenceTransformerEmbedding(str(Path(model_dir).resolve()), device="cpu", batch=2)
        return backend
    for channel in ("cases", "base", "civil"):
        target = staging / "indexes" / channel
        token_audit = audit_tokens(rows[channel], model_dir)
        stats = {}
        if previous:
            spec = previous["indexes"][channel]
            shutil.copytree(previous_path.parent / spec["path"], target)
            dense = ChromaRetriever(None, target)
            reference = previous_path.parent / spec["vector_reference"] if "vector_reference" in spec else None
            verify_collection(dense.collection, previous_rows[channel], spec["logical_sha256"], model, reference)
            stats = sync_collection(dense.collection, rows[channel], get_backend, model)
        elif channel == "cases":
            shutil.copytree(case_release.parent / source["index"]["path"], target)
        else:
            print("색인 생성: " + channel + " " + str(len(rows[channel])), flush=True)
            build_index(rows[channel], get_backend(), target, prune_all=True)
        dense = ChromaRetriever(backend, target)
        logical = verify_collection(dense.collection, rows[channel],
            source["index"]["logical_sha256"] if channel == "cases" and not previous else None, model)
        reference = "references/" + channel + ".f32"
        write_vector_reference(dense.collection, staging / reference)
        indexes[channel] = {"path": "indexes/" + channel, "count": len(rows[channel]),
                            "logical_sha256": logical, "vector_reference": reference,
                            "update": stats, "token_audit": token_audit}
        print("색인 검증 완료: " + channel, flush=True)
    release = {"schema": SCHEMA, "index_status": "ready", "fallback_allowed": False,
        "snapshots": {c: m["snapshot_id"] for c, m in manifests.items()},
        "indexes": indexes, "model": model, "case_policy": CASE_POLICY,
        "law_policy": law_policy, "code_files": code_files(),
        "runtime_versions": runtime_versions(),
        "provenance": provenance}
    write_json(staging / "worker-result.json", release)


def build_release(export_root, output, model_dir, case_release=None, previous_release=None):
    if bool(case_release) == bool(previous_release):
        raise ValueError("case_release 또는 previous_release 중 하나를 지정하세요.")
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("기존 검색 배포본을 덮어쓰지 않습니다.")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Chroma's native paths must be ASCII, and handles must be closed before
    # computing physical hashes or moving files on Windows.
    with tempfile.TemporaryDirectory(prefix="lens-mysql-build-") as temporary:
        staging = Path(temporary)
        if not str(staging).isascii():
            raise ValueError("TEMP에 영문 경로를 지정한 뒤 다시 실행하세요.")
        for corpus in STREAMS:
            read_export(Path(export_root) / corpus, corpus)
            shutil.copytree(Path(export_root) / corpus, staging / "exports" / corpus)
        reference = (["--previous-release", str(Path(previous_release).resolve())] if previous_release else
                     ["--case-release", str(Path(case_release).resolve())])
        subprocess.run([sys.executable, "-X", "utf8", "-m", "src.ingestion.mysql_search", "_worker",
                        "--staging", str(staging), "--model-dir", str(Path(model_dir).resolve()),
                        *reference],
                       cwd=ROOT, check=True)
        release = _read_json(staging / "worker-result.json")
        release["files"] = {p.relative_to(staging).as_posix(): file_hash(p)
                            for folder in ("exports", "indexes", "references")
                            for p in sorted((staging / folder).rglob("*")) if p.is_file()}
        release["release_id"] = digest(release)
        write_json(staging / "release.json", release)
        read_release(staging / "release.json")
        with tempfile.TemporaryDirectory(prefix=".mysql-install-", dir=output.parent) as install:
            destination = Path(install) / "release"
            shutil.copytree(staging, destination, ignore=shutil.ignore_patterns("worker-result.json"))
            read_release(destination / "release.json")
            destination.rename(output)
    return output / "release.json"


def verify_release(path):
    path, release, rows = read_release(path)
    # No embedding calls are required to validate stored vectors.
    open_indexes(path, release, rows, backend=None)
    return {"release_id": release["release_id"], "snapshots": release["snapshots"],
            "indexes": {c: len(r) for c, r in rows.items()}, "verified": True}


def activate(path, pointer):
    path, _, _ = read_release(path)
    verify_release(path)
    pointer = Path(pointer).resolve()
    if pointer == path or pointer.is_relative_to(path.parent):
        raise ValueError("활성 포인터는 불변 배포 폴더 밖에 두세요.")
    pointer.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mysql-active-", suffix=".json", dir=pointer.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(encoded({"schema": "lens-mysql-active-v1", "release": str(path),
                               "sha256": file_hash(path)}) + b"\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, pointer)
    finally:
        Path(name).unlink(missing_ok=True)
    return pointer


def prepare_model(path, directory):
    from huggingface_hub import hf_hub_download
    _, release, _ = read_release(path)
    model = release["model"]
    directory = Path(directory).resolve()
    for name, expected in model["files"].items():
        target = directory / name
        if target.exists():
            if file_hash(target) != expected:
                raise ValueError("기존 모델 파일 해시가 다릅니다: " + name)
        else:
            hf_hub_download(model["model_id"], name, revision=model["revision"], local_dir=directory)
    return verify_model(directory, model)


# PEP 440 release, pre/post/dev and local segments. This runs in a fresh virtual
# environment, so it must not import third-party packages such as packaging.
VERSION_PIN = re.compile(r"[0-9]+(\.[0-9]+)*((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?"
                         r"(\+[a-z0-9]+(\.[a-z0-9]+)*)?")


def dependency_requirements(path):
    """Bootstrap dependency pins before strict runtime verification is possible."""
    release = _read_json(resolve_release(path))
    versions = release.get("runtime_versions", {})
    if (release.get("schema") != SCHEMA
            or digest({k: v for k, v in release.items() if k != "release_id"}) != release.get("release_id")
            or set(versions) != {"chromadb", "numpy", "sentence-transformers", "torch", "transformers", "tokenizers"}
            or not all(isinstance(v, str) and VERSION_PIN.fullmatch(v) for v in versions.values())):
        raise ValueError("검색 배포 의존성 목록·해시가 잘못됐습니다.")
    return "".join(f"{name}=={value}\n" for name, value in sorted(versions.items()))


def main(argv=None):
    parser = argparse.ArgumentParser(description="MySQL 청크 → 검색 배포본 → 검증·활성화·질의")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--export-root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--model-dir", type=Path, required=True)
    origin = build.add_mutually_exclusive_group(required=True)
    origin.add_argument("--case-release", type=Path)
    origin.add_argument("--previous-release", type=Path)
    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--staging", type=Path, required=True)
    worker.add_argument("--model-dir", type=Path, required=True)
    worker_origin = worker.add_mutually_exclusive_group(required=True)
    worker_origin.add_argument("--case-release", type=Path)
    worker_origin.add_argument("--previous-release", type=Path)
    for cmd in ("verify", "activate", "model", "query"):
        p = sub.add_parser(cmd)
        p.add_argument("--release", type=Path, required=True)
        if cmd == "activate":
            p.add_argument("--pointer", type=Path, required=True)
        if cmd in ("model", "query"):
            p.add_argument("--model-dir", type=Path, required=True)
        if cmd == "query":
            p.add_argument("--question", required=True)
            p.add_argument("--output", type=Path)
    requirements = sub.add_parser("requirements")
    requirements.add_argument("--release", type=Path, required=True)
    requirements.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "_worker":
        build_worker(args.staging, args.model_dir, args.case_release, args.previous_release)
        return
    if args.command == "requirements":
        content = dependency_requirements(args.release)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(content)
        result = str(args.output.resolve())
    elif args.command == "build":
        result = str(build_release(args.export_root, args.output, args.model_dir, args.case_release, args.previous_release))
    elif args.command == "verify":
        result = verify_release(args.release)
    elif args.command == "activate":
        result = str(activate(args.release, args.pointer))
    elif args.command == "model":
        result = str(prepare_model(args.release, args.model_dir))
    else:
        import torch
        torch.set_num_threads(4)
        service = load_service(args.release, args.model_dir)
        result = service.evidence_payload(service.search(args.question, k_case=2))
        if args.output:
            write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
