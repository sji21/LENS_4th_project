"""Capture and replay the current product retriever on the reviewed 235 inputs.

This is a retrieval-only evaluator.  It never calls the answer-generation LLM.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import time

from scripts.dev100_v2.report import load_dataset
from scripts.patch015_baseline import norm
from scripts.patch023_report import diagnose, summarize
from scripts.patch041_settings_contract import snapshot_settings, validate_settings
from src.evaluation.baseline import SEARCH_K, settings
from src.retrieval.expanded import POLICY
from src.retrieval.profile import CHUNKS, FILES, INDEXES, PROFILE, index_hash


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_ROWS = ROOT / "data/eval/patch027-final-test/rows.json"
REFERENCE_AUDIT = ROOT / "data/eval/patch027-context-tuning/audit.json"
QUERY_SOURCE = ROOT / "data/eval/patch015-baseline/capture/results.json"
ARTIFACTS = ("audit.json", "rows.json", "report.json")
CHANNEL_LIMITS = {"laws": 5, "civil_laws": 3, "cases": 5, "guides": 2}
DEV_MODES = ("question_only", "context_diagnostic")
GROUP_COUNTS = {"question_only": 100, "context_diagnostic": 100,
                "required_law": 29, "scope_provisional": 1, "diagnostic_only": 5}


def sha(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_sha(path: Path) -> str:
    """Hash source text consistently across LF and CRLF checkouts."""
    content = Path(path).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(content).hexdigest()


def text_sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path: Path, value) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")


def build_jobs(root: Path = ROOT) -> list[dict]:
    """Return the fixed ordered input/target plan and reject identity drift."""
    plan, _ = load_dataset(root / "data/eval/dev100-v2")
    questions = read(root / "data/eval/dev100-v2/questions.json")
    question_by_id = {q["qid"]: q for q in questions}
    if len(plan) != 100 or len(question_by_id) != 100:
        raise ValueError("Expected 100 unique DEV100 v2 items")
    jobs = []
    for item in plan:
        qid = item["qid"]
        targets = [target["article_anchor"] for target in item["law_targets"]]
        for mode in DEV_MODES:
            query = question_by_id[qid]["modes"][mode]["query"]
            jobs.append({"qid": qid, "mode": mode, "group": mode, "track": "dev100",
                         "query": query, "query_sha256": text_sha(query),
                         "targets": targets,
                         "historical": bool(item.get("historical_review_required", False))})

    civil_root = root / "data/eval/civil-review2"
    civil_manifest = read(civil_root / "manifest.json")
    for item in civil_manifest["files"]:
        if sha(civil_root / item["path"]) != item["stored_sha256"]:
            raise ValueError("Civil review bundle hash mismatch: " + item["path"])
    civil = read(civil_root / "retrieval-plan.json")["items"]
    for item in civil:
        targets = sorted({target for group in item["required_groups_all_of"]
                          for target in group["article_targets_all_of"]})
        query = item["input_text"]
        jobs.append({"qid": item["id"], "mode": "question_context",
                     "group": item["track"], "track": item["track"],
                     "query": query, "query_sha256": text_sha(query), "targets": targets,
                     "historical": bool(item.get("historical_review_required", False))})

    prior = read(root / "data/eval/patch015-baseline/capture/results.json")
    expected = {(row["qid"], row["mode"]): row["query_sha256"] for row in prior}
    keys = [(job["qid"], job["mode"]) for job in jobs]
    if len(jobs) != 235 or len(set(keys)) != 235 or set(keys) != set(expected):
        raise ValueError("Expected the same 235 unique reviewed inputs")
    if any(job["query_sha256"] != expected[(job["qid"], job["mode"])] for job in jobs):
        raise ValueError("Reviewed query hash changed")
    return jobs


def load_reference_rows(root: Path = ROOT) -> list[dict]:
    """Convert the PATCH-027 finalist to stable article identities."""
    anchors = read(root / REFERENCE_AUDIT.relative_to(ROOT))["anchors"]
    rows = []
    for source in read(root / REFERENCE_ROWS.relative_to(ROOT)):
        result, evidence = {}, {}
        for channel, values in source["record_lookup"].items():
            if channel in ("laws", "civil_laws"):
                result[channel] = [norm(anchors[item["chunk_id"]]) for item in values]
                for item, article in zip(values, result[channel]):
                    evidence[article] = {key: item[key] for key in
                                         ("chunk_id", "citation", "source_url", "text_sha256")}
            else:
                result[channel] = [item["chunk_id"] for item in values]
        rows.append({"qid": source["qid"], "mode": source["mode"],
                     "query_sha256": source["query_sha256"], "result": result,
                     "evidence": evidence})
    keys = [(row["qid"], row["mode"]) for row in rows]
    if len(rows) != 235 or len(set(keys)) != 235:
        raise ValueError("Finalist reference must contain 235 rows")
    return rows


def _article_values(row: dict, channel: str) -> list[str]:
    return [norm(item["article_id"]) for item in row["result"][channel]]


def _validate_rows(rows: list[dict], jobs: list[dict]) -> list[dict]:
    keyed = {(row.get("qid"), row.get("mode")): row for row in rows}
    keys = [(row.get("qid"), row.get("mode")) for row in rows]
    expected = [(job["qid"], job["mode"]) for job in jobs]
    if len(rows) != 235 or len(keyed) != 235 or set(keys) != set(expected):
        raise ValueError("Missing, duplicate, or unknown reviewed input")
    ordered = [keyed[key] for key in expected]
    for row, job in zip(ordered, jobs):
        if row.get("query_sha256") != job["query_sha256"] or row.get("group") != job["group"]:
            raise ValueError("Query hash or group differs from the reviewed plan")
        result = row.get("result")
        if not isinstance(result, dict) or set(result) != set(CHANNEL_LIMITS):
            raise ValueError("Missing retrieval channel")
        for channel, limit in CHANNEL_LIMITS.items():
            values = result[channel]
            if not isinstance(values, list) or len(values) > limit:
                raise ValueError("Invalid channel budget")
            ids = [item.get("chunk_id") for item in values]
            articles = [norm(str(item.get("article_id", ""))) for item in values]
            if (len(ids) != len(set(ids))
                    or (channel in ("laws", "civil_laws")
                        and len(articles) != len(set(articles)))
                    or any(item.get("rank") != rank
                           for rank, item in enumerate(values, 1))):
                raise ValueError("Duplicate evidence or invalid rank")
            required = {"chunk_id", "article_id", "citation", "source_url", "text_sha256", "rank"}
            if any(not required <= set(item) for item in values):
                raise ValueError("Incomplete evidence identity")
        laws, civil = _article_values(row, "laws"), _article_values(row, "civil_laws")
        if any(value.startswith("민법-") for value in laws):
            raise ValueError("Civil article leaked into general law channel")
        if any(not value.startswith("민법-") for value in civil):
            raise ValueError("Non-civil article leaked into civil channel")
    return ordered


def analyze(rows: list[dict], *, available_articles, reference_rows=None,
            root: Path = ROOT) -> dict:
    """Pure scoring and finalist comparison over stable article identifiers."""
    jobs = build_jobs(root)
    rows = _validate_rows(rows, jobs)
    available = {norm(value) for value in available_articles}
    details = []
    for row, job in zip(rows, jobs):
        item = diagnose(job["targets"], available, _article_values(row, "laws"),
                        _article_values(row, "civil_laws"), job["historical"])
        item.update(qid=job["qid"], mode=job["mode"], track=job["track"])
        details.append(item)
    groups = {mode: summarize([item for item in details if item["track"] == "dev100"
                               and item["mode"] == mode]) for mode in DEV_MODES}
    for track in ("required_law", "scope_provisional", "diagnostic_only"):
        groups[track] = summarize([item for item in details if item["track"] == track])
    if {name: value["n"] for name, value in groups.items()} != GROUP_COUNTS:
        raise ValueError("Evaluation denominator drift")
    for mode in DEV_MODES:
        group = groups[mode]
        if (group["union_all_required"]["n"] != 75
                or group["channel_metrics"]["general"]["all_target_items"]["n"] != 47
                or group["channel_metrics"]["civil"]["all_target_items"]["n"] != 32):
            raise ValueError("DEV target denominator drift")
    required = groups["required_law"]
    if required["channel_metrics"]["civil"]["all_target_items"]["n"] != 29:
        raise ValueError("CIV35 primary denominator drift")

    reference_rows = load_reference_rows(root) if reference_rows is None else reference_rows
    reference_keys = [(r.get("qid"), r.get("mode")) for r in reference_rows]
    if len(reference_rows) != 235 or len(set(reference_keys)) != 235:
        raise ValueError("Missing or duplicate finalist comparison input")
    reference = {(r["qid"], r["mode"]): r for r in reference_rows}
    lost, gained, top3_lost, ranking_changes, all_channel_ranking_changes = [], [], [], [], []
    identity_changes = []
    transitions = {mode: {"gained": [], "lost": [], "net": 0} for mode in DEV_MODES}
    for row, job in zip(rows, jobs):
        old = reference.get((job["qid"], job["mode"]))
        if old is None or old.get("query_sha256") != job["query_sha256"]:
            raise ValueError("Finalist comparison input mismatch")
        targets = {norm(value) for value in job["targets"]}
        old_law = list(map(norm, old["result"]["laws"] + old["result"]["civil_laws"]))
        new_law = _article_values(row, "laws") + _article_values(row, "civil_laws")
        missing = sorted((set(old_law) & targets) - set(new_law))
        added = sorted((set(new_law) & targets) - set(old_law))
        if missing:
            lost.append({"qid": job["qid"], "mode": job["mode"], "lost": missing})
        if added:
            gained.append({"qid": job["qid"], "mode": job["mode"], "gained": added})
        old_top3, new_top3 = set(map(norm, old["result"]["laws"][:3])), set(_article_values(row, "laws")[:3])
        missing_top3 = sorted((old_top3 & targets) - new_top3)
        if missing_top3:
            top3_lost.append({"qid": job["qid"], "mode": job["mode"], "lost": missing_top3})
        for channel in CHANNEL_LIMITS:
            if channel in ("laws", "civil_laws"):
                before = list(map(norm, old["result"][channel]))
                after = _article_values(row, channel)
                identity_field = "article_id"
            else:
                before = list(old["result"][channel])
                after = [item["chunk_id"] for item in row["result"][channel]]
                identity_field = "chunk_id"
            if before != after:
                all_channel_ranking_changes.append({
                    "qid": job["qid"], "mode": job["mode"], "channel": channel,
                    "identity_field": identity_field, "before": before, "after": after,
                })
            if channel in ("laws", "civil_laws") and before != after:
                ranking_changes.append({"qid": job["qid"], "mode": job["mode"],
                                        "channel": channel, "before": before, "after": after})
        old_complete = bool(targets) and targets <= set(old_law)
        new_complete = bool(targets) and targets <= set(new_law) and targets <= available
        if job["track"] == "dev100" and not job["historical"]:
            if not old_complete and new_complete:
                transitions[job["mode"]]["gained"].append(job["qid"])
            if old_complete and not new_complete:
                transitions[job["mode"]]["lost"].append(job["qid"])
        old_evidence = old.get("evidence", {})
        for channel in ("laws", "civil_laws"):
            for current in row["result"][channel]:
                article = norm(current["article_id"])
                prior = old_evidence.get(article)
                if prior and any(current[key] != prior[key]
                                 for key in ("citation", "source_url", "text_sha256")):
                    identity_changes.append({"qid": job["qid"], "mode": job["mode"],
                                             "article_id": article,
                                             "before": prior, "after": {key: current[key] for key in
                                                                         ("chunk_id", "citation", "source_url", "text_sha256")}})
    for value in transitions.values():
        value["gained"] = sorted(set(value["gained"]))
        value["lost"] = sorted(set(value["lost"]))
        value["net"] = len(value["gained"]) - len(value["lost"])
    unique_identity = {json.dumps(value, ensure_ascii=False, sort_keys=True): value
                       for value in identity_changes}
    return {
        "unit": "fixed reviewed article identifiers; not paragraph or answer accuracy",
        "groups": groups,
        "comparison": {"reference": "PATCH-027 record_lookup finalist",
                       "lost_required_inputs": lost, "gained_required_inputs": gained,
                       "general_top3_loss_inputs": top3_lost,
                       "all_required_transitions": transitions,
                       "article_ranking_changes": ranking_changes,
                       "all_channel_ranking_changes": all_channel_ranking_changes,
                       "chunk_id_is_not_article_identity": True},
        "evidence_identity_changes": list(unique_identity.values()),
        "details": details,
    }


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).rstrip("\r\n")


def product_source_hashes() -> dict[str, str]:
    names = _git("ls-files", "src/retrieval", "setup_data.py", "requirements.txt").splitlines()
    return {name: source_sha(ROOT / name) for name in sorted(names)}


def data_hashes(data_root: Path) -> dict[str, str]:
    paths = [data_root / name for name in FILES | {PROFILE}]
    for name in INDEXES:
        paths.extend(path for path in (data_root / name).rglob("*") if path.is_file())
    if any(not path.is_file() for path in paths):
        raise ValueError("Incomplete retrieval data tree")
    return {path.relative_to(data_root).as_posix(): sha(path) for path in sorted(set(paths))}


def criteria_hashes() -> dict[str, str]:
    roots = (ROOT / "data/eval/dev100-v2", ROOT / "data/eval/civil-review2")
    paths = [path for root in roots for path in root.rglob("*") if path.is_file()]
    paths.append(QUERY_SOURCE)
    return {path.relative_to(ROOT).as_posix(): source_sha(path) for path in sorted(paths)}


def evaluator_source_hashes() -> dict[str, str]:
    names = (
        "scripts/patch041_retrieval_eval.py", "scripts/dev100_v2/report.py",
        "scripts/patch015_baseline.py", "scripts/patch023_report.py",
        "scripts/patch041_settings_contract.py",
        "src/evaluation/baseline.py", "src/retrieval/profile.py",
    )
    return {name: source_sha(ROOT / name) for name in names}


def reference_hashes() -> dict[str, str]:
    paths = (QUERY_SOURCE, REFERENCE_ROWS, REFERENCE_AUDIT)
    return {path.relative_to(ROOT).as_posix(): sha(path) for path in paths}


def _payload_hashes(data_root: Path) -> dict[str, str]:
    return {name: sha(data_root / name) for name in sorted(FILES | {PROFILE})}


def _serialize(service, result) -> dict:
    serialized = {}
    for channel in CHANNEL_LIMITS:
        serialized[channel] = []
        for evidence in getattr(result, channel):
            metadata = service._chunks[evidence.chunk_id]["metadata"]
            identity = (metadata.get("article_id") or metadata.get("case_id")
                        or metadata.get("guide_id") or evidence.chunk_id)
            serialized[channel].append({
                "chunk_id": evidence.chunk_id, "article_id": identity,
                "citation": evidence.citation, "source_url": evidence.source_url,
                "text_sha256": text_sha(evidence.text), "rank": evidence.rank,
            })
    return serialized


def prepare_product_service(data_root: Path):
    """Validate the pinned model/profile and construct the real product factory."""
    from setup_data import prepare_model
    from src.retrieval.service import RetrievalService

    data_root = Path(data_root).resolve()
    prepare_model(check=True)
    profile = read(data_root / PROFILE)
    if profile.get("policy") != POLICY:
        raise ValueError("The expanded-laws-record-v1 profile is required")
    paths = tuple(data_root / name for name in CHUNKS)
    service = RetrievalService.from_index(
        chunk_paths=paths, index_path=data_root / INDEXES[0],
        civil_index_path=data_root / INDEXES[1], model=profile["model"],
    )
    if getattr(service, "profile_name", None) != POLICY:
        raise ValueError("Production factory did not activate the expanded profile")
    if service.dense.backend.delegate is not service.civil_dense.backend.delegate:
        raise ValueError("General and civil dense search do not share the KURE backend")
    logical = [index_hash(retriever) for retriever in (service.dense, service.civil_dense)]
    if logical != profile["index_hashes"]:
        raise ValueError("Logical index hashes differ from the profile")
    return service, profile, logical


class KureCallCounter:
    """Count actual shared-model calls and requests through both dense backends."""

    def __init__(self, service):
        self.delegate = service.dense.backend.delegate
        self.general_backend = service.dense.backend
        self.civil_backend = service.civil_dense.backend
        self.counts = {"actual_kure_calls": 0, "general_backend_requests": 0,
                       "civil_backend_requests": 0}

    def __enter__(self):
        self.original_delegate = self.delegate.embed
        self.original_general = self.general_backend.embed
        self.original_civil = self.civil_backend.embed

        def measured_delegate(texts):
            self.counts["actual_kure_calls"] += 1
            return self.original_delegate(texts)

        def measured_general(texts):
            self.counts["general_backend_requests"] += 1
            return self.original_general(texts)

        def measured_civil(texts):
            self.counts["civil_backend_requests"] += 1
            return self.original_civil(texts)

        self.delegate.embed = measured_delegate
        self.general_backend.embed = measured_general
        self.civil_backend.embed = measured_civil
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.delegate.embed = self.original_delegate
        self.general_backend.embed = self.original_general
        self.civil_backend.embed = self.original_civil

    @property
    def actual_calls(self):
        return self.counts["actual_kure_calls"]


def capture(data_root: Path, out: Path) -> dict:
    """Run the production factory and save a self-auditing retrieval bundle."""
    from huggingface_hub.constants import HF_HUB_CACHE
    from src.retrieval.retriever import load_chunks

    data_root, out = data_root.resolve(), out.resolve()
    if out.exists():
        raise ValueError("Use a new output directory")
    criteria_before = criteria_hashes()
    reference_before = reference_hashes()
    evaluator_before = evaluator_source_hashes()
    jobs = build_jobs()
    status = _git("status", "--porcelain")
    product_names = set(_git("ls-files", "src/retrieval", "setup_data.py", "requirements.txt").splitlines())
    dirty_product = [line for line in status.splitlines()
                     if line[3:].replace("\\", "/") in product_names]
    if dirty_product:
        raise ValueError("Product retrieval source is dirty")
    data_preopen, payload_before = data_hashes(data_root), _payload_hashes(data_root)
    source_before = product_source_hashes()
    model_audit = read(ROOT / "data/eval/patch027-full/capture/audit.json")["model_files"]
    model_root = Path(HF_HUB_CACHE).expanduser().resolve() / "models--nlpai-lab--KURE-v1"
    service, profile, logical_before = prepare_product_service(data_root)
    settings_before = snapshot_settings(settings(service))
    model_hashes_before = {name: sha(model_root / name) for name in model_audit}
    data_before = data_hashes(data_root)
    paths = tuple(data_root / name for name in CHUNKS)
    chunks = [chunk for path in paths for chunk in load_chunks(path)]
    available = sorted({norm(chunk["metadata"]["article_id"]) for chunk in chunks
                        if chunk["metadata"].get("article_id")})

    rows = []
    with KureCallCounter(service) as counter:
        for number, job in enumerate(jobs, 1):
            before_calls = counter.actual_calls
            started = time.perf_counter()
            result = service.search(job["query"], **SEARCH_K)
            rows.append({"qid": job["qid"], "mode": job["mode"], "group": job["group"],
                         "query_sha256": job["query_sha256"],
                         "seconds": time.perf_counter() - started,
                         "model_calls": counter.actual_calls - before_calls,
                         "result": _serialize(service, result)})
            if number % 25 == 0:
                print(f"{number}/235 product retrieval inputs", flush=True)
    counts = counter.counts
    if (counts["actual_kure_calls"] <= 0 or counts["general_backend_requests"] <= 0
            or counts["civil_backend_requests"] <= 0
            or any(row["model_calls"] <= 0 for row in rows)):
        raise ValueError("Both dense channels must make measured KURE calls")
    report = analyze(rows, available_articles=available)
    settings_after = snapshot_settings(settings(service))
    logical_after = [index_hash(retriever) for retriever in (service.dense, service.civil_dense)]
    data_after, source_after = data_hashes(data_root), product_source_hashes()
    payload_after = _payload_hashes(data_root)
    model_hashes_after = {name: sha(model_root / name) for name in model_audit}
    criteria_after = criteria_hashes()
    reference_after = reference_hashes()
    evaluator_after = evaluator_source_hashes()
    if (data_before != data_after or payload_before != payload_after
            or source_before != source_after or logical_before != logical_after
            or model_hashes_before != model_hashes_after
            or criteria_before != criteria_after or reference_before != reference_after
            or evaluator_before != evaluator_after):
        raise ValueError("Code or retrieval data changed during capture")
    packages = {name: importlib.metadata.version(name) for name in
                ("torch", "transformers", "sentence-transformers", "chromadb", "numpy")}
    delegate = service.dense.backend.delegate
    audit = {
        "schema": "patch041-retrieval-eval-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git("rev-parse", "HEAD"), "branch": _git("branch", "--show-current"),
        "git_status": status, "product_source_clean": True,
        "harness_sha256": sha(Path(__file__)), "python": platform.python_version(),
        "platform": platform.platform(), "generation_performed": False,
        "packages": packages, "embedding_device": str(delegate._model.device),
        "data_root": str(data_root), "profile": profile,
        "settings_before": settings_before, "settings_after": settings_after,
        "settings": settings_after,
        "criteria_hashes_before": criteria_before, "criteria_hashes_after": criteria_after,
        "reference_hashes_before": reference_before, "reference_hashes_after": reference_after,
        "evaluator_source_hashes_before": evaluator_before,
        "evaluator_source_hashes_after": evaluator_after,
        "product_source_hashes_before": source_before,
        "product_source_hashes_after": source_after, "data_hashes_before": data_before,
        "data_hashes_after": data_after, "data_hashes_preopen": data_preopen,
        "index_opening_changes": sorted(name for name in set(data_preopen) | set(data_before)
                                        if data_preopen.get(name) != data_before.get(name)),
        "payload_hashes_before": payload_before, "payload_hashes_after": payload_after,
        "logical_index_hashes_before": logical_before,
        "logical_index_hashes_after": logical_after,
        "model_hashes_before": model_hashes_before, "model_hashes_after": model_hashes_after,
        "available_articles": available, "inputs": len(rows), "model_usage": counts,
        "channel_limits": CHANNEL_LIMITS,
    }
    out.mkdir(parents=True)
    write(out / "rows.json", rows)
    write(out / "report.json", report)
    write(out / "audit.json", audit)
    write(out / "manifest.json", {name: sha(out / name) for name in ARTIFACTS})
    return check(out)


def check(run: Path) -> dict:
    """Verify artifact integrity and recompute every score without model calls."""
    run = Path(run).resolve()
    manifest = read(run / "manifest.json")
    if set(manifest) != set(ARTIFACTS) or any(sha(run / name) != digest
                                               for name, digest in manifest.items()):
        raise ValueError("Artifact manifest mismatch")
    audit, rows, saved_report = read(run / "audit.json"), read(run / "rows.json"), read(run / "report.json")
    data_before, data_after = audit.get("data_hashes_before"), audit.get("data_hashes_after")
    source_before = audit.get("product_source_hashes_before")
    source_after = audit.get("product_source_hashes_after")
    logical_before = audit.get("logical_index_hashes_before")
    logical_after = audit.get("logical_index_hashes_after")
    criteria_before = audit.get("criteria_hashes_before")
    criteria_after = audit.get("criteria_hashes_after")
    reference_before = audit.get("reference_hashes_before")
    reference_after = audit.get("reference_hashes_after")
    evaluator_before = audit.get("evaluator_source_hashes_before")
    evaluator_after = audit.get("evaluator_source_hashes_after")
    validate_settings(audit.get("settings_before"))
    validate_settings(audit.get("settings_after"))
    validate_settings(audit.get("settings"))
    if (audit.get("schema") != "patch041-retrieval-eval-v1" or audit.get("inputs") != 235
            or audit.get("generation_performed") is not False
            or audit.get("product_source_clean") is not True
            or audit.get("channel_limits") != CHANNEL_LIMITS
            or audit.get("profile", {}).get("policy") != POLICY
            or not isinstance(data_before, dict) or not data_before or data_before != data_after
            or not isinstance(source_before, dict) or not source_before or source_before != source_after
            or not isinstance(logical_before, list) or len(logical_before) != 2
            or logical_before != logical_after
            or not isinstance(audit.get("payload_hashes_before"), dict)
            or not audit["payload_hashes_before"]
            or audit["payload_hashes_before"] != audit.get("payload_hashes_after")
            or not isinstance(audit.get("model_hashes_before"), dict)
            or not audit["model_hashes_before"]
            or audit["model_hashes_before"] != audit.get("model_hashes_after")
            or not isinstance(criteria_before, dict) or not criteria_before
            or criteria_before != criteria_after
            or not isinstance(reference_before, dict) or not reference_before
            or reference_before != reference_after
            or not isinstance(evaluator_before, dict) or not evaluator_before
            or evaluator_before != evaluator_after):
        raise ValueError("Invalid capture audit")
    usage = audit.get("model_usage", {})
    if (usage.get("actual_kure_calls", 0) <= 0 or usage.get("general_backend_requests", 0) <= 0
            or usage.get("civil_backend_requests", 0) <= 0
            or any(type(row.get("model_calls")) is not int or row["model_calls"] <= 0 for row in rows)
            or usage["actual_kure_calls"] != sum(row["model_calls"] for row in rows)):
        raise ValueError("Invalid KURE call audit")
    if criteria_after != criteria_hashes():
        raise ValueError("Reviewed evaluation inputs changed")
    if reference_after != reference_hashes():
        raise ValueError("Finalist reference changed")
    if evaluator_after != evaluator_source_hashes():
        raise ValueError("Evaluator source changed")
    report = analyze(rows, available_articles=audit.get("available_articles", []))
    if report != saved_report:
        raise ValueError("Saved report does not replay")
    return {"inputs": len(rows), "groups": report["groups"],
            "comparison": report["comparison"], "model_usage": usage}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("--data-root", type=Path, required=True)
    capture_parser.add_argument("--out", type=Path, required=True)
    check_parser = commands.add_parser("check")
    check_parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args(argv)
    result = capture(args.data_root, args.out) if args.action == "capture" else check(args.run)
    print(json.dumps({"inputs": result["inputs"], "model_usage": result["model_usage"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
