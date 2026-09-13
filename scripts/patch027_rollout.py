"""Stage, verify, back up and install the approved local expanded-law profile."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

from scripts.patch027_paths import ROOT, read, sha, write
from scripts.patch027_context_live import _serialize, _committed_sources
from scripts.patch027_final_test import check as check_final, BUNDLE as FINAL, SEARCH_K
from scripts.patch027_loss_analysis import align
from src.retrieval.expanded import CIVIL_IDS, POLICY
from src.retrieval.profile import FILES, CHUNKS, INDEXES, PROFILE

SCOPES = tuple(sorted(FILES)) + INDEXES + (PROFILE,)


def expected_profile():
    audit = read(ROOT / "data/eval/patch027-full/capture/audit.json")
    return {"version": 1, "policy": POLICY, "model": "nlpai-lab/KURE-v1",
            "civil_ids": list(CIVIL_IDS), "files": audit["candidate_files"],
            "index_hashes": audit["candidate_index_hashes"]}


def payload_hashes(root):
    return {rel: tree_hashes(child(root, rel)) if child(root, rel).is_dir() else
            sha(child(root, rel)) if child(root, rel).exists() else None for rel in SCOPES}


def child(root, rel):
    root = Path(root).resolve()
    result = (root / rel).resolve()
    if result == root or not result.is_relative_to(root):
        raise ValueError("Path escapes intended data directory")
    return result


def fresh_tmp(path):
    path = Path(path).resolve()
    if path.exists() or path == ROOT / "tmp" or not path.is_relative_to(ROOT / "tmp"):
        raise ValueError("Use a fresh directory inside workspace tmp")
    return path


def tree_hashes(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def copy_scope(source, target):
    for rel in SCOPES:
        src, dst = child(source, rel), child(target, rel)
        if not src.exists():
            if rel == PROFILE:
                continue
            raise ValueError(f"Missing payload: {rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def stage(candidate, out):
    check_final()
    out = fresh_tmp(out)
    audit = read(ROOT / "data/eval/patch027-full/capture/audit.json")
    if set(audit["candidate_files"]) != FILES or any(sha(candidate / p) != h for p, h in audit["candidate_files"].items()):
        raise ValueError("Candidate differs from final test corpus")
    out.mkdir(parents=True)
    copy_scope(candidate, out / "data")
    write(out / "data" / PROFILE, expected_profile())
    print("Staged profile:", out / "data")


def code_snapshot():
    files = subprocess.check_output(["git", "ls-files", "src", "scripts", "requirements*.txt"], cwd=ROOT, text=True).splitlines()
    # Git stores LF; Windows edits/checkouts may contain mixed LF/CRLF lines.
    return {p: source_hash((ROOT / p).read_bytes()) for p in files}


def source_hash(content):
    return hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()


def verify(data, out):
    from src.evaluation.baseline import settings
    from src.retrieval.profile import index_hash
    from src.retrieval.service import RetrievalService

    check_final()
    out = fresh_tmp(out)
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise ValueError("Commit product code before verification")
    data = data.resolve()
    snapshot = code_snapshot()
    profile = read(data / PROFILE)
    before = {p: sha(child(data, p)) for p in FILES | {PROFILE}}
    full = read(ROOT / "data/eval/patch027-full/capture/audit.json")
    model_root = Path.home() / ".cache/huggingface/hub/models--nlpai-lab--KURE-v1"
    if any(sha(model_root / p) != h for p, h in full["model_files"].items()):
        raise ValueError("Model files differ from final test")
    service = (RetrievalService.from_index() if data == (ROOT / "data").resolve() else
               RetrievalService.from_index(chunk_paths=tuple(data / p for p in CHUNKS),
                   index_path=data / INDEXES[0], civil_index_path=data / INDEXES[1]))
    if service.profile_name != POLICY:
        raise ValueError("Product factory did not activate expanded policy")
    runtime_settings = settings(service)
    backend = service.dense.backend.delegate
    if backend is not service.civil_dense.backend.delegate:
        raise ValueError("Model backend is not shared")
    original = backend.embed
    original(["임대차 검색 준비"])
    calls = 0
    def measured(texts):
        nonlocal calls
        calls += 1
        return original(texts)
    backend.embed = measured
    queries = read(ROOT / "data/eval/patch015-baseline/capture/results.json")
    expected = align(read(FINAL / "rows.json"), queries)
    rows = []
    for query, reference in zip(queries, expected):
        start, previous = time.perf_counter(), calls
        result = service.search(query["query"], **SEARCH_K)
        row = {k: query[k] for k in ("qid", "mode", "query_sha256")}
        row.update(result=_serialize(result), seconds=time.perf_counter() - start, model_calls=calls - previous)
        if row["result"] != reference["record_lookup"]:
            raise ValueError(f"Product differs from finalist: {query['qid']} {query['mode']}")
        rows.append(row)
        if len(rows) % 25 == 0:
            print(f"{len(rows)}/235 product factory matches", flush=True)
    backend.embed = original
    after = {p: sha(child(data, p)) for p in FILES | {PROFILE}}
    if before != after or [index_hash(r) for r in (service.dense, service.civil_dense)] != profile["index_hashes"]:
        raise ValueError("Data changed during product verification")
    if code_snapshot() != snapshot:
        raise ValueError("Execution code changed during verification")
    out.mkdir(parents=True)
    write(out / "rows.json", rows)
    write(out / "audit.json", {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "clean": True, "default_data_path": data == (ROOT / "data").resolve(),
        "source_hashes": snapshot, "profile": profile, "file_hashes": before,
        "runtime_settings": runtime_settings, "inputs": len(rows),
        "final_manifest_sha256": sha(FINAL / "manifest.json"),
        "model_calls": calls, "mean_seconds": statistics.mean(r["seconds"] for r in rows),
    })
    write(out / "manifest.json", {p: sha(out / p) for p in ("rows.json", "audit.json")})
    check_verification(out)
    print("Product verified:", len(rows), "model calls:", calls)


def check_verification(out):
    check_final()
    manifest = read(out / "manifest.json")
    if set(manifest) != {"rows.json", "audit.json"} or any(sha(out / p) != h for p, h in manifest.items()):
        raise ValueError("Product verification manifest changed")
    audit = read(out / "audit.json")
    profile = expected_profile()
    if (audit["profile"] != profile or set(audit["file_hashes"]) != FILES | {PROFILE}
            or any(audit["file_hashes"][p] != h for p, h in profile["files"].items())
            or audit["runtime_settings"].get("profile") != POLICY):
        raise ValueError("Product verification corpus or policy changed")
    sources = _committed_sources(audit["commit"])
    if set(sources) != set(audit["source_hashes"]):
        raise ValueError("Incomplete execution source list")
    for name, content in sources.items():
        normalized = content.replace(b"\r\n", b"\n")
        if audit["source_hashes"][name] not in {hashlib.sha256(normalized).hexdigest(), hashlib.sha256(normalized.replace(b"\n", b"\r\n")).hexdigest()}:
            raise ValueError("Product source differs from capture commit")
    if audit["clean"] is not True or audit["inputs"] != 235 or audit["final_manifest_sha256"] != sha(FINAL / "manifest.json"):
        raise ValueError("Product verification identity changed")
    expected = read(FINAL / "rows.json")
    rows = align(read(out / "rows.json"), expected)
    for row, reference in zip(rows, expected):
        if row["query_sha256"] != reference["query_sha256"] or row["result"] != reference["record_lookup"]:
            raise ValueError("Product evidence differs from final test")
    if (any(type(r["model_calls"]) is not int or r["model_calls"] < 1 for r in rows)
            or audit["model_calls"] != sum(r["model_calls"] for r in rows)):
        raise ValueError("Model call count mismatch")
    return audit


def install_payload(staged, target, backup, *, receipt_metadata=None):
    """Roll back replaced paths if payload installation or receipt storage fails."""
    backup.mkdir(parents=True, exist_ok=False)
    copy_scope(target, backup / "snapshot")
    expected = payload_hashes(target)
    actual = payload_hashes(backup / "snapshot")
    if actual != expected:
        raise ValueError("Backup bytes differ")
    copy_scope(staged, backup / "incoming")
    # Reserve rollback directories before installation, including for disk-full errors.
    for rel in SCOPES:
        child(backup / "failed", rel).parent.mkdir(parents=True, exist_ok=True)
    touched = []
    try:
        for rel in SCOPES:
            dest, incoming, old = child(target, rel), child(backup / "incoming", rel), child(backup / "previous", rel)
            old.parent.mkdir(parents=True, exist_ok=True)
            existed = dest.exists()
            if existed:
                dest.rename(old)
            touched.append((rel, existed))
            dest.parent.mkdir(parents=True, exist_ok=True)
            if incoming.exists():
                incoming.rename(dest)
            elif rel != PROFILE:
                raise ValueError(f"Missing installation payload: {rel}")
        if payload_hashes(target) != payload_hashes(staged):
            raise ValueError("Installed payload differs from staged data")
        if receipt_metadata is not None:
            receipt = {**receipt_metadata, "before": expected}
            write(backup / "receipt.json", receipt)
            if read(backup / "receipt.json") != receipt:
                raise ValueError("Stored receipt differs from installation metadata")
    except Exception:
        for rel, existed in reversed(touched):
            dest, old = child(target, rel), child(backup / "previous", rel)
            if dest.exists():
                failed = child(backup / "failed", rel)
                failed.parent.mkdir(parents=True, exist_ok=True)
                dest.rename(failed)
            if existed:
                old.rename(dest)
        raise
    return expected


def apply(staged, verification, backup):
    audit = check_verification(verification)
    check_final()
    target = (ROOT / "data").resolve()
    backup = fresh_tmp(backup)
    if not target.is_relative_to(ROOT):
        raise ValueError("Operating data leaves this checkout")
    baseline = read(ROOT / "data/eval/patch027-full/capture/audit.json")
    if (target / PROFILE).exists():
        raise ValueError("Profile already active; do not overwrite an existing rollout")
    if any(sha(ROOT / p) != h for p, h in baseline["data_hashes"].items()):
        raise ValueError("Operating data changed since baseline")
    if code_snapshot() != audit["source_hashes"]:
        raise ValueError("Product code differs from preflight")
    # A separate process releases Chroma's Windows handles before directory swaps.
    subprocess.run([sys.executable, "-m", "scripts.patch027_rollout", "inspect", "--data", str(target)],
                   cwd=ROOT, check=True)
    if read(staged / PROFILE) != audit["profile"] or any(sha(staged / p) != h for p, h in audit["file_hashes"].items()):
        raise ValueError("Staged data differs from product preflight")
    receipt_metadata = {
        "target": "data", "backup": backup.relative_to(ROOT).as_posix(),
        "profile": audit["profile"],
        "verification_manifest_sha256": sha(verification / "manifest.json"),
    }
    install_payload(staged, target, backup, receipt_metadata=receipt_metadata)
    print("Installed profile; backup:", backup)


def inspect_baseline(data):
    from src.retrieval.dense import ChromaRetriever
    from src.retrieval.profile import index_hash

    audit = read(ROOT / "data/eval/patch027-full/capture/audit.json")
    actual = [index_hash(ChromaRetriever(None, data / p)) for p in INDEXES]
    if actual != audit["operating_index_hashes"]:
        raise ValueError("Operating indexes changed since baseline")


def restore(backup, out):
    backup = backup.resolve()
    if not backup.is_relative_to(ROOT / "tmp"):
        raise ValueError("Backup must stay inside this checkout tmp")
    receipt = read(backup / "receipt.json")
    if (receipt["target"] != "data" or receipt["backup"] != backup.relative_to(ROOT).as_posix()
            or payload_hashes(backup / "snapshot") != receipt["before"]):
        raise ValueError("Backup does not match the installation receipt")
    install_payload(backup / "snapshot", ROOT / "data", fresh_tmp(out))
    print("Restored previous data; replaced data preserved:", out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "verify", "apply", "check", "inspect", "restore"))
    parser.add_argument("--data", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--verification", type=Path)
    args = parser.parse_args()
    if args.action == "stage":
        stage(args.data, args.out)
    elif args.action == "verify":
        verify(args.data, args.out)
    elif args.action == "apply":
        apply(args.data, args.verification, args.out)
    elif args.action == "inspect":
        inspect_baseline(args.data)
    elif args.action == "restore":
        restore(args.data, args.out)
    else:
        print(check_verification(args.data)["inputs"])
