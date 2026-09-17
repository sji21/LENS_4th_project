"""Frozen retrieval regression at actual law budgets 3/5; no generated answers."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path

from scripts import patch041_retrieval_eval as base
from scripts.patch041_public_regression import sources
from scripts.patch041_settings_contract import snapshot_settings
from src.evaluation.baseline import load_dataset, prepare_questions, settings
from src.retrieval.profile import index_hash
from src.retrieval.service import route_law_corpus


def jobs(service, case_dev, case_external):
    result = [dict(j, scope="combined", score=bool(j["targets"]) and not j["historical"]
                   and j["track"] in ("dev100", "required_law")) for j in base.build_jobs()]
    inputs, exclusions = {}, {}
    for group, path, kind, scope in (
        ("legacy_dev", base.ROOT / "data/eval/dev.jsonl", "law", "general"),
        ("legacy_holdout", base.ROOT / "data/eval/holdout.jsonl", "law", "general"),
        ("civil_public", base.ROOT / "data/eval/minbeop_review_holdout_20260901.jsonl", "law", "combined"),
        ("case_dev13", case_dev, "case", "case"),
        ("case_external8", case_external, "case", "case"),
    ):
        rows = load_dataset(path, kind)
        rows = [{**r, "answer_type": "unanswerable"} if r.get("answer_type") == "abstain" else r for r in rows]
        selected, exclusions[group] = prepare_questions(rows, list(service._chunks.values()), kind)
        inputs[group] = {"path": str(path), "sha256": base.sha(path)}
        result.extend(dict(qid=r["qid"], mode="question_only", group=group, track=group,
                           query=r["question"], query_sha256=base.text_sha(r["question"]),
                           targets=r["gold"], historical=False, scope=scope, score=True) for r in selected)
    if len(result) != 313:
        raise ValueError("Expected frozen 235 and published 78 inputs")
    return result, inputs, exclusions


def capture(data, out, case_dev, case_external):
    if out.exists():
        raise ValueError("Use a fresh output directory")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      ANONYMIZED_TELEMETRY="False")
    from huggingface_hub.constants import HF_HUB_CACHE
    frozen = base.read(base.ROOT / "data/eval/patch041-rebuilt/capture/audit.json")
    model_root = Path(HF_HUB_CACHE) / "models--nlpai-lab--KURE-v1"
    model_hashes = lambda: {p: base.sha(model_root / p) for p in frozen["model_hashes_after"]}
    service, profile, logical = base.prepare_product_service(data)
    plan, inputs, exclusions = jobs(service, case_dev, case_external)
    snapshot = lambda: {
        "sources": sources(), "criteria": base.criteria_hashes(), "data": base.data_hashes(data),
        "payload": base._payload_hashes(data), "model": model_hashes(),
        "logical_indices": [index_hash(r) for r in (service.dense, service.civil_dense)],
        "settings": snapshot_settings(settings(service)),
        "tax_selection": dict(service.selection_config),
        "companion_selection": dict(getattr(service, "companion_selection_config", {})),
        "intent_selection": dict(getattr(service, "intent_selection_config", {})),
        "public_inputs": {g: base.sha(Path(i["path"])) for g, i in inputs.items()},
    }
    before = snapshot()
    for key, expected in (("model", "model_hashes_after"), ("payload", "payload_hashes_after"),
                          ("logical_indices", "logical_index_hashes_after")):
        if before[key] != frozen[expected]:
            raise ValueError("Frozen " + key + " differs")
    rows = []
    with base.KureCallCounter(service) as counter:
        for number, job in enumerate(plan, 1):
            results, calls = {}, counter.actual_calls
            for k in (3, 5):
                found = service.search(job["query"], k_law=k, k_civil=3, k_case=5, k_guide=2)
                results[str(k)] = base._serialize(service, found)
            rows.append({**job, "results": results, "model_calls": counter.actual_calls - calls})
            if number % 25 == 0:
                print(f"{number}/313 paired K3/K5 searches", flush=True)
        question = next(j["query"] for j in plan if j["qid"] == "dev-001")
        where = route_law_corpus(question, service.corpora[0]).where()
        pool, members = service._context_law.search_with_member_hits(question, 20, where)
        def trace(hits):
            return [{"rank": i, "chunk_id": cid, "score": score,
                     "article_id": service._chunks[cid]["metadata"].get("article_id")}
                    for i, (cid, score) in enumerate(hits, 1)]
        detail = {"qid": "dev-001", "query": question, "where": where,
                  "fused": trace(pool), "members": {
                      name: [{"rank": i, "chunk_id": cid,
                              "article_id": service._chunks[cid]["metadata"].get("article_id")}
                             for i, cid in enumerate(ids, 1)] for name, ids in members.items()}}
    after = snapshot()
    if before != after:
        raise ValueError("Source, criteria, data, model or settings changed during capture")
    audit = {"schema": "patch051-paired-budgets-v1", "created_at": datetime.now(timezone.utc).isoformat(),
             "head": base._git("rev-parse", "HEAD"), "status": base._git("status", "--porcelain"),
             "generation_performed": False, "profile": profile, "device": str(service.dense.backend.delegate._model.device),
             "before": before, "after": after, "model_usage": counter.counts,
             "public_inputs": inputs, "exclusions": exclusions,
             "budgets": {"laws": [3, 5], "civil_laws": 3, "cases": 5, "guides": 2}}
    out.mkdir(parents=True)
    for name, value in (("rows.json", rows), ("audit.json", audit), ("target-trace.json", detail)):
        base.write(out / name, value)
    base.write(out / "manifest.json", {n: base.sha(out / n) for n in ("rows.json", "audit.json", "target-trace.json")})
    print("Capture complete", flush=True)


def retrieved(row, k):
    result = row["results"][str(k)]
    if row["scope"] == "case":
        return [base.norm(e["article_id"]) for e in result["cases"]]
    channels = ["laws"] + (["civil_laws"] if row["scope"] == "combined" else [])
    return [base.norm(e["article_id"]) for c in channels for e in result[c]]


def compare(before, after, out):
    old, new = [base.read(p / "rows.json") for p in (before, after)]
    audits = [base.read(p / "audit.json") for p in (before, after)]
    for p, audit in zip((before, after), audits):
        if any(base.sha(p / n) != h for n, h in base.read(p / "manifest.json").items()):
            raise ValueError("Manifest mismatch")
        if audit["before"] != audit["after"]:
            raise ValueError("Unstable capture")
    for key in ("criteria", "payload", "model", "logical_indices", "settings", "public_inputs", "tax_selection"):
        if audits[0]["before"][key] != audits[1]["before"][key]:
            raise ValueError("Comparison drift: " + key)
    if len(old) != 313 or len(new) != 313:
        raise ValueError("Expected 313 rows each")
    groups, changes, losses = {}, [], []
    for a, b in zip(old, new):
        if {k: v for k, v in a.items() if k not in ("results", "model_calls")} != {k: v for k, v in b.items() if k not in ("results", "model_calls")}:
            raise ValueError("Input/gold drift")
        targets = set(map(base.norm, a["targets"]))
        for k in (3, 5):
            av, bv = retrieved(a, k), retrieved(b, k)
            if a["score"]:
                label = (f'law{k}+civil3' if a["scope"] == "combined" else
                         f'law{k}+case5' if a["scope"] == "case" else f'law{k}')
                bucket = groups.setdefault(f'{a["group"]}:{label}',
                                           {"n": 0, "before_any": 0, "after_any": 0, "before_all": 0, "after_all": 0})
                bucket["n"] += 1
                for prefix, vals in (("before", av), ("after", bv)):
                    bucket[prefix + "_any"] += bool(targets & set(vals))
                    bucket[prefix + "_all"] += targets <= set(vals)
                lost = sorted((targets & set(av)) - set(bv))
                if lost:
                    losses.append({"qid": a["qid"], "mode": a["mode"], "k": k, "lost": lost})
            if a["results"][str(k)] != b["results"][str(k)]:
                changes.append({"qid": a["qid"], "mode": a["mode"], "k": k, "targets": sorted(targets),
                                "before": av, "after": bv, "before_all": targets <= set(av), "after_all": targets <= set(bv)})
    base.write(out, {"scope": "Published development/regression, not independent holdout or LLM quality",
                     "groups": groups, "changes": changes, "required_target_losses": losses})
    print({"changed": len(changes), "losses": len(losses), "groups": groups}, flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("capture", "compare"))
    for name in ("data", "out", "case-dev", "case-external", "before", "after"):
        p.add_argument("--" + name, type=Path)
    a = p.parse_args()
    if a.action == "capture":
        capture(a.data, a.out, a.case_dev, a.case_external)
    else:
        compare(a.before, a.after, a.out)
