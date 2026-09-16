"""Explain frozen retrieval losses and test one selection-only counterfactual.

Gold is used only after selection. This does not change the frozen policy,
production retriever, corpus, or acceptance criteria.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.patch027_paths import ROOT, read, write, sha
from scripts.patch027_expand import score
from scripts.patch027_context_tuning import (
    BUNDLE as BASE, check as check_base, civil_select, fuse, rank_changes,
    _validated_reference_graph,
)

BUNDLE = ROOT / "data/eval/patch027-loss-analysis"
SOURCES = (
    "data/eval/patch027-context-tuning/manifest.json",
    "data/eval/patch027-full/capture/before.json",
    "data/eval/patch027-full/capture/after.json",
    "data/eval/patch015-baseline/capture/results.json",
    "data/eval/patch024-expansion/report.json",
    "data/eval/patch027-full/capture/audit.json",
)


def key(row):
    return row["qid"], row["mode"]


def position(values, value):
    return values.index(value) + 1 if value in values else None


def align(rows, reference):
    """Match input identities explicitly instead of relying on capture order."""
    indexed = {key(row): row for row in rows}
    reference_keys = [key(row) for row in reference]
    if len(indexed) != len(rows) or len(set(reference_keys)) != len(reference_keys):
        raise ValueError("Duplicate input identity")
    if set(indexed) != set(reference_keys):
        raise ValueError("Input identities differ")
    return [indexed[k] for k in reference_keys]


def select_seedless_rrf(row, anchors, graph):
    """Keep topic seeds and reference pairs; omit the seedless third-slot override."""
    selected, references = civil_select(row, "context_reference", anchors, graph)
    if row["civil_seed"] or references:
        return selected
    return fuse(row["civil"]["bm25_context"], row["civil"]["dense_context"])[:3]


def summarize(rows, prior, current, available):
    assessed = score(rows, available, prior)
    changes = rank_changes(prior, rows, assessed["details"])
    return {
        "groups": assessed["groups"], "losses_vs_operating": assessed["losses"],
        "losses_vs_current": score(rows, available, current)["losses"],
        "general_top3_losses": [r for r in changes["laws"]["3"] if r["lost"]],
    }


def analyze():
    baseline_result = check_base()["joint"]["context_both+context_reference"]
    prior = read(ROOT / SOURCES[1])
    untuned = read(ROOT / SOURCES[2])
    queries = {key(r): r for r in read(ROOT / SOURCES[3])}
    traces = read(BASE / "traces.json")
    audit = read(BASE / "audit.json")
    anchors = audit["anchors"]
    inverse = {a: c for c, a in anchors.items()}
    graph = _validated_reference_graph(audit)
    live = read(BASE / "live-verification.json")["rows"]
    current = [{"qid": r["qid"], "mode": r["mode"],
                **{ch: [anchors[e["chunk_id"]] for e in r["candidate"][ch]]
                   for ch in ("laws", "civil_laws")},
                **{ch: [e["chunk_id"] for e in r["candidate"][ch]] for ch in ("cases", "guides")}}
               for r in live]
    if len(current) != 235:
        raise ValueError("Expected all 235 frozen inputs")
    traces = align(traces, current)
    prior = align(prior, current)
    untuned = align(untuned, current)
    align(list(queries.values()), current)
    full_audit = read(ROOT / SOURCES[5])
    available = full_audit["available_after"]
    assessed = score(current, available, prior)
    details = {key(r): r for r in assessed["details"]}
    losses = {key(r): r["lost"] for r in baseline_result["losses"]}
    top3 = {key(r): r["lost"] for r in baseline_result["rank_changes"]["laws"]["3"] if r["lost"]}
    maps = [{key(r): r for r in rows} for rows in (prior, untuned, current, traces)]
    items = []
    for item_key in sorted(losses.keys() | top3.keys()):
        old, full, now, trace = [mapping[item_key] for mapping in maps]
        targets = details[item_key]["targets"]
        missing = sorted(set(targets) - set(now["laws"] + now["civil_laws"]))
        targets_detail = []
        for target in sorted(set(losses.get(item_key, []) + top3.get(item_key, []))):
            ch = "civil" if target.startswith("민법-") else "general"
            output_ch = "civil_laws" if ch == "civil" else "laws"
            cid = inverse[target]
            pooled = fuse(trace[ch]["bm25_context"], trace[ch]["dense_context"])
            targets_detail.append({
                "target": target, "channel": ch,
                "operating_rank": position(old[output_ch], target),
                "untuned_rank": position(full[output_ch], target),
                "final_rank": position(now[output_ch], target),
                "member_ranks": {name: position(ids, cid) for name, ids in trace[ch].items()},
                "rrf_rank": position(pooled, cid),
                "candidate_present": cid in pooled,
                "selected_outside_limit": cid in pooled[:3 if ch == "civil" else 5]
                and target not in now[output_ch],
            })
        complete = lambda r: bool(targets) and set(targets) <= set(r["laws"] + r["civil_laws"])
        items.append({
            "qid": item_key[0], "mode": item_key[1], "query": queries[item_key]["query"],
            "targets": targets, "returned_losses": losses.get(item_key, []),
            "top3_losses": top3.get(item_key, []), "missing_targets": missing,
            "complete_operating": complete(old), "complete_untuned": complete(full),
            "complete_current": complete(now), "expansion": trace["expansion"],
            "operating": old, "untuned": full, "current": now,
            "target_diagnostics": targets_detail,
            "added_vs_operating": {ch: [a for a in now[ch] if a not in old[ch]]
                                   for ch in ("laws", "civil_laws")},
        })
    alternative = [{**row, "civil_laws": [anchors[c] for c in select_seedless_rrf(trace, anchors, graph)]}
                   for row, trace in zip(current, traces)]
    changed = [{"qid": row["qid"], "mode": row["mode"], "before": old["civil_laws"],
                "after": row["civil_laws"],
                "gained_targets": sorted(set(row["civil_laws"]) & set(details[key(row)]["targets"]) - set(old["civil_laws"])),
                "lost_targets": sorted(set(old["civil_laws"]) & set(details[key(row)]["targets"]) - set(row["civil_laws"]))}
               for old, row in zip(current, alternative)
               if old["civil_laws"] != row["civil_laws"]]
    new_civil = {a for a in available if a.startswith("민법-")} - set(full_audit["available_before"])
    new_hits = sorted({a for r in alternative for a in r["civil_laws"]
                       if a in new_civil and a in details[key(r)]["targets"]})
    return {
        "scope": "frozen loss analysis and one offline counterfactual; no adoption or legal regrading",
        "source_hashes": {p: sha(ROOT / p) for p in SOURCES},
        "counts": {
            "returned_loss_inputs": len(losses), "top3_loss_inputs": len(top3),
            "overlap_inputs": len(losses.keys() & top3.keys()), "union_inputs": len(items),
            "top3_only_inputs": len(top3.keys() - losses.keys()),
            "complete_to_incomplete": sum(r["complete_operating"] and not r["complete_current"] for r in items),
            "all_lost_targets_in_candidate_union": all(d["candidate_present"] for r in items for d in r["target_diagnostics"]),
            "returned_losses_already_absent_untuned": sum(d["target"] in r["returned_losses"] and d["untuned_rank"] is None
                                                         for r in items for d in r["target_diagnostics"]),
        },
        "items": items,
        "paired_modes": [
            {"qid": row["qid"], "mode": row["mode"], "query": queries[key(row)]["query"],
             "targets": details[key(row)]["targets"], "category": details[key(row)]["category"],
             "laws": row["laws"], "civil_laws": row["civil_laws"]}
            for row in current if row["qid"] in {item["qid"] for item in items}
        ],
        "current": summarize(current, prior, current, available),
        "counterfactual": {
            "name": "seedless_rrf", "changed_inputs": changed,
            "new_required_civil_articles": new_hits,
            **summarize(alternative, prior, current, available),
        },
    }


def save(out):
    out.mkdir(parents=True, exist_ok=False)
    result = analyze()
    write(out / "analysis.json", result)
    write(out / "manifest.json", {"analysis.json": sha(out / "analysis.json")})
    return result


def check(out=BUNDLE):
    from scripts.patch025_ranking import close
    manifest = read(out / "manifest.json")
    if set(manifest) != {"analysis.json"} or sha(out / "analysis.json") != manifest["analysis.json"]:
        raise ValueError("Loss analysis bundle changed")
    result = analyze()
    if not close(result, read(out / "analysis.json")):
        raise ValueError("Loss analysis does not replay")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    result = save(args.out) if args.out else check()
    print(result["counts"])
    cf = result["counterfactual"]
    print("counterfactual", cf["name"], "changes", len(cf["changed_inputs"]),
          "complete", *[cf["groups"][m]["union_all_required"]["hits"] for m in ("question_only", "context_diagnostic")],
          "operating losses", len(cf["losses_vs_operating"]),
          "new losses", len(cf["losses_vs_current"]), "new civil", len(cf["new_required_civil_articles"]))
