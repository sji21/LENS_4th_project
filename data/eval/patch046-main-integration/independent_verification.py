"""Read-only independent review; no evaluator scoring helpers or model loading.

Run from repository root after capture, public regression and full tests finish:
python -X utf8 data/eval/patch046-main-integration/independent_verification.py \
    --out data/eval/patch046-main-integration/independent-verification.json
Optional path flags support replay against copied bundles. Only --out is written.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_sha(path):
    value = Path(path).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(value).hexdigest()


def norm(value):
    return "".join(value.split())


def verify(out, previous, public_path, previous_public_path, log_path):
    baseline = Path("data/eval/patch041-rebuilt/capture")
    rows, oldrows = read(out / "rows.json"), read(baseline / "rows.json")
    audit, oldaudit = read(out / "audit.json"), read(baseline / "audit.json")
    report = read(out / "report.json")
    new = {(r["qid"], r["mode"]): r for r in rows}
    old = {(r["qid"], r["mode"]): r for r in oldrows}
    assert len(rows) == len(new) == len(old) == 235 and set(new) == set(old)
    previous_rows = read(previous / "rows.json")
    previous_by_key = {(r["qid"], r["mode"]): r for r in previous_rows}
    assert len(previous_rows) == len(previous_by_key) == 235 and set(new) == set(previous_by_key)
    for key, row in new.items():
        assert {k: v for k, v in row.items() if k != "seconds"} == {
            k: v for k, v in previous_by_key[key].items() if k != "seconds"
        }, key
    limits = {"laws": 5, "civil_laws": 3, "cases": 5, "guides": 2}
    plan = read("data/eval/dev100-v2/diagnostic-plan.json")
    questions = {q["qid"]: q for q in read("data/eval/dev100-v2/questions.json")}
    targets, scores, strata, gained, hit_counts = {}, {}, {}, {}, {}
    for mode, expected in [("question_only", 47), ("context_diagnostic", 48)]:
        counts = [0, 0, 0]
        strata[mode], gained[mode] = {}, []
        for p in plan:
            key = (p["qid"], mode)
            target = {norm(t["article_anchor"]) for t in p["law_targets"]}
            targets[key] = target
            query = questions[p["qid"]]["modes"][mode]["query"]
            assert new[key]["query_sha256"] == hashlib.sha256(query.encode()).hexdigest()
            if not target or p["historical_review_required"]:
                continue
            returned = lambda row: {norm(e["article_id"]) for ch in ("laws", "civil_laws")
                                    for e in row["result"][ch]}
            before, after = target <= returned(old[key]), target <= returned(new[key])
            counts[0] += 1
            counts[1] += before
            counts[2] += after
            bucket = strata[mode].setdefault(str(len(target)), [0, 0, 0])
            bucket[0] += 1
            bucket[1] += before
            bucket[2] += after
            if not before and after:
                gained[mode].append(p["qid"])
            assert not (before and not after), key
        assert counts == [75, 43, expected]
        assert report["groups"][mode]["union_all_required"] == {"hits": expected, "n": 75}
        scores[mode] = dict(zip(("n", "baseline", "candidate"), counts))
        for channel, kind, ks in [("laws", "general", (1, 2, 3, 5)),
                                  ("civil_laws", "civil", (1, 2, 3))]:
            totals, n = {k: 0 for k in ks}, 0
            for p in plan:
                target = {norm(t["article_anchor"]) for t in p["law_targets"]
                          if norm(t["article_anchor"]).startswith("민법-") == (kind == "civil")}
                if p["historical_review_required"] or not target:
                    continue
                n += 1
                values = [norm(e["article_id"]) for e in new[p["qid"], mode]["result"][channel]]
                for k in ks:
                    totals[k] += bool(target & set(values[:k]))
            metric = report["groups"][mode]["channel_metrics"][kind]["all_target_items"]
            assert metric["n"] == n
            assert all(math.isclose(metric["hit@" + str(k)], hits / n) for k, hits in totals.items())
            hit_counts[mode + ":" + kind] = {"n": n, "hits": totals}
    for p in read("data/eval/civil-review2/retrieval-plan.json")["items"]:
        key = (p["id"], "question_context")
        targets[key] = {norm(t) for g in p["required_groups_all_of"] for t in g["article_targets_all_of"]}
        assert new[key]["query_sha256"] == hashlib.sha256(p["input_text"].encode()).hexdigest()
    changes, lost = Counter(), []
    for key, row in new.items():
        assert row["query_sha256"] == old[key]["query_sha256"]
        evidence = lambda r: {norm(e["article_id"]) for ch in ("laws", "civil_laws") for e in r["result"][ch]}
        missing = (evidence(old[key]) & targets[key]) - evidence(row)
        if missing:
            lost.append([key, sorted(missing)])
        for channel, limit in limits.items():
            hits = row["result"][channel]
            assert len(hits) <= limit and len({e["chunk_id"] for e in hits}) == len(hits)
            assert [e["rank"] for e in hits] == list(range(1, len(hits) + 1))
            changes[channel] += hits != old[key]["result"][channel]
        assert row["model_calls"] == old[key]["model_calls"] > 0
    assert not lost and changes == {"laws": 10, "civil_laws": 0, "cases": 0, "guides": 0}
    for folder in (out, baseline, previous):
        manifest = read(folder / "manifest.json")
        assert set(manifest) == {"rows.json", "report.json", "audit.json"}
        assert all(sha(folder / p) == h for p, h in manifest.items())
    assert audit["baseline_manifest_sha256"] == sha(baseline / "manifest.json")
    pairs = audit["before_after"]
    assert all(a == b and a for a, b in pairs.values())
    for category in ("sources", "criteria"):
        assert all(source_sha(p) == h for p, h in pairs[category][0].items())
    for current, previous_key in [("payload", "payload_hashes_after"), ("model", "model_hashes_after"),
                              ("logical_indices", "logical_index_hashes_after")]:
        assert pairs[current][0] == oldaudit[previous_key]
    assert audit["profile"] == oldaudit["profile"]
    assert audit["generation_performed"] is False and audit["channel_limits"] == limits
    assert sum(r["model_calls"] for r in rows) == audit["model_usage"]["actual_kure_calls"] == 375
    gold_manifest_counts = {}
    for dataset in ("dev100-v2", "civil-review2"):
        folder = Path("data/eval") / dataset
        files = read(folder / "manifest.json")["files"]
        entries = files.items() if isinstance(files, dict) else [(e["path"], e["stored_sha256"]) for e in files]
        entries = list(entries)
        assert all(sha(folder / p) == h for p, h in entries)
        gold_manifest_counts[dataset] = len(entries)
    data = Path(audit["data_root"])
    assert all(sha(data / p) == h for p, h in pairs["payload"][0].items())
    chunks = {}
    for name in ("chunks", "civil", "cases", "guides"):
        for line in (data / "chunks" / (name + ".jsonl")).read_text(encoding="utf-8").splitlines():
            if line.strip():
                chunk = json.loads(line)
                chunks[chunk["chunk_id"]] = chunk
    evidence_count = 0
    for row in rows:
        for channel, hits in row["result"].items():
            for e in hits:
                c = chunks[e["chunk_id"]]
                m = c["metadata"]
                assert hashlib.sha256(c["text"].encode()).hexdigest() == e["text_sha256"]
                assert (m.get("article_id") or m.get("case_id") or m.get("guide_id") or c["chunk_id"]) == e["article_id"]
                assert str(m.get("source_url", "")) == e["source_url"] and m["status"] == "current"
                if channel == "laws":
                    assert m["title"] != "민법" and m["doc_type"] in ("law", "decree", "rule")
                elif channel == "civil_laws":
                    assert m["title"] == "민법"
                else:
                    assert m["doc_type"] == {"cases": "case", "guides": "guide"}[channel]
                evidence_count += 1
    public = read(public_path)
    oldpublic = read(previous_public_path)
    assert set(public["results"]) == set(oldpublic["results"])
    public_summary = {}
    for name, after in public["results"].items():
        before = oldpublic["results"][name]
        for key in ("question_gold_sha256", "input_count", "state", "metrics"):
            assert after[key] == before[key], (name, key)
        if after["state"] == "not_scored":
            assert after["missing_gold"] == before["missing_gold"]
            public_summary[name] = {"state": "not_scored", "input_count": after["input_count"]}
            continue
        for key in ("n", "exclusions", "actual_model_calls", "scope", "ranking"):
            assert after[key] == before[key], (name, key)
        aa = {q["qid"]: q for q in after["questions"]}
        bb = {q["qid"]: q for q in before["questions"]}
        assert len(aa) == len(bb) == after["n"] and set(aa) == set(bb)
        for qid, a in aa.items():
            assert {k: v for k, v in a.items() if k != "seconds"} == {k: v for k, v in bb[qid].items() if k != "seconds"}
        public_summary[name] = {"n": after["n"], "actual_model_calls": after["actual_model_calls"],
                                "metrics": after["metrics"], "all_question_fields_except_seconds_equal": True}
    assert public["actual_model_calls"] == oldpublic["actual_model_calls"] == 103
    assert public["data_files_before"] == public["data_files_after"]
    assert public["index_hashes_before"] == public["index_hashes_after"] == oldpublic["index_hashes_after"]
    for p in ("src/retrieval/expanded.py", "src/retrieval/multi_evidence.py"):
        assert public["source_hashes"][p] == source_sha(p)
    historical_dir = Path("data/eval/patch042-gap-analysis")
    historical_manifest = read(historical_dir / "manifest.json")
    historical_hash = historical_manifest["source_sha256"]["src/generation/chain.py"]
    historical_source = Path(__file__).resolve().parent / "historical-sources" / (historical_hash + ".py")
    assert source_sha(historical_source) == historical_hash
    assert all(sha(historical_dir / name) == digest
               for name, digest in historical_manifest["artifacts"].items())
    log = log_path.read_text(encoding="utf-8")
    summaries = [line for line in log.splitlines()
                 if re.search(r"\b\d+ passed\b", line) and re.search(r"\bin [\d.]+s\b", line)]
    assert summaries, "Completed pytest summary is missing"
    summary = summaries[-1]
    assert not re.search(r"\b\d+ (?:failed|errors?|xpassed)\b", summary, re.I), summary
    passed = int(re.search(r"\b(\d+) passed\b", summary)[1])
    skipped_match = re.search(r"\b(\d+) skipped\b", summary)
    subtests_match = re.search(r"\b(\d+) subtests passed\b", summary)
    full_counts = {"passed": passed,
                   "skipped": int(skipped_match[1]) if skipped_match else 0,
                   "subtests_passed": int(subtests_match[1]) if subtests_match else 0,
                   "seconds": float(re.search(r"\bin ([\d.]+)s\b", summary)[1])}
    assert passed > 0
    # Product code is imported only for a synthetic concurrency check.
    from src.retrieval.multi_evidence import TaxLookupSelector
    synthetic = [{"chunk_id": cid, "text": text, "metadata": {"status": status}}
                 for cid, text, status in [("r", "보증금 반환", "current"), ("n", "미납국세 열람 신청", "current"),
                                          ("l", "미납지방세 열람 신청", "current"), ("h", "미납국세 열람", "historical")]]
    ranked = [("r", .4), ("h", .3), ("l", .2), ("n", .1)]
    selector = TaxLookupSelector(synthetic)
    queries = ["미납국세 열람 방법은요?", "미납지방세 열람 방법은요?", "전입신고 방법은요?", "미납국세 열람과 전입신고 방법은요?"]
    expected = [[("n", .1), ("r", .4)], [("l", .2), ("r", .4)], ranked[:2], ranked[:2]]
    original = deepcopy((synthetic, ranked))
    def probe(i):
        assert selector.select(queries[i % 4], ranked, 2, {"status": "current"}) == expected[i % 4]
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(probe, range(400)))
    assert (synthetic, ranked) == original
    return {
        "verified_at": datetime.now(timezone.utc).isoformat(), "status": "passed",
        "method": "Independent raw JSON/set arithmetic; no evaluator scoring helpers; synthetic selector concurrency only",
        "scope": "Frozen article retrieval diagnostic and published regression, not formal OR/paragraph/LLM correctness",
        "script_sha256": sha(__file__), "candidate_manifest": read(out / "manifest.json"),
        "previous_patch046_capture": {"path": previous.as_posix(),
            "manifest_sha256": sha(previous / "manifest.json"), "compared_inputs": 235,
            "all_serialized_row_fields_except_seconds_equal": True},
        "source_hashes": {p: source_sha(p) for p in ("src/retrieval/expanded.py", "src/retrieval/multi_evidence.py")},
        "reviewed_inputs": 235, "scores": scores, "strata_n_baseline_candidate": strata,
        "new_all_hit_items": gained, "new_required_target_loss": lost, "full_evidence_changed_inputs": dict(changes),
        "hit_at_k_counts": hit_counts, "actual_kure_calls": 375, "per_input_call_counts_equal": True,
        "source_hash_count": len(pairs["sources"][0]), "criteria_hash_count": len(pairs["criteria"][0]),
        "gold_manifest_files": gold_manifest_counts, "payload_current_hash_count": len(pairs["payload"][0]),
        "returned_evidence_identity_body_source_status_channel_checks": evidence_count,
        "public_regression_direct_previous_PATCH046_comparison": public_summary,
        "public_previous_artifact": {"path": previous_public_path.as_posix(), "sha256": sha(previous_public_path)},
        "public_actual_kure_calls": 103,
        "historical_gap_replay_inputs": {
            "artifact_manifest_sha256": sha(historical_dir / "manifest.json"),
            "unchanged_artifact_count": len(historical_manifest["artifacts"]),
            "generation_source_normalized_sha256": historical_hash,
            "archived_source_matches_original_manifest": True},
        "synthetic_concurrency": {"workers": 8, "calls": 400, "input_unchanged": True, "mixed_scopes_passed": True},
        "full_suite": {"method": "Directly inspected completed log; process exit code reported by integration agent",
                       "log": log_path.as_posix(), "sha256": sha(log_path),
                       **full_counts, "summary": summary.strip()},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path("data/eval/patch046-main-integration")
    parser.add_argument("--capture", type=Path, default=root / "capture")
    parser.add_argument("--previous-capture", type=Path, default=Path("data/eval/patch046-improvement"))
    parser.add_argument("--public", type=Path, default=root / "public-regression.json")
    parser.add_argument("--previous-public", type=Path, default=Path("data/eval/patch046-public-regression.json"))
    parser.add_argument("--full-tests", type=Path, default=root / "full-tests.log")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path.cwd()))
    result = verify(args.capture, args.previous_capture, args.public, args.previous_public, args.full_tests)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "scores": result["scores"], "out": str(args.out)}, ensure_ascii=False))
