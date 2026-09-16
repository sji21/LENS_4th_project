"""Replay PATCH-051 artifact checks using only Python's standard library.

Run from any directory with --data-root pointing to the frozen corpus. This
does not import the evaluator/retriever, load models, or make network calls.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def norm(value):
    return "".join(value.split())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(data_root):
    chunks = {}
    for name in ("chunks", "cases", "guides"):
        for line in (data_root / "chunks" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            chunk = json.loads(line)
            require(chunk["chunk_id"] not in chunks, "duplicate corpus chunk")
            chunks[chunk["chunk_id"]] = chunk
    captures, counts = {}, {}
    for stage in ("before", "after"):
        directory = HERE / stage
        manifest = read(directory / "manifest.json")
        require(set(manifest) == {"rows.json", "audit.json", "target-trace.json"}, "manifest members")
        require(all(sha(directory / name) == digest for name, digest in manifest.items()), "artifact hash mismatch")
        audit, rows = read(directory / "audit.json"), read(directory / "rows.json")
        require(audit["before"] == audit["after"], "capture changed during execution")
        require(not audit["generation_performed"], "unexpected generation capture")
        require(len(rows) == 313, "expected 313 rows")
        require(len({(r["track"], r["qid"], r["mode"]) for r in rows}) == 313, "duplicate input")
        count = Counter()
        for row in rows:
            require(hashlib.sha256(row["query"].encode()).hexdigest() == row["query_sha256"], "query hash")
            require(set(row["results"]) == {"3", "5"}, "paired budgets missing")
            require(row["results"]["3"]["laws"] == row["results"]["5"]["laws"][:3], "K3/K5 law prefix differs")
            for k, result in row["results"].items():
                limits = {"laws": int(k), "civil_laws": 3, "cases": 5, "guides": 2}
                require(set(result) == set(limits), "channel missing")
                for channel, values in result.items():
                    require(len(values) <= limits[channel], "budget exceeded")
                    require(len({e["chunk_id"] for e in values}) == len(values), "duplicate evidence")
                    require([e["rank"] for e in values] == list(range(1, len(values) + 1)), "ranks not sequential")
                    for evidence in values:
                        chunk = chunks[evidence["chunk_id"]]
                        meta = chunk["metadata"]
                        identity = meta.get("article_id") or meta.get("case_id") or meta.get("guide_id") or chunk["chunk_id"]
                        require(identity == evidence["article_id"], "identity mismatch")
                        require(meta.get("source_url", "") == evidence["source_url"], "source URL mismatch")
                        require(meta["status"] == "current", "non-current evidence")
                        require(hashlib.sha256(chunk["text"].encode()).hexdigest() == evidence["text_sha256"], "body hash mismatch")
                        count[channel] += 1
        captures[stage] = {"audit": audit, "rows": rows, "trace": read(directory / "target-trace.json")}
        counts[stage] = dict(count)

    old, new = captures["before"], captures["after"]
    stable = ("criteria", "payload", "model", "logical_indices", "settings", "public_inputs", "tax_selection")
    for field in stable:
        require(old["audit"]["before"][field] == new["audit"]["before"][field], "cross-run drift: " + field)
    for name, digest in new["audit"]["before"]["payload"].items():
        require(sha(data_root / name) == digest, "frozen corpus payload changed: " + name)
    for name, digest in new["audit"]["before"]["criteria"].items():
        text_bytes = (ROOT / name).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        require(hashlib.sha256(text_bytes).hexdigest() == digest, "frozen criteria changed: " + name)
    require(old["trace"] == new["trace"], "raw candidate ranking changed")
    require(old["audit"]["model_usage"] == new["audit"]["model_usage"], "model usage changed")
    sources_before = old["audit"]["before"]["sources"]
    sources_after = new["audit"]["before"]["sources"]
    changed_sources = sorted(name for name in sources_before.keys() | sources_after.keys()
                             if sources_before.get(name) != sources_after.get(name))
    require(changed_sources == ["src/retrieval/companion_evidence.py", "src/retrieval/expanded.py"], "unexpected source changes")
    for name, digest in sources_after.items():
        require(hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == digest,
                "source changed since after capture: " + name)

    groups, changes, losses = {}, [], []
    channel_changes = Counter({channel: 0 for channel in ("laws", "civil_laws", "cases", "guides")})
    for before, after in zip(old["rows"], new["rows"]):
        identity = lambda row: {key: value for key, value in row.items() if key not in ("results", "model_calls")}
        require(identity(before) == identity(after), "question or gold drift")
        require(before["model_calls"] == after["model_calls"], "per-input model usage changed")
        target = {norm(value) for value in before["targets"]}
        for k in (3, 5):
            a, b = before["results"][str(k)], after["results"][str(k)]
            require(a["laws"][:1] == b["laws"][:1], "original first evidence changed")
            changed = [channel for channel in a if a[channel] != b[channel]]
            for channel in changed:
                channel_changes[channel] += 1
            if changed:
                require(changed == ["laws"], "non-law channel changed")
                changes.append({"qid": before["qid"], "mode": before["mode"], "law_k": k,
                                "channels": changed, "before_laws": [e["article_id"] for e in a["laws"]],
                                "after_laws": [e["article_id"] for e in b["laws"]]})
            if not before["score"]:
                continue
            require(bool(target), "empty scored gold")
            scope = before["scope"]
            channels = ["cases"] if scope == "case" else ["laws", "civil_laws"] if scope == "combined" else ["laws"]
            suffix = f"law{k}+case5" if scope == "case" else f"law{k}+civil3" if scope == "combined" else f"law{k}"
            bucket = groups.setdefault(before["group"] + ":" + suffix,
                                       {"n": 0, "before_any": 0, "after_any": 0, "before_all": 0, "after_all": 0})
            bucket["n"] += 1
            found = [{norm(e["article_id"]) for channel in channels for e in result[channel]} for result in (a, b)]
            for label, values in zip(("before", "after"), found):
                bucket[label + "_any"] += bool(target & values)
                bucket[label + "_all"] += target <= values
            lost = sorted((target & found[0]) - found[1])
            if lost:
                losses.append({"qid": before["qid"], "mode": before["mode"], "k": k, "lost": lost})
    comparison = read(HERE / "comparison.json")
    require(groups == comparison["groups"], "independent metric mismatch")
    require(losses == comparison["required_target_losses"] == [], "required target loss")
    require({(r["qid"], r["mode"], r["law_k"]) for r in changes}
            == {(r["qid"], r["mode"], r["k"]) for r in comparison["changes"]}, "changed-input mismatch")

    target_row = next(row for row in new["rows"] if row["qid"] == "dev-001")
    core = []
    for article, marker in (("주택임대차보호법-제3조", "①"), ("주택임대차보호법-제3조의2", "②")):
        entries = [chunk for chunk in chunks.values() if chunk["metadata"].get("article_id") == article]
        require(len(entries) == 1, "expected current single article chunk")
        chunk = entries[0]
        paragraph = chunk["text"].split(marker, 1)[1].split("②" if marker == "①" else "③", 1)[0].strip()
        if marker == "①":
            require("주택의 인도" in paragraph and "주민등록" in paragraph and "다음 날" in paragraph, "opposition body incomplete")
        else:
            require("확정일자" in paragraph and "우선하여 보증금을 변제" in paragraph, "priority body incomplete")
        for k in ("3", "5"):
            require(any(e["chunk_id"] == chunk["chunk_id"] for e in target_row["results"][k]["laws"]), "core article missing")
        core.append({"article_id": article, "chunk_id": chunk["chunk_id"], "text_chars": len(chunk["text"]),
                     "text_sha256": hashlib.sha256(chunk["text"].encode()).hexdigest(), "required_paragraph": marker,
                     "paragraph_sha256": hashlib.sha256(paragraph.encode()).hexdigest()})
    return {"schema": "patch051-independent-verification-v1", "verified_at": datetime.now(timezone.utc).isoformat(),
            "method": "Standalone stdlib replay; no evaluator imports, model or network calls",
            "data_root": str(data_root), "capture_rows_each": 313, "manifest_and_execution_stability": True,
            "source_changes": changed_sources, "cross_run_unchanged_fields": list(stable),
            "all_evidence_body_identity_source_status_and_budget_checks": counts,
            "model_usage_each": new["audit"]["model_usage"], "raw_candidate_trace_unchanged": True,
            "k3_equals_k5_law_prefix_rows_each": 313, "first_law_preserved_at_both_budgets_rows": 313,
            "channel_change_counts": dict(channel_changes), "changes": changes, "groups": groups,
            "required_target_losses": losses, "target_core_body_checks": core,
            "limits": ["Article coverage is distinct from paragraph/model-answer correctness.",
                       "HTTP payload delivery is covered separately by test_patch051_generation_delivery.py.",
                       "Published evaluation inputs are regression data, not independent held-out quality evidence."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="Optional fresh report path; stored evidence is read-only by default")
    args = parser.parse_args()
    report = verify(args.data_root.resolve())
    if args.out:
        with args.out.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.out) if args.out else None, "channel_changes": report["channel_change_counts"],
                      "losses": len(report["required_target_losses"])}, ensure_ascii=False))
