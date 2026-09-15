"""Measure published regression sets on one rebuilt production index."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from scripts.dev100_diagnostics.repair_public_regression import compare_reports
from scripts.patch041_retrieval_eval import prepare_product_service
from src.evaluation.baseline import evaluate, fingerprint, load_dataset, prepare_questions, settings
from src.retrieval.profile import CHUNKS, FILES, INDEXES, PROFILE, index_hash
from src.retrieval.retriever import load_chunks

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def identities(rows, kind):
    gold = "gold_articles" if kind == "law" else "gold_case_ids"
    return sorted((r["qid"], r["question"], sorted(set(r[gold]))) for r in rows)


def files(data):
    return {name: fingerprint(data / name)["sha256"] for name in sorted(FILES | {PROFILE})}


def sources():
    paths = [p for scope in ("src", "scripts") for p in (ROOT / scope).rglob("*.py")]
    paths += [ROOT / "setup_data.py", ROOT / "requirements.txt"]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(
        p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in sorted(paths)}


def run(data, out, case_dev, case_external, previous_public, previous_case_dev,
        previous_case_external, case_source):
    if out.exists():
        raise ValueError("Output already exists")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", ANONYMIZED_TELEMETRY="False")
    from setup_data import prepare_model
    before = files(data)
    code = sources()
    paths = tuple(data / p for p in CHUNKS)
    chunks = [c for p in paths for c in load_chunks(p)]
    service, profile, indices = prepare_product_service(data)
    backend = service.dense.backend.delegate
    original = backend.embed
    calls = 0

    def counted(texts):
        nonlocal calls
        calls += 1
        return original(texts)

    backend.embed = counted
    public_baseline = read(previous_public)
    old_public = public_baseline["results"]
    old_cases = {"case_dev13": read(previous_case_dev), "case_external8": read(previous_case_external)}
    source = read(case_source)
    source_dev = next(iter(source["datasets"]["dev_mapped_13"]["retrievers"].values()))["results"]
    dev_rows = load_dataset(case_dev, "case")
    if identities(dev_rows, "case") != identities(source_dev, "case"):
        raise ValueError("Case Dev differs from historical source questions/gold")
    cases = [
        ("dev", ROOT / "data/eval/dev.jsonl", "law", "general"),
        ("holdout", ROOT / "data/eval/holdout.jsonl", "law", "general"),
        ("civil_published_regression", ROOT / "data/eval/minbeop_review_holdout_20260901.jsonl", "law", "combined"),
        ("case_dev13", case_dev, "case", "general"),
        ("case_external8", case_external, "case", "general"),
        ("case_legacy20", ROOT / "data/eval/case_holdout_current_20.jsonl", "case", "general"),
    ]
    results = {}
    for name, path, kind, scope in cases:
        rows = load_dataset(path, kind)
        raw = fingerprint(path)
        info = {"input": raw, "question_gold_sha256": digest(identities(rows, kind)),
                "input_count": len(rows)}
        if name in old_public:
            prior = old_public[name]
            if raw["sha256"] != prior["input"]["sha256"]:
                relative = path.relative_to(ROOT).as_posix()
                prior_bytes = subprocess.check_output(
                    ["git", "show", public_baseline["code_commit"] + ":" + relative], cwd=ROOT)
                prior_rows = [json.loads(line) for line in prior_bytes.decode("utf-8-sig").splitlines() if line.strip()]
                if (hashlib.sha256(prior_bytes).hexdigest() != prior["input"]["sha256"]
                        or prior_rows != rows):
                    raise ValueError("Public input differs from frozen baseline: " + name)
                info["baseline_input_comparison"] = {
                    "raw_bytes_equal": False, "all_json_rows_equal": True,
                    "historical_sha256": prior["input"]["sha256"],
                    "historical_commit": public_baseline["code_commit"]}
            else:
                info["baseline_input_comparison"] = {"raw_bytes_equal": True}
            old = prior if name == "civil_published_regression" else prior["after"]
        elif name in old_cases:
            old = old_cases[name]
            if raw["sha256"] != old["inputs"][-1]["sha256"]:
                raise ValueError("Case input differs from frozen baseline: " + name)
        else:
            old = None
        rows = [{**r, "answer_type": "unanswerable"} if r.get("answer_type") == "abstain" else r
                for r in rows]
        gold_key, id_key = (("gold_articles", "article_id") if kind == "law" else ("gold_case_ids", "case_id"))
        available = {c["metadata"].get(id_key) for c in chunks}
        missing = {r["qid"]: sorted(set(r[gold_key]) - available) for r in rows
                   if set(r[gold_key]) - available}
        if missing:
            if name != "case_legacy20":
                raise ValueError("Missing expected regression gold: " + name)
            results[name] = {**info, "state": "not_scored", "reason": "gold absent from current corpus",
                             "missing_gold": missing, "metrics": None}
            continue
        selected, excluded = prepare_questions(rows, chunks, kind)
        start_calls = calls
        result = evaluate(service, selected, chunks, kind, law_scope=scope)
        results[name] = {**info, **result, "exclusions": excluded, "actual_model_calls": calls - start_calls,
                         "state": "measured"}
        if calls == start_calls:
            raise ValueError("No actual KURE calls: " + name)
        if old is not None:
            comparison = compare_reports(result, old)
            # Legacy reports may omit scope; keep their numbers as reference only.
            comparison["previous_metrics_reference_only"] = old["metrics"]
            results[name]["comparison"] = comparison
        print(json.dumps({"set": name, "n": result["n"], "metrics": result["metrics"]}), flush=True)
    after = files(data)
    end_indices = [index_hash(r) for r in (service.dense, service.civil_dense)]
    if before != after or indices != end_indices or code != sources():
        raise ValueError("Data, indices or execution sources changed during measurement")
    prepare_model(check=True)
    backend.embed = original
    import torch
    payload = {"purpose": "published regression, not independent evaluation or LLM assessment",
               "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
               "source_hashes": code, "data_files_before": before, "data_files_after": after,
               "index_hashes_before": indices, "index_hashes_after": end_indices,
               "settings": settings(service), "actual_model_calls": calls,
               "torch": torch.__version__, "device": str(backend._model.device),
               "baseline_files": [fingerprint(p) for p in (previous_public, previous_case_dev,
                    previous_case_external, case_source)], "results": results}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data-root", "out", "case-dev", "case-external", "previous-public",
                 "previous-case-dev", "previous-case-external", "case-source"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = vars(parser.parse_args())
    args["data"] = args.pop("data_root")
    run(**args)


if __name__ == "__main__":
    main()
