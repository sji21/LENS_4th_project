"""Final development acceptance test for adding the reviewed law corpus."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import statistics
import subprocess
import time

from scripts.patch027_paths import ROOT, read, sha, write
from scripts.patch025_ranking import close, index_digest
from scripts.patch027_expand import score
from scripts.patch027_context_live import (
    CHANNELS, ContextRetrievalService, _serialize, evidence_identity,
    execution_spec, validate_execution_spec, validate_live_row,
)
from scripts.patch027_context_tuning import BUNDLE as VERIFIED, check as check_verified, fuse, rank_changes
from scripts.patch027_final_policy import RecordLookupService, final_law_concepts
from scripts.patch027_loss_analysis import align, key

BUNDLE = ROOT / "data/eval/patch027-final-test"
PLAN = "docs/patch027-final-test-plan.md"
LABELS = ("operating", "verified_context", "record_lookup")
FILES = {"audit.json", "rows.json", "catalog.json", "report.json"}
DEPENDENCIES = (
    "data/eval/patch027-full/capture/audit.json",
    "data/eval/patch027-full/capture/before.json",
    "data/eval/patch015-baseline/capture/results.json",
    "data/eval/patch024-expansion/report.json",
    "data/eval/patch027-context-tuning/manifest.json",
)
SEARCH_K = {"k_law": 5, "k_case": 5, "k_guide": 2, "k_civil": 3}


def plain(rows, label, anchors):
    return [{"qid": r["qid"], "mode": r["mode"],
             **{ch: [anchors[e["chunk_id"]] if ch in ("laws", "civil_laws") else e["chunk_id"]
                     for e in r[label][ch]] for ch in CHANNELS}} for r in rows]


def transitions(before, after):
    result = {}
    after = align(after, before)
    for mode in ("question_only", "context_diagnostic"):
        gained, lost = [], []
        for old, new in zip(before, after):
            if old["mode"] != mode:
                continue
            a, b = old["all_required_law5_civil3"], new["all_required_law5_civil3"]
            if a is False and b is True:
                gained.append(old["qid"])
            if a is True and b is False:
                lost.append(old["qid"])
        result[mode] = {"gained": gained, "lost": lost, "net": len(gained) - len(lost)}
    return result


def quantitative_checks(result, baseline):
    groups = result["groups"]
    checks = {}
    for mode in ("question_only", "context_diagnostic"):
        current = groups[mode]["union_all_required"]
        prior = baseline["groups"][mode]["union_all_required"]
        checks[mode + "_net_gain"] = current["n"] == prior["n"] == 75 and current["hits"] > prior["hits"]
        checks[mode + "_consumed_not_lower"] = (
            result["consumed_groups"][mode]["union_all_required"]["n"]
            == baseline["consumed_groups"][mode]["union_all_required"]["n"] == 75
            and
            result["consumed_groups"][mode]["union_all_required"]["hits"]
            >= baseline["consumed_groups"][mode]["union_all_required"]["hits"]
        )
    required = groups["required_law"]
    checks["civil35_complete"] = (required["union_all_required"]["n"] == 29
                                   and required["union_all_required"]["hits"] >= 28)
    civil = required["channel_metrics"]["civil"]["all_target_items"]
    checks["civil35_hit3"] = civil["n"] == 29 and civil["hit@3"] == 1
    checks["new_civil_coverage"] = len(result["new_required_civil"]) == 9
    return checks


def analyze(rows, anchors):
    full = read(ROOT / DEPENDENCIES[0])
    base_rows = plain(rows, "operating", anchors)
    available = full["available_after"]
    results = {}
    for label in LABELS:
        actual = plain(rows, label, anchors)
        inventory = full["available_before"] if label == "operating" else available
        assessed = score(actual, inventory, base_rows)
        consumed = score([{**r, "laws": r["laws"][:3]} for r in actual], inventory, base_rows)
        ranks = rank_changes(base_rows, actual, assessed["details"])
        costs = [r[label + "_cost"] for r in rows]
        durations = sorted(c["seconds"] for c in costs)
        new_ids = set(available) - set(full["available_before"])
        new_civil = sorted({a for r, d in zip(actual, assessed["details"])
                            for a in r["civil_laws"] if a in new_ids and a in d["targets"]})
        results[label] = {
            **assessed, "consumed_groups": consumed["groups"],
            "general_top3_losses": [r for r in ranks["laws"]["3"] if r["lost"]],
            "rank_changes": ranks, "new_required_civil": new_civil,
            "cost": {"model_calls": sum(c["model_calls"] for c in costs),
                     "embed_requests": sum(c["embed_requests"] for c in costs),
                     "median_seconds": statistics.median(durations),
                     "mean_seconds": statistics.mean(durations),
                     "p95_seconds": durations[int((len(durations) - 1) * .95)]},
        }
    eligible = []
    for label in LABELS[1:]:
        r = results[label]
        r["transitions"] = transitions(results["operating"]["details"], r["details"])
        r["checks"] = quantitative_checks(r, results["operating"])
        if all(r["checks"].values()):
            eligible.append(label)
    def preference(label):
        r = results[label]
        total = sum(r["groups"][m]["union_all_required"]["hits"] for m in ("question_only", "context_diagnostic"))
        return (-total, len(r["losses"]), r["cost"]["model_calls"], LABELS.index(label))
    selected = min(eligible, key=preference) if eligible else None
    changed = [{"qid": r["qid"], "mode": r["mode"],
                "before": [e["chunk_id"] for e in r["verified_context"]["laws"]],
                "after": [e["chunk_id"] for e in r["record_lookup"]["laws"]]}
               for r in rows if r["verified_context"]["laws"] != r["record_lookup"]["laws"]]
    return {"results": results, "selected": selected, "record_lookup_changes": changed,
            "scope": "development quantitative recommendation; operational rollout and legal severity review are separate"}


def validate_rows(rows, catalog, anchors):
    queries = read(ROOT / DEPENDENCIES[2])
    ordered = align(rows, queries)
    if len(ordered) != 235:
        raise ValueError("Expected 235 inputs")
    old = align(read(ROOT / DEPENDENCIES[1]), queries)
    verified = align(read(VERIFIED / "live-verification.json")["rows"], queries)
    inverse = {a: c for c, a in anchors.items()}
    for row, query, prior, known in zip(ordered, queries, old, verified):
        if row["query_sha256"] != query["query_sha256"]:
            raise ValueError("Query identity changed")
        if row["final_concepts"] != final_law_concepts(query["query"]):
            raise ValueError("Expansion differs from frozen policy")
        expected = {ch: [inverse[a] for a in prior[ch]] if ch in ("laws", "civil_laws") else prior[ch]
                    for ch in CHANNELS}
        validate_live_row(row["operating"], expected, catalog)
        if row["verified_context"] != known["candidate"]:
            raise ValueError("Verified control drift")
        for label in LABELS[1:]:
            ids = {ch: [e["chunk_id"] for e in row[label][ch]] for ch in CHANNELS}
            members = row[label + "_general_members"]
            ids["laws"] = fuse(members["bm25_context"], members["dense_context"])[:5]
            ids["civil_laws"] = [e["chunk_id"] for e in known["candidate"]["civil_laws"]]
            validate_live_row(row[label], ids, catalog)
            if any(row[label][ch] != row["operating"][ch] for ch in ("cases", "guides")):
                raise ValueError("Case/guide evidence changed")
        for label in LABELS:
            cost = row[label + "_cost"]
            if not (0 < cost["model_calls"] <= cost["embed_requests"] and cost["seconds"] > 0):
                raise ValueError("Invalid measured cost")
    return ordered


def capture(out, candidate):
    from src.retrieval.dense import ChromaRetriever, SentenceTransformerEmbedding
    from src.retrieval.index import clean_metadata
    from src.retrieval.retriever import load_chunks
    from src.retrieval.service import RetrievalService, _to_evidence

    if out.exists() or not out.resolve().is_relative_to(ROOT / "tmp"):
        raise ValueError("Use a fresh workspace tmp output")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise ValueError("Commit final test source before capture")
    check_verified()
    spec = execution_spec()
    dependencies = {p: sha(ROOT / p) for p in DEPENDENCIES}
    full = read(ROOT / DEPENDENCIES[0])
    source_files = {ROOT / p: digest for p, digest in full["data_hashes"].items()}
    source_files.update({candidate / p: digest for p, digest in full["candidate_files"].items()})
    def check_files():
        if any(sha(path) != digest for path, digest in source_files.items()):
            raise ValueError("Source corpus changed")
    check_files()
    model_root = Path.home() / ".cache/huggingface/hub/models--nlpai-lab--KURE-v1"
    if any(sha(model_root / p) != digest for p, digest in full["model_files"].items()):
        raise ValueError("Embedding model changed")
    backend = SentenceTransformerEmbedding("nlpai-lab/KURE-v1")
    data_roots = [ROOT / "data", candidate]
    indexes, chunks = [], []
    catalog = {}
    for data in data_roots:
        current_chunks = [c for name in ("chunks", "cases", "guides")
                          for c in load_chunks(data / f"chunks/{name}.jsonl")]
        mapping = {c["chunk_id"]: c for c in current_chunks}
        pair = [ChromaRetriever(backend, data / "index" / name)
                for name in ("chroma_kurev1_1024", "chroma_civil_kurev1_1024")]
        seen = set()
        for retriever in pair:
            content = retriever.collection.get(include=["documents", "metadatas"])
            for cid, body, meta in zip(content["ids"], content["documents"], content["metadatas"]):
                if cid in seen or cid not in mapping or body != mapping[cid]["text"] or meta != clean_metadata(mapping[cid]["metadata"]):
                    raise ValueError("Index/source mismatch")
                seen.add(cid)
        if seen != set(mapping):
            raise ValueError("Incomplete indexes")
        for cid, chunk in mapping.items():
            identity = evidence_identity(_to_evidence(1, chunk, 0))
            if cid in catalog and catalog[cid] != identity:
                raise ValueError("Existing evidence identity changed")
            catalog[cid] = identity
        indexes.extend(pair)
        chunks.append(current_chunks)
    hashes = [index_digest(r) for r in indexes]
    if hashes != full["operating_index_hashes"] + full["candidate_index_hashes"]:
        raise ValueError("Index vectors changed")
    anchors = read(VERIFIED / "audit.json")["anchors"]
    services = {
        "operating": RetrievalService(chunks[0], indexes[0], civil_dense=indexes[1]),
        "verified_context": ContextRetrievalService(chunks[1], indexes[2], indexes[3]),
        "record_lookup": RecordLookupService(chunks[1], indexes[2], indexes[3]),
    }
    original = backend.embed
    original(["임대차 근거 검색 준비"])
    rows = []
    for i, query in enumerate(read(ROOT / DEPENDENCIES[2])):
        row = {k: query[k] for k in ("qid", "mode", "query_sha256")}
        row["final_concepts"] = final_law_concepts(query["query"])
        order = LABELS[i % 3:] + LABELS[:i % 3]
        for label in order:
            cache, counts = {}, {"embed_requests": 0, "model_calls": 0}
            def measured(texts):
                counts["embed_requests"] += 1
                cache_key = tuple(texts)
                if cache_key not in cache:
                    counts["model_calls"] += 1
                    cache[cache_key] = original(texts)
                return cache[cache_key]
            backend.embed = measured
            start = time.perf_counter()
            result = services[label].search(query["query"], **SEARCH_K)
            row[label + "_cost"] = {**counts, "seconds": time.perf_counter() - start}
            row[label] = _serialize(result)
            if label != "operating":
                # Single-threaded observation only; never used to route another request.
                row[label + "_general_members"] = services[label]._context_law._last
        rows.append(row)
        if (i + 1) % 25 == 0:
            print(f"{i + 1}/235 operating/control/final searches", flush=True)
    backend.embed = original
    check_files()
    if [index_digest(r) for r in indexes] != hashes or execution_spec() != spec:
        raise ValueError("Index or execution code changed during capture")
    rows = validate_rows(rows, catalog, anchors)
    out.mkdir(parents=True)
    write(out / "rows.json", rows)
    write(out / "catalog.json", catalog)
    write(out / "audit.json", {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "clean": True, "inputs": 235, "labels": LABELS, "search_k": SEARCH_K,
        "execution_spec": spec, "plan_sha256": sha(ROOT / PLAN), "dependencies": dependencies,
        "anchors": anchors, "index_hashes": hashes, "data_unchanged": True,
        "cost_scope": "warmed model, rotated service order, per-input/service cache, single run",
    })
    write(out / "report.json", analyze(rows, anchors))
    write(out / "manifest.json", {p: sha(out / p) for p in sorted(FILES)})
    return check(out)


def check(out=BUNDLE):
    manifest = read(out / "manifest.json")
    if set(manifest) != FILES or any(sha(out / p) != digest for p, digest in manifest.items()):
        raise ValueError("Final bundle manifest mismatch")
    audit = read(out / "audit.json")
    if audit["clean"] is not True or audit["labels"] != list(LABELS) or audit["search_k"] != SEARCH_K or audit["inputs"] != 235:
        raise ValueError("Final execution settings changed")
    validate_execution_spec(audit["execution_spec"], audit["commit"])
    plan = subprocess.check_output(["git", "show", audit["commit"] + ":" + PLAN], cwd=ROOT).replace(b"\r\n", b"\n")
    if audit["plan_sha256"] not in {hashlib.sha256(plan).hexdigest(), hashlib.sha256(plan.replace(b"\n", b"\r\n")).hexdigest()}:
        raise ValueError("Acceptance plan differs from capture commit")
    if audit["dependencies"] != {p: sha(ROOT / p) for p in DEPENDENCIES}:
        raise ValueError("Final dependencies changed")
    check_verified()
    base_audit = read(ROOT / DEPENDENCIES[0])
    if (audit["index_hashes"] != base_audit["operating_index_hashes"] + base_audit["candidate_index_hashes"]
            or audit["anchors"] != read(VERIFIED / "audit.json")["anchors"]
            or audit["data_unchanged"] is not True):
        raise ValueError("Final corpus identity mismatch")
    catalog = read(out / "catalog.json")
    if catalog != read(VERIFIED / "evidence-catalog.json"):
        raise ValueError("Final evidence catalog differs from verified source")
    rows = validate_rows(read(out / "rows.json"), catalog, audit["anchors"])
    result = analyze(rows, audit["anchors"])
    if not close(result, read(out / "report.json")):
        raise ValueError("Final report does not replay")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args()
    result = capture(args.out, args.candidate) if args.out and args.candidate else check()
    print("selected:", result["selected"])
    for label, value in result["results"].items():
        print(label, "complete:", *[value["groups"][m]["union_all_required"] for m in ("question_only", "context_diagnostic")],
              "losses:", len(value["losses"]), "calls:", value["cost"]["model_calls"])
