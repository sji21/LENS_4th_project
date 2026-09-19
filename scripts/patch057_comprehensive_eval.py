"""Freeze, capture, and score PATCH-057 retrieval evaluation artifacts.

``prepare`` is deliberately model-free.  It freezes the reviewed inputs, gold
identities, old-to-new case mapping, and corpus/profile hashes before any query
is sent to the retriever.  ``capture`` requires that bundle to be committed,
then records both full-channel capacity and the product's staged delivery.
``score`` is a pure replay over the captured JSON.

This module never calls the answer-generation model and never changes gold.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter
from urllib.parse import parse_qs, urlparse

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts import patch041_retrieval_eval as reviewed
from src.evaluation.baseline import load_dataset
from src.generation.evidence_routing import retrieve_staged
from src.retrieval.retriever import load_chunks


ROOT = _PROJECT_ROOT
SCHEMA = "patch057-comprehensive-retrieval-v1"
PUBLIC_SPECS = (
    ("legacy_dev", ROOT / "data/eval/dev.jsonl", "general"),
    ("legacy_holdout", ROOT / "data/eval/holdout.jsonl", "general"),
    ("civil_public", ROOT / "data/eval/minbeop_review_holdout_20260901.jsonl", "combined"),
)
HO_ROOT = ROOT / "data/eval/holdout-v2-ho30-20260918"
BASE_CHUNKS = ("chunks/chunks.jsonl", "chunks/cases.jsonl", "chunks/guides.jsonl")
EXPECTED = {
    "reviewed": 235,
    "public_scored": {"legacy_dev": 24, "legacy_holdout": 18, "civil_public": 15,
                      "case_dev13": 13, "case_external8": 8, "case_legacy20": 20},
    "public_inputs": {"legacy_dev": 24, "legacy_holdout": 18, "civil_public": 20,
                      "case_dev13": 13, "case_external8": 8, "case_legacy20": 20},
    "ho30": 30,
    "jobs": 368,
}
RUN_BUDGETS = {
    "raw3": {"laws": 3, "civil_laws": 3, "cases": 5, "forms": 0},
    "raw5": {"laws": 5, "civil_laws": 3, "cases": 5, "forms": 0},
    "staged_operating": {"laws": 3, "civil_laws": 3, "cases": 2, "forms": 0},
    "staged_diagnostic": {"laws": 5, "civil_laws": 3, "cases": 5, "forms": 0},
}


def sha(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path: Path, value) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(
        encoding="utf-8-sig").splitlines() if line.strip()]


def normalized(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).lower()


def case_number_set(value: object) -> frozenset[str]:
    """Canonicalize a single or consolidated Korean case-number bundle."""
    parts = [part for part in re.split(r"[,·/]", str(value or "")) if part.strip()]
    if not parts:
        return frozenset()
    first = re.fullmatch(r"\s*(\d{2,4})([가-힣]{1,4})(\d+)\s*", parts[0])
    if first is None:
        return frozenset()
    prefix = first.group(1) + first.group(2)
    result = {prefix + first.group(3)}
    for part in parts[1:]:
        full = re.fullmatch(r"\s*(\d{2,4})([가-힣]{1,4})(\d+)\s*", part)
        abbreviated = re.fullmatch(r"\s*(\d+)\s*", part)
        if full:
            result.add(full.group(1) + full.group(2) + full.group(3))
        elif abbreviated:
            result.add(prefix + abbreviated.group(1))
        else:
            return frozenset()
    return frozenset(result)


def law_identity(value: object) -> str:
    """Match the established evaluator's NFC/whitespace article identity."""
    return reviewed.norm(str(value or ""))


def target(label: str, identity: str, available: bool, *, source_code: str | None = None) -> dict:
    return {"label": label, "identity": identity, "available": bool(available),
            **({"source_code": source_code} if source_code else {})}


def split_law_targets(values: list[str], available: set[str]) -> dict[str, list[dict]]:
    result = {"laws": [], "civil_laws": [], "cases": [], "forms": []}
    normalized_available = {law_identity(item) for item in available}
    for value in dict.fromkeys(values):
        identity = law_identity(value)
        channel = "civil_laws" if identity.startswith("민법-") else "laws"
        result[channel].append(target(value, identity, identity in normalized_available))
    return result


def all_files(root: Path) -> list[Path]:
    return sorted(path for path in Path(root).rglob("*") if path.is_file())


def base_chunks(data_root: Path) -> list[dict]:
    paths = [Path(data_root) / name for name in BASE_CHUNKS]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError("Missing base retrieval chunks: " + ", ".join(missing))
    chunks = [chunk for path in paths for chunk in load_chunks(path)]
    ids = [chunk["chunk_id"] for chunk in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate base chunk_id")
    return chunks


def case_profile_chunks(profile_path: Path) -> tuple[dict, list[dict]]:
    profile = read(profile_path)
    data_root = Path(profile.get("data_root", ""))
    if not data_root.is_absolute():
        raise ValueError("Case profile data_root must be absolute")
    names = ("chunks/laws.jsonl", "chunks/cases.jsonl", "chunks/guides.jsonl")
    chunks = [chunk for name in names for chunk in load_chunks(data_root / name)]
    return profile, chunks


def corpus_case_index(chunks: list[dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        if metadata.get("doc_type") != "case":
            continue
        result.setdefault(normalized(metadata.get("case_number")), []).append(metadata)
    return result


def map_legacy_cases(case_dev: Path, case_external: Path, legacy_case_chunks: Path,
                     current_chunks: list[dict]) -> tuple[dict, dict]:
    rows = jsonl(case_dev) + jsonl(case_external)
    wanted = sorted({case_id for row in rows for case_id in row["gold_case_ids"]})
    old = {str(chunk["metadata"].get("case_id")): chunk["metadata"]
           for chunk in load_chunks(legacy_case_chunks)}
    current = corpus_case_index(current_chunks)
    mapping, summary = {}, {"requested": len(wanted), "mapped": 0, "lower_bound_coverage": 0.0,
                            "unknown": [], "missing": [], "metadata_mismatch": [], "ambiguous": []}
    for old_id in wanted:
        metadata = old.get(old_id)
        if metadata is None:
            raise ValueError("Legacy case metadata missing: " + old_id)
        matches = current.get(normalized(metadata.get("case_number")), [])
        # A case number should be unique.  Court and date are additional guards,
        # never fuzzy fallback rules.
        guarded = [item for item in matches
                   if (not metadata.get("court_name") or
                       normalized(item.get("court_name")) == normalized(metadata.get("court_name")))
                   and (not metadata.get("decision_date") or
                        str(item.get("decision_date")) == str(metadata.get("decision_date")))]
        unguarded = matches
        matches = guarded
        entry = {"old_case_id": old_id, "case_number": metadata.get("case_number"),
                 "court_name": metadata.get("court_name"),
                 "decision_date": metadata.get("decision_date"), "matches": [
                     {key: item.get(key) for key in ("case_id", "canonical_case_key", "case_number",
                                                     "court_name", "decision_date", "source_url")}
                     for item in matches]}
        if len(matches) == 1:
            item = matches[0]
            entry.update(status="mapped", case_id=str(item["case_id"]),
                         canonical_case_key=str(item["canonical_case_key"]))
            summary["mapped"] += 1
        else:
            if not unguarded:
                reason = "missing"
            elif not matches:
                reason = "metadata_mismatch"
            else:
                reason = "ambiguous"
            entry.update(status="unmapped", unresolved_reason=reason)
            summary[reason].append(old_id)
            summary["unknown"].append(old_id)
        mapping[old_id] = entry
    summary["lower_bound_coverage"] = summary["mapped"] / summary["requested"] if wanted else 0.0
    return mapping, summary


def mapped_case_targets(values: list[str], mapping: dict) -> list[dict]:
    result = []
    for value in dict.fromkeys(values):
        item = mapping[value]
        identity = (item.get("canonical_case_key") if item["status"] == "mapped"
                    else "unavailable:legacy-case:" + value)
        result.append(target(value, identity, item["status"] == "mapped"))
    return result


def official_case_targets(values: list[str], current_chunks: list[dict]) -> list[dict]:
    """Map published Law.go.kr sequence IDs to the new corpus without guessing."""
    by_id: dict[str, list[dict]] = {}
    for chunk in current_chunks:
        metadata = chunk.get("metadata", {})
        if metadata.get("doc_type") == "case":
            by_id.setdefault(str(metadata.get("case_id")), []).append(metadata)
    result = []
    for value in dict.fromkeys(values):
        matches = by_id.get(str(value), [])
        available = len(matches) == 1 and bool(matches[0].get("canonical_case_key"))
        identity = (str(matches[0]["canonical_case_key"]) if available
                    else "unavailable:official-case:" + str(value))
        result.append(target(str(value), identity, available))
    return result


def source_identity(code: str, source: dict, article_ids: set[str], case_chunks: list[dict]) -> tuple[str, str, bool]:
    title = str(source["title"])
    if code.startswith("P"):
        query = parse_qs(urlparse(str(source.get("url", ""))).query)
        seq = (query.get("precSeq") or [""])[0]
        candidates = [c["metadata"] for c in case_chunks
                      if c["metadata"].get("doc_type") == "case"
                      and (seq and str(c["metadata"].get("case_id")) == seq)]
        court = title.split(maxsplit=1)[0]
        if not candidates:
            bundle = title.split("선고", 1)[1].strip() if "선고" in title else ""
            wanted_numbers = case_number_set(bundle)
            candidates = [c["metadata"] for c in case_chunks
                          if c["metadata"].get("doc_type") == "case"
                          and wanted_numbers
                          and case_number_set(c["metadata"].get("case_number")) == wanted_numbers]
        candidates = [item for item in candidates
                      if normalized(item.get("court_name")) == normalized(court)]
        unique = {str(item.get("canonical_case_key")): item for item in candidates
                  if item.get("canonical_case_key")}
        if len(unique) == 1:
            return "cases", next(iter(unique)), True
        return "cases", "unavailable:ho-source:" + code, False
    if "별지" in title and "서식" in title:
        identity = law_identity(re.sub(r"\s+(별지\s+제\d+호서식)$", r"-\1", title))
        return "forms", identity, identity in {law_identity(item) for item in article_ids}
    match = re.fullmatch(r"(.+?)\s+(제\d+조(?:의\d+)?)", title)
    if not match:
        raise ValueError(f"Cannot derive source identity: {code}: {title}")
    identity = law_identity(f"{match.group(1)}-{match.group(2)}")
    channel = "civil_laws" if identity.startswith("민법-") else "laws"
    return channel, identity, identity in {law_identity(item) for item in article_ids}


def reviewed_jobs(article_ids: set[str]) -> list[dict]:
    result = []
    for item in reviewed.build_jobs():
        score = bool(item["targets"]) and not item["historical"] and item["track"] in ("dev100", "required_law")
        result.append({"qid": item["qid"], "mode": item["mode"], "group": item["group"],
                       "track": item["track"], "query": item["query"],
                       "query_sha256": item["query_sha256"],
                       "targets": split_law_targets(item["targets"], article_ids),
                       "score": score,
                       "diagnostic_score": bool(item["targets"]) and item["track"] in (
                           "scope_provisional", "diagnostic_only"),
                       "scope": "combined", "suite": "reviewed235"})
    return result


def public_law_jobs(article_ids: set[str]) -> list[dict]:
    result = []
    for group, path, scope in PUBLIC_SPECS:
        rows = load_dataset(path, "law")
        for row in rows:
            gold = list(dict.fromkeys(row["gold_articles"]))
            # Preserve all civil-public inputs to observe abstain/out-of-scope
            # behavior.  Historical general sets retain their published scored
            # subset, matching PATCH-051/053's 24 + 18 denominator.
            if not gold and group != "civil_public":
                continue
            removed_guides = [value for value in gold if value.startswith("guide-")]
            law_gold = [value for value in gold if value not in removed_guides]
            if not law_gold and group != "civil_public":
                continue
            score = bool(law_gold)
            result.append({"qid": row["qid"], "mode": "question_only", "group": group,
                           "track": group, "query": row["question"],
                           "query_sha256": text_sha(row["question"]),
                           "targets": split_law_targets(law_gold, article_ids),
                           "score": score, "scope": scope, "suite": "public_regression",
                           "answer_type": row.get("answer_type"),
                           "excluded_guide_gold": removed_guides})
    return result


def public_case_jobs(path: Path, group: str, mapping: dict) -> list[dict]:
    return [{"qid": row["qid"], "mode": "question_only", "group": group, "track": group,
             "query": row["question"], "query_sha256": text_sha(row["question"]),
             "targets": {"laws": [], "civil_laws": [],
                         "cases": mapped_case_targets(row["gold_case_ids"], mapping), "forms": []},
             "score": True, "scope": "case", "suite": "public_regression"}
            for row in load_dataset(path, "case")]


def legacy20_jobs(path: Path, current_chunks: list[dict]) -> list[dict]:
    return [{"qid": row["qid"], "mode": "question_only", "group": "case_legacy20",
             "track": "case_legacy20", "query": row["question"],
             "query_sha256": text_sha(row["question"]),
             "targets": {"laws": [], "civil_laws": [],
                         "cases": official_case_targets(row["gold_case_ids"], current_chunks),
                         "forms": []},
             "score": True, "scope": "case", "suite": "public_regression"}
            for row in load_dataset(path, "case")]


def holdout_jobs(article_ids: set[str], case_chunks: list[dict]) -> tuple[list[dict], dict]:
    contract = read(HO_ROOT / "evaluation-contract.json")
    gold = read(HO_ROOT / contract["gold_file"])
    questions = {row["id"]: row["question"] for row in jsonl(HO_ROOT / contract["input_file"])}
    sources = gold["sources"]
    jobs, inventory = [], {}
    for item in contract["items"]:
        qid = item["id"]
        targets = {"laws": [], "civil_laws": [], "cases": [], "forms": []}
        for code in item["required_statutes_all_of"] + item["required_other_sources_all_of"]:
            channel, identity, available = source_identity(code, sources[code], article_ids, case_chunks)
            targets[channel].append(target(sources[code]["title"], identity, available, source_code=code))
            inventory.setdefault(code, {"channel": channel, "identity": identity,
                                        "available": available, "source": sources[code]})
        jobs.append({"qid": qid, "mode": "question_only", "group": "ho30",
                     "track": item["stratum"], "query": questions[qid],
                     "query_sha256": text_sha(questions[qid]), "targets": targets,
                     "score": True, "scope": "mixed", "suite": "holdout30"})
    return jobs, inventory


def source_files(base_data: Path, case_profile: Path, case_dev: Path,
                 case_external: Path, legacy_case_chunks: Path) -> dict:
    from huggingface_hub.constants import HF_HUB_CACHE
    from src.retrieval.profile import FILES, INDEXES, PROFILE

    receipts = {Path(case_dev).resolve(), Path(case_external).resolve(),
                Path(legacy_case_chunks).resolve()}
    paths = [case_profile, case_dev, case_external, legacy_case_chunks]
    paths += [Path(base_data) / name for name in FILES | {PROFILE}]
    for name in INDEXES:
        paths += all_files(Path(base_data) / name)
    case_header = read(case_profile)
    case_root = Path(case_header["data_root"])
    paths += [case_root / name for name in case_header["files"]]
    paths += [case_root / "index/chroma_kurev1_1024" / name
              for name in case_header.get("index_files", {})]
    release = case_root / "release.json"
    if release.is_file():
        paths.append(release)
    model_root = (Path(HF_HUB_CACHE) / "models--nlpai-lab--KURE-v1" /
                  "snapshots" / case_header["model_revision"])
    model_files = all_files(model_root) if model_root.is_dir() else []
    if not model_files:
        raise ValueError("Pinned KURE model snapshot is not installed: " + str(model_root))
    paths += model_files
    paths += [HO_ROOT / name for name in ("questions.jsonl", "evaluation-contract.json",
                                           "review/05_최종기준_구조화.json")]
    paths.append(ROOT / "data/eval/case_holdout_current_20.jsonl")
    paths += [path for _, path, _ in PUBLIC_SPECS]
    paths += [ROOT / "data/eval/dev100-v2/manifest.json",
              ROOT / "data/eval/civil-review2/manifest.json"]
    paths += [ROOT / name for name in (
        "scripts/patch057_comprehensive_eval.py", "scripts/patch041_retrieval_eval.py",
        "scripts/dev100_v2/report.py", "src/evaluation/baseline.py",
        "src/generation/evidence_routing.py")]
    paths += sorted((ROOT / "src/retrieval").glob("*.py"))
    environment_receipts = ROOT / "data/eval/patch057-comprehensive"
    if environment_receipts.is_dir():
        # Only top-level receipts; never recursively hash the protocol itself.
        paths += sorted(path for path in environment_receipts.iterdir() if path.is_file())
    return {str(path.resolve()): {"sha256": sha(path), "bytes": path.stat().st_size,
                                  "required_at_capture": path.resolve() not in receipts}
            for path in sorted(set(paths), key=lambda item: str(item.resolve()))}


def job_counts(jobs: list[dict]) -> dict:
    groups: dict[str, dict[str, int]] = {}
    for job in jobs:
        bucket = groups.setdefault(job["group"], {"inputs": 0, "scored": 0})
        bucket["inputs"] += 1
        bucket["scored"] += bool(job["score"])
    return {"total": len(jobs), "groups": groups}


def prepare(base_data: Path, case_profile: Path, case_dev: Path, case_external: Path,
            legacy_case_chunks: Path, out: Path) -> None:
    if out.exists():
        raise ValueError("Use a fresh protocol directory")
    base = base_chunks(base_data)
    article_ids = {str(c["metadata"].get("article_id")) for c in base
                   if c["metadata"].get("article_id")}
    profile, case = case_profile_chunks(case_profile)
    mapping, mapping_summary = map_legacy_cases(case_dev, case_external, legacy_case_chunks, case)
    jobs = reviewed_jobs(article_ids) + public_law_jobs(article_ids)
    jobs += public_case_jobs(case_dev, "case_dev13", mapping)
    jobs += public_case_jobs(case_external, "case_external8", mapping)
    jobs += legacy20_jobs(ROOT / "data/eval/case_holdout_current_20.jsonl", case)
    holdout, source_inventory = holdout_jobs(article_ids, case)
    jobs += holdout
    keys = [(item["suite"], item["qid"], item["mode"]) for item in jobs]
    if len(jobs) != EXPECTED["jobs"] or len(keys) != len(set(keys)):
        raise ValueError(f"Expected {EXPECTED['jobs']} unique jobs, got {len(jobs)}")
    counts = job_counts(jobs)
    for group, expected in EXPECTED["public_inputs"].items():
        if counts["groups"].get(group, {}).get("inputs") != expected:
            raise ValueError(f"Input denominator drift: {group}")
    for group, expected in EXPECTED["public_scored"].items():
        if counts["groups"].get(group, {}).get("scored") != expected:
            raise ValueError(f"Score denominator drift: {group}")
    out.mkdir(parents=True)
    inputs = out / "inputs"
    inputs.mkdir()
    for source, name in ((case_dev, "case-dev13.jsonl"),
                         (case_external, "case-external8.jsonl"),
                         (ROOT / "data/eval/case_holdout_current_20.jsonl", "case-legacy20.jsonl")):
        (inputs / name).write_bytes(Path(source).read_bytes())
    write(out / "jobs.json", jobs)
    write(out / "case-id-map.json", {"method": "exact normalized case number, guarded by court/date",
                                      "summary": mapping_summary, "mapping": mapping})
    protocol = {
        "schema": SCHEMA, "status": "prepared_no_queries_run",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "generation_performed": False, "retrieval_performed": False,
        "prepared_from_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "paths": {"base_data": str(Path(base_data).resolve()),
                  "case_profile": str(Path(case_profile).resolve()),
                  "case_dev": str(Path(case_dev).resolve()),
                  "case_external": str(Path(case_external).resolve()),
                  "legacy_case_chunks": str(Path(legacy_case_chunks).resolve())},
        "source_files": source_files(base_data, case_profile, case_dev, case_external,
                                      legacy_case_chunks),
        "case_profile_identity": {key: profile.get(key) for key in (
            "schema", "version", "model_id", "model_revision", "dimension", "candidate_depth",
            "return_k", "rerank_policy", "case_rrf_k", "case_bm25_weight", "case_dense_weight",
            "case_query_expansion", "case_field_policy", "index_content_sha256")},
        "budgets": {"raw": [{"laws": 3, "civil_laws": 3, "cases": 5, "guides": 2},
                              {"laws": 5, "civil_laws": 3, "cases": 5, "guides": 2}],
                    "staged_operating": {"laws": 3, "civil_laws": 3, "cases": 2, "guides": 2},
                    "staged_diagnostic": {"laws": 5, "civil_laws": 3, "cases": 5, "guides": 2}},
        "scoring_contract": {
            "channel_metrics": "Hit@1/3/5, MRR, recall and all-required; case also Hit@2",
            "combined_rank": "Separate channel ranks are never pooled",
            "missing_gold": "Retained in all-required and recall denominators",
            "ho_law_complete_n": 30, "ho_full_evidence_complete_n": 30,
            "ho_strata": {"statute_only": 27, "mixed_source": 3},
            "ho_occurrences": {"statutes": 44, "other_sources": 4},
            "reviewed_denominators": {"question_only_all_required": 75,
                                      "context_diagnostic_all_required": 75,
                                      "general_target_items_each_mode": 47,
                                      "civil_target_items_each_mode": 32,
                                      "civil35_required_law": 29,
                                      "civil35_scope_provisional": 1,
                                      "civil35_diagnostic_only": 5}},
        "expected": EXPECTED, "counts": counts, "holdout_source_inventory": source_inventory,
        "limitations": "Retrieval only; no answer generation or semantic answer grading."
    }
    write(out / "protocol.json", protocol)
    names = ("jobs.json", "case-id-map.json", "protocol.json",
             "inputs/case-dev13.jsonl", "inputs/case-external8.jsonl",
             "inputs/case-legacy20.jsonl")
    write(out / "manifest.json", {name: sha(out / name) for name in names})


def verify_bundle(bundle: Path) -> tuple[dict, list[dict]]:
    manifest = read(bundle / "manifest.json")
    expected_files = {"jobs.json", "case-id-map.json", "protocol.json",
                      "inputs/case-dev13.jsonl", "inputs/case-external8.jsonl",
                      "inputs/case-legacy20.jsonl"}
    if set(manifest) != expected_files:
        raise ValueError("Protocol manifest file set differs")
    for name, expected in manifest.items():
        if sha(bundle / name) != expected:
            raise ValueError("Protocol manifest mismatch: " + name)
    protocol, jobs = read(bundle / "protocol.json"), read(bundle / "jobs.json")
    if protocol.get("schema") != SCHEMA or len(jobs) != EXPECTED["jobs"]:
        raise ValueError("Unsupported protocol or job count")
    for name, identity in protocol["source_files"].items():
        path = Path(name)
        if identity.get("required_at_capture", True) and (
                not path.is_file() or sha(path) != identity["sha256"]):
            raise ValueError("Frozen source differs: " + name)
    return protocol, jobs


def metadata_lookup(service, chunk_id: str, *, owner=None) -> dict:
    owners = ([owner] if owner is not None else
              [service, getattr(service, "base_service", None)])
    for current in owners:
        if current is not None and chunk_id in getattr(current, "_chunks", {}):
            return current._chunks[chunk_id]
    raise KeyError("Unknown returned chunk: " + chunk_id)


def serialized_evidence(service, evidence) -> dict:
    owner = service if evidence.doc_type == "case" else getattr(service, "base_service", service)
    chunk = metadata_lookup(service, evidence.chunk_id, owner=owner)
    metadata = chunk["metadata"]
    return {**asdict(evidence), "text_sha256": text_sha(evidence.text),
            "article_id": metadata.get("article_id"), "case_id": metadata.get("case_id"),
            "canonical_case_key": metadata.get("canonical_case_key"),
            "guide_id": metadata.get("guide_id"), "metadata": metadata}


def serialized_result(service, result) -> dict:
    return {"question": result.question, "civil_topics": list(result.civil_topics),
            **{name: [serialized_evidence(service, item) for item in getattr(result, name)]
               for name in ("laws", "civil_laws", "cases", "guides")}}


def serialized_candidates(service, ids: list[str], *, owner=None) -> list[dict]:
    rows = []
    for rank, chunk_id in enumerate(ids, 1):
        chunk = metadata_lookup(service, chunk_id, owner=owner)
        metadata = chunk["metadata"]
        rows.append({"rank": rank, "chunk_id": chunk_id,
                     "text_sha256": text_sha(chunk["text"]),
                     "article_id": metadata.get("article_id"),
                     "case_id": metadata.get("case_id"),
                     "canonical_case_key": metadata.get("canonical_case_key"),
                     "identity": metadata.get("canonical_case_key") or metadata.get("article_id")
                     or metadata.get("guide_id") or chunk_id})
    return rows


class MemberObserver:
    def __init__(self, service):
        self.service = service
        self.base = service.base_service
        self.current: dict[str, dict] = {}
        self.originals = []

    def install(self):
        for name, retriever in (("laws", self.base._context_law),
                                ("civil_laws", self.base._context_civil)):
            original = retriever.search_with_member_hits
            self.originals.append((retriever, original))

            def observed(*args, _name=name, _retriever=retriever, _original=original, **kwargs):
                ranked, members = _original(*args, **kwargs)
                self.current[_name] = {"fused": serialized_candidates(
                    self.service, [cid for cid, _ in ranked], owner=self.base),
                    "members": {member: serialized_candidates(self.service, ids, owner=self.base)
                                for member, ids in members.items()},
                    "where": kwargs.get("where", args[2] if len(args) > 2 else None)}
                return ranked, members
            retriever.search_with_member_hits = observed
        return self

    def close(self):
        for retriever, original in self.originals:
            retriever.search_with_member_hits = original

    def clear(self):
        self.current = {}


def load_service(protocol: dict):
    native_temp = ROOT / "tmp/native"
    native_temp.mkdir(parents=True, exist_ok=True)
    os.environ.update(TEMP=str(native_temp), TMP=str(native_temp),
                      HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", ANONYMIZED_TELEMETRY="False")
    from src.retrieval.profile import index_hash
    from src.retrieval.case_profile import load_case_profile
    from src.retrieval.service import RetrievalService

    data_root = Path(protocol["paths"]["base_data"])
    base_profile = read(data_root / "index/retrieval-profile.json")
    base = RetrievalService._from_index_without_case_profile(
        chunk_paths=tuple(data_root / name for name in BASE_CHUNKS),
        index_path=data_root / "index/chroma_kurev1_1024",
        model=base_profile["model"],
        civil_index_path=data_root / "index/chroma_civil_kurev1_1024")
    base_indices = [index_hash(retriever) for retriever in (base.dense, base.civil_dense)]
    if base_indices != base_profile["index_hashes"]:
        raise ValueError("Base logical index differs from frozen profile")
    service = load_case_profile(Path(protocol["paths"]["case_profile"]), attach_base=False)
    service.base_service = base
    if service.case_profile.get("fallback_allowed") is not False:
        raise ValueError("Case fallback must remain disabled")
    expected = protocol["case_profile_identity"]
    if any(service.case_profile.get(key) != value for key, value in expected.items()):
        raise ValueError("Case profile differs from frozen protocol")
    devices = {"base": str(base.dense.backend.delegate._model.device),
               "case": str(service.dense.backend._model.device)}
    if any(not value.lower().startswith("cuda") for value in devices.values()):
        raise ValueError("PATCH-057 capture requires CUDA for every KURE backend: " + str(devices))
    for owner, corpus in ((base, base.corpora[0]), (base, base.civil),
                          (service, service.corpora[1])):
        retriever = owner._retrievers[corpus.name]
        names = {member.name for member in retriever.members}
        if not any(name.endswith("bm25") or name == "bm25_context" for name in names):
            raise ValueError("BM25 member missing: " + corpus.name)
        if not any(name.endswith("dense") or name == "dense_context" for name in names):
            raise ValueError("KURE member missing: " + corpus.name)
    return service, {"devices": devices, "base_profile": base_profile,
                     "base_logical_indices": base_indices}


def case_trace(service, trace: dict) -> dict:
    return {"where": trace.get("where"),
            "raw_rrf_candidates": [{"score": score, **item}
                for item, (_, score) in zip(serialized_candidates(
                    service, [cid for cid, _ in trace.get("raw_rrf_candidates", [])], owner=service),
                    trace.get("raw_rrf_candidates", []))],
            "members": {name: serialized_candidates(service, ids, owner=service)
                        for name, ids in trace.get("member_hits", {}).items()}}


class CaptureDriver:
    def __init__(self, service, observer):
        self.service, self.observer = service, observer

    def search(self, question, k_law=5, k_case=5, k_guide=2, *, k_civil=None):
        self.observer.clear()
        started = perf_counter()
        result, trace = self.service.search_with_trace(
            question, k_law=k_law, k_case=k_case, k_guide=k_guide, k_civil=k_civil)
        self.last = {"request": {"k_law": k_law, "k_case": k_case,
                                  "k_guide": k_guide, "k_civil": k_civil},
                     "latency_seconds": perf_counter() - started,
                     "result": serialized_result(self.service, result),
                     "member_traces": {**self.observer.current, "cases": case_trace(self.service, trace)}}
        return result


class StagedProxy:
    def __init__(self, driver):
        self.driver, self.calls = driver, []

    def search(self, *args, **kwargs):
        result = self.driver.search(*args, **kwargs)
        self.calls.append(self.driver.last)
        return result


def committed_protocol(bundle: Path, commit: str) -> None:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    resolved = subprocess.check_output(["git", "rev-parse", commit], cwd=ROOT, text=True).strip()
    if resolved != head:
        raise ValueError("Approved protocol commit must be current HEAD")
    relative = bundle.resolve().relative_to(ROOT.resolve())
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", str(relative)],
                                    cwd=ROOT, text=True).strip()
    if dirty:
        raise ValueError("Protocol bundle has uncommitted changes")
    tracked = ("manifest.json", "jobs.json", "case-id-map.json", "protocol.json",
               "inputs/case-dev13.jsonl", "inputs/case-external8.jsonl",
               "inputs/case-legacy20.jsonl")
    for name in tracked:
        subprocess.check_call(["git", "cat-file", "-e", f"{head}:{(relative / name).as_posix()}"], cwd=ROOT)


def capture(bundle: Path, protocol_commit: str, out: Path) -> None:
    if out.exists():
        raise ValueError("Use a fresh capture directory")
    protocol, jobs = verify_bundle(bundle)
    committed_protocol(bundle, protocol_commit)
    service, environment = load_service(protocol)
    # Loading native indexes must not mutate the frozen source bundle before
    # the first evaluated question.
    verify_bundle(bundle)
    observer = MemberObserver(service).install()
    driver = CaptureDriver(service, observer)
    out.mkdir(parents=True)
    rows_path = out / "rows.jsonl"
    completed = 0
    try:
        with rows_path.open("x", encoding="utf-8", newline="\n") as stream:
            for number, job in enumerate(jobs, 1):
                runs = {}
                for k in (3, 5):
                    driver.search(job["query"], k_law=k, k_case=5, k_guide=2, k_civil=3)
                    runs[f"raw{k}"] = driver.last
                for name, limits in (("staged_operating", (3, 2, 2)),
                                     ("staged_diagnostic", (5, 5, 2))):
                    proxy = StagedProxy(driver)
                    started = perf_counter()
                    staged = retrieve_staged(proxy, job["query"], k_law=limits[0],
                                             k_case=limits[1], k_guide=limits[2])
                    runs[name] = {"latency_seconds": perf_counter() - started,
                                  "result": serialized_result(service, staged.result),
                                  "route": asdict(staged.route), "calls": proxy.calls,
                                  "latency_note": "actual retrieve_staged calls; no cached conversion"}
                stream.write(json.dumps({"job": job, "runs": runs}, ensure_ascii=False) + "\n")
                stream.flush()
                completed = number
                if number % 25 == 0:
                    print(f"{number}/{len(jobs)}", flush=True)
    finally:
        observer.close()
    # Re-hash every frozen input/source after the long run and re-check logical
    # indexes.  This catches edits during capture without trusting mtimes.
    verify_bundle(bundle)
    from src.retrieval.case_profile import index_content_hash
    from src.retrieval.profile import index_hash
    if environment["base_logical_indices"] != [index_hash(r) for r in (
            service.base_service.dense, service.base_service.civil_dense)]:
        raise ValueError("Base logical index changed during capture")
    if protocol["case_profile_identity"]["index_content_sha256"] != index_content_hash(
            service.dense.collection):
        raise ValueError("Case logical index changed during capture")
    audit = {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
             "protocol_commit": protocol_commit, "protocol_manifest_sha256": sha(bundle / "manifest.json"),
             "generation_performed": False, "queries": completed, "environment": environment,
             "candidate_trace_text": "Candidate text is referenced by frozen chunk ID and text_sha256; final returned evidence includes full text.",
             "git_status": subprocess.check_output(["git", "status", "--porcelain"],
                                                   cwd=ROOT, text=True)}
    write(out / "audit.json", audit)
    write(out / "manifest.json", {name: sha(out / name) for name in ("rows.jsonl", "audit.json")})


def evidence_identity(item: dict, channel: str) -> str:
    if channel == "cases":
        return str(item.get("canonical_case_key") or "")
    if channel == "forms":
        return str(item.get("article_id") or "")
    return law_identity(item.get("article_id") or "")


def metric_bucket() -> dict:
    return {"n": 0, "hit": 0, "all": 0, "recall_sum": 0.0, "rr_sum": 0.0,
            "required_occurrences": 0, "found_occurrences": 0, "available_occurrences": 0}


def add_metric(bucket: dict, targets: list[dict], returned: list[str]) -> None:
    wanted = [item["identity"] for item in targets]
    found = set(wanted) & set(returned)
    bucket["n"] += 1
    bucket["hit"] += bool(found)
    bucket["all"] += set(wanted) <= set(returned)
    bucket["recall_sum"] += len(found) / len(set(wanted))
    first = next((rank for rank, value in enumerate(returned, 1) if value in set(wanted)), None)
    bucket["rr_sum"] += 1 / first if first else 0
    bucket["required_occurrences"] += len(targets)
    bucket["found_occurrences"] += len(found)
    bucket["available_occurrences"] += sum(item["available"] for item in targets)


def finish_metric(bucket: dict) -> dict:
    n = bucket["n"]
    return {**bucket, "hit_rate": bucket["hit"] / n if n else None,
            "all_rate": bucket["all"] / n if n else None,
            "mean_recall": bucket["recall_sum"] / n if n else None,
            "micro_recall": (bucket["found_occurrences"] / bucket["required_occurrences"]
                             if bucket["required_occurrences"] else None),
            "mrr": bucket["rr_sum"] / n if n else None}


def metric_cutoffs(run_name: str, channel: str) -> tuple[int, ...]:
    budget = RUN_BUDGETS[run_name][channel]
    candidates = (1, 2, 3, 5) if channel == "cases" else (1, 3, 5)
    return tuple(value for value in candidates if value <= budget)


def score_rows(rows: list[dict]) -> dict:
    metrics: dict[str, dict] = {}
    complete: dict[str, dict] = {}
    details = []
    for row in rows:
        job = row["job"]
        if not job["score"] and not job.get("diagnostic_score"):
            continue
        for run_name, run in row["runs"].items():
            result = run["result"]
            found_by_channel = {channel: [evidence_identity(item, channel) for item in result.get(channel, [])]
                                for channel in ("laws", "civil_laws", "cases", "forms")}
            channel_detail = {channel: {"targets": targets, "returned": found_by_channel[channel]}
                              for channel, targets in job["targets"].items() if targets}
            all_targets = [item for values in job["targets"].values() for item in values]
            found = set(value for values in found_by_channel.values() for value in values)
            law_targets = job["targets"]["laws"] + job["targets"]["civil_laws"]
            labels = ([job["group"]] if job["score"] else
                      [f"diagnostic:{job['group']}"])
            if job["score"] and job.get("suite") == "holdout30":
                labels.append(f"{job['group']}:{job['track']}")
            for label in labels:
                for channel, targets in job["targets"].items():
                    if not targets:
                        continue
                    for k in metric_cutoffs(run_name, channel):
                        selected = found_by_channel[channel][:k]
                        key = f"{label}:{run_name}:{channel}:@{k}"
                        add_metric(metrics.setdefault(key, metric_bucket()), targets, selected)
                key = f"{label}:{run_name}:all-required"
                bucket = complete.setdefault(key, {
                    "n": 0, "complete": 0, "full_recall_sum": 0.0,
                    "full_required_occurrences": 0, "full_found_occurrences": 0,
                    "law_n": 0, "law_complete": 0, "law_recall_sum": 0.0,
                    "law_required_occurrences": 0, "law_found_occurrences": 0})
                wanted = {item["identity"] for item in all_targets}
                full_found = wanted & found
                bucket["n"] += 1
                bucket["complete"] += wanted <= found
                bucket["full_recall_sum"] += len(full_found) / len(wanted) if wanted else 0.0
                bucket["full_required_occurrences"] += len(all_targets)
                bucket["full_found_occurrences"] += len(full_found)
                if law_targets:
                    law_wanted = {item["identity"] for item in law_targets}
                    law_found = law_wanted & found
                    bucket["law_n"] += 1
                    bucket["law_complete"] += law_wanted <= found
                    bucket["law_recall_sum"] += len(law_found) / len(law_wanted)
                    bucket["law_required_occurrences"] += len(law_targets)
                    bucket["law_found_occurrences"] += len(law_found)
            details.append({"qid": job["qid"], "mode": job["mode"], "group": job["group"],
                            "run": run_name, "channels": channel_detail,
                            "all_complete": {item["identity"] for item in all_targets} <= found})
    metrics = {key: finish_metric(value) for key, value in metrics.items()}
    for value in complete.values():
        value["complete_rate"] = value["complete"] / value["n"] if value["n"] else None
        value["macro_recall"] = value["full_recall_sum"] / value["n"] if value["n"] else None
        value["micro_recall"] = (value["full_found_occurrences"] / value["full_required_occurrences"]
                                 if value["full_required_occurrences"] else None)
        value["law_complete_rate"] = (value["law_complete"] / value["law_n"]
                                      if value["law_n"] else None)
        value["law_macro_recall"] = (value["law_recall_sum"] / value["law_n"]
                                     if value["law_n"] else None)
        value["law_micro_recall"] = (value["law_found_occurrences"] /
                                     value["law_required_occurrences"]
                                     if value["law_required_occurrences"] else None)
    return {"schema": SCHEMA, "channel_metrics": metrics, "complete_metrics": complete,
            "details": details,
            "note": "Separate channel ranks; unavailable required sources remain in denominators."}


def score(capture_dir: Path, out: Path) -> None:
    if out.exists():
        raise ValueError("Use a fresh score path")
    manifest = read(capture_dir / "manifest.json")
    if set(manifest) != {"rows.jsonl", "audit.json"}:
        raise ValueError("Capture manifest file set differs")
    if any(sha(capture_dir / name) != digest for name, digest in manifest.items()):
        raise ValueError("Capture manifest mismatch")
    rows = jsonl(capture_dir / "rows.jsonl")
    audit = read(capture_dir / "audit.json")
    if (audit.get("schema") != SCHEMA or audit.get("queries") != len(rows)
            or not re.fullmatch(r"[0-9a-f]{64}", audit.get("protocol_manifest_sha256", ""))):
        raise ValueError("Capture audit linkage or row count differs")
    write(out, score_rows(rows))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    for name in ("base-data", "case-profile", "case-dev", "case-external",
                 "legacy-case-chunks", "out"):
        prep.add_argument("--" + name, required=True, type=Path)
    cap = sub.add_parser("capture")
    cap.add_argument("--protocol", required=True, type=Path)
    cap.add_argument("--protocol-commit", required=True)
    cap.add_argument("--out", required=True, type=Path)
    scoring = sub.add_parser("score")
    scoring.add_argument("--capture", required=True, type=Path)
    scoring.add_argument("--out", required=True, type=Path)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.action == "prepare":
        prepare(args.base_data, args.case_profile, args.case_dev, args.case_external,
                args.legacy_case_chunks, args.out)
    elif args.action == "capture":
        capture(args.protocol, args.protocol_commit, args.out)
    else:
        score(args.capture, args.out)


if __name__ == "__main__":
    main()
