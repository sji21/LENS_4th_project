"""Measure a working-tree retrieval candidate without rewriting PATCH-041.

The uncommitted source hashes are explicit; the HEAD alone is not the candidate.
Frozen gold, model, data and channel limits remain the comparison contract.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import os
from pathlib import Path
import platform
import time

from scripts import patch041_retrieval_eval as base
from scripts.patch041_settings_contract import snapshot_settings, validate_settings
from src.evaluation.baseline import SEARCH_K, settings
from src.retrieval.multi_evidence import POLICY_CONFIG
from src.retrieval.profile import index_hash

BASELINE = base.ROOT / "data/eval/patch041-rebuilt/capture"
ARTIFACTS = ("rows.json", "report.json", "audit.json")


def sources():
    paths = list((base.ROOT / "src/retrieval").rglob("*.py"))
    paths += [base.ROOT / "setup_data.py", base.ROOT / "requirements.txt", Path(__file__)]
    return {**base.evaluator_source_hashes(),
            **{p.relative_to(base.ROOT).as_posix(): base.source_sha(p) for p in sorted(paths)}}


def reference():
    rows = base.read(BASELINE / "rows.json")
    return [{**row, "result": {
        channel: [base.norm(e["article_id"]) if channel in ("laws", "civil_laws") else e["chunk_id"]
                  for e in values] for channel, values in row["result"].items()}}
            for row in rows]


def report(rows, available):
    jobs = base.build_jobs()
    rows = base._validate_rows(rows, jobs)
    result = base.analyze(rows, available_articles=available, reference_rows=reference())
    result["comparison"]["reference"] = "PATCH-041 rebuilt production baseline"
    counts = {}
    for job, row in zip(jobs, rows):
        if job["track"] != "dev100" or job["historical"] or not job["targets"]:
            continue
        targets = set(map(base.norm, job["targets"]))
        returned = set(base._article_values(row, "laws") + base._article_values(row, "civil_laws"))
        key = job["mode"] + ":" + str(len(targets))
        bucket = counts.setdefault(key, {"n": 0, "any": 0, "all": 0, "found": 0, "targets": 0})
        bucket["n"] += 1
        bucket["any"] += bool(targets & returned)
        bucket["all"] += targets <= returned
        bucket["found"] += len(targets & returned)
        bucket["targets"] += len(targets)
    result["required_count_strata"] = counts
    return result


def capture(data_root, out):
    if out.exists():
        raise ValueError("Use a fresh output directory")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", ANONYMIZED_TELEMETRY="False")
    base.check(BASELINE)
    from huggingface_hub.constants import HF_HUB_CACHE

    data_root = data_root.resolve()
    source_before = sources()
    criteria_before = base.criteria_hashes()
    baseline_manifest = base.sha(BASELINE / "manifest.json")
    preopen = base.data_hashes(data_root)
    payload_before = base._payload_hashes(data_root)
    baseline_audit = base.read(BASELINE / "audit.json")
    if payload_before != baseline_audit["payload_hashes_after"]:
        raise ValueError("Data payload differs from baseline")
    model_files = baseline_audit["model_hashes_after"]
    model_root = Path(HF_HUB_CACHE).expanduser().resolve() / "models--nlpai-lab--KURE-v1"
    model_before = {p: base.sha(model_root / p) for p in model_files}
    if model_before != model_files:
        raise ValueError("Model differs from the baseline")
    service, profile, logical_before = base.prepare_product_service(data_root)
    if profile != baseline_audit["profile"] or logical_before != baseline_audit["logical_index_hashes_after"]:
        raise ValueError("Profile or logical index differs from baseline")
    data_before = base.data_hashes(data_root)
    config_before = snapshot_settings(settings(service))
    selection_before = dict(service.selection_config)
    if selection_before != dict(POLICY_CONFIG):
        raise ValueError("Service did not activate the candidate selection policy")
    validate_settings(config_before)
    available = sorted({base.norm(c["metadata"]["article_id"]) for c in service._chunks.values()
                        if c["metadata"].get("article_id")})
    rows = []
    with base.KureCallCounter(service) as counter:
        for number, job in enumerate(base.build_jobs(), 1):
            calls = counter.actual_calls
            started = time.perf_counter()
            result = service.search(job["query"], **SEARCH_K)
            rows.append({"qid": job["qid"], "mode": job["mode"], "group": job["group"],
                         "query_sha256": job["query_sha256"], "seconds": time.perf_counter() - started,
                         "model_calls": counter.actual_calls - calls, "result": base._serialize(service, result)})
            if number % 25 == 0:
                print(f"{number}/235 candidate searches", flush=True)
    config_after = snapshot_settings(settings(service))
    validate_settings(config_after)
    observed = {
        "sources": (source_before, sources()),
        "criteria": (criteria_before, base.criteria_hashes()),
        "data": (data_before, base.data_hashes(data_root)),
        "payload": (payload_before, base._payload_hashes(data_root)),
        "model": (model_before, {p: base.sha(model_root / p) for p in model_files}),
        "logical_indices": (logical_before, [index_hash(r) for r in (service.dense, service.civil_dense)]),
        "settings": (config_before, config_after),
        "selection": (selection_before, dict(service.selection_config)),
    }
    if any(before != after for before, after in observed.values()):
        raise ValueError("Inputs, settings, code or data changed during capture")
    if base.sha(BASELINE / "manifest.json") != baseline_manifest:
        raise ValueError("Baseline changed")
    audit = {
        "schema": "patch046-working-tree-candidate-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "head": base._git("rev-parse", "HEAD"), "git_status": base._git("status", "--porcelain"),
        "baseline_manifest_sha256": baseline_manifest,
        "generation_performed": False, "channel_limits": base.CHANNEL_LIMITS,
        "available_articles": available, "model_usage": counter.counts,
        "device": str(service.dense.backend.delegate._model.device), "profile": profile,
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": {name: importlib.metadata.version(name) for name in
                     ("torch", "transformers", "sentence-transformers", "chromadb", "numpy")},
        "data_root": str(data_root), "before_after": observed,
        "index_opening_changes": sorted(p for p in set(preopen) | set(data_before)
                                        if preopen.get(p) != data_before.get(p)),
        "scope": "Frozen DEV/public criteria; working-tree candidate, not independent evaluation or LLM quality",
    }
    result = report(rows, available)
    out.mkdir(parents=True)
    for name, value in (("rows.json", rows), ("audit.json", audit), ("report.json", result)):
        base.write(out / name, value)
    base.write(out / "manifest.json", {name: base.sha(out / name) for name in ARTIFACTS})
    check(out)
    print({mode: result["groups"][mode]["union_all_required"] for mode in base.DEV_MODES}, flush=True)


def check(out):
    manifest = base.read(out / "manifest.json")
    if set(manifest) != set(ARTIFACTS) or any(base.sha(out / name) != digest for name, digest in manifest.items()):
        raise ValueError("Candidate manifest mismatch")
    audit, rows = base.read(out / "audit.json"), base.read(out / "rows.json")
    if (audit["schema"] != "patch046-working-tree-candidate-v1" or audit["generation_performed"] is not False
            or audit["channel_limits"] != base.CHANNEL_LIMITS
            or audit["baseline_manifest_sha256"] != base.sha(BASELINE / "manifest.json")):
        raise ValueError("Candidate contract mismatch")
    base.check(BASELINE)
    baseline_audit = base.read(BASELINE / "audit.json")
    pairs = audit["before_after"]
    if set(pairs) != {"sources", "criteria", "data", "payload", "model", "logical_indices", "settings", "selection"}:
        raise ValueError("Missing capture checks")
    if any(not before or before != after for before, after in pairs.values()):
        raise ValueError("Unstable candidate capture")
    for candidate_key, baseline_key in (("payload", "payload_hashes_after"),
                                        ("model", "model_hashes_after"),
                                        ("logical_indices", "logical_index_hashes_after")):
        if pairs[candidate_key][0] != baseline_audit[baseline_key]:
            raise ValueError("Candidate differs from frozen " + candidate_key)
    if audit["profile"] != baseline_audit["profile"]:
        raise ValueError("Candidate profile drift")
    for config in pairs["settings"]:
        validate_settings(config)
    if pairs["selection"][0] != dict(POLICY_CONFIG):
        raise ValueError("Candidate selection policy drift")
    if pairs["criteria"][0] != base.criteria_hashes():
        raise ValueError("Gold criteria drift")
    usage = audit["model_usage"]
    if (len(rows) != 235 or any(r["model_calls"] <= 0 for r in rows)
            or usage["actual_kure_calls"] != sum(r["model_calls"] for r in rows)
            or any(value <= 0 for value in usage.values())):
        raise ValueError("Missing actual KURE measurements")
    if report(rows, audit["available_articles"]) != base.read(out / "report.json"):
        raise ValueError("Candidate report does not replay")
    print("Candidate hashes and all scores verified", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "check"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()
    if args.action == "capture":
        if args.data_root is None:
            parser.error("--data-root is required for capture")
        capture(args.data_root, args.out)
    else:
        check(args.out)
