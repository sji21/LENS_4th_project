"""Describe fixed-article retrieval by gold size and requirement metadata.

Metadata flags do not activate conditional branches or grade paragraphs/OR.
Run with --check to verify the saved result against the frozen inputs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from scripts.patch015_baseline import norm

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/eval/patch042-stratified-analysis.json"
SOURCES = {
    "plan": "data/eval/dev100-v2/diagnostic-plan.json",
    "requirements": "data/eval/dev100-v2/requirements.json",
    "rows": "data/eval/patch041-rebuilt/capture/rows.json",
}


def summarize(items):
    n = len(items)
    return {
        "n": n,
        "any_hit": sum(x["found_count"] > 0 for x in items),
        "all_hit": sum(x["found_count"] == x["required_count"] for x in items),
        "macro_recall": sum(x["found_count"] / x["required_count"] for x in items) / n,
        "qids": [x["qid"] for x in items],
    }


def analyze():
    data = {key: json.loads((ROOT / path).read_text(encoding="utf-8"))
            for key, path in SOURCES.items()}
    requirements = {x["qid"]: x for x in data["requirements"]}
    rows = {(x["qid"], x["mode"]): x for x in data["rows"]}
    assert len(rows) == len(data["rows"]) == 235
    eligible = [x for x in data["plan"]
                if x["law_targets"] and not x.get("historical_review_required", False)]
    assert len(eligible) == 75
    modes = {}
    for mode in ("question_only", "context_diagnostic"):
        buckets = defaultdict(list)
        details = []
        for item in eligible:
            qid = item["qid"]
            gold = {norm(t["article_anchor"]) for t in item["law_targets"]}
            row = rows[qid, mode]
            assert len(row["result"]["laws"]) <= 5
            assert len(row["result"]["civil_laws"]) <= 3
            returned = {norm(t["article_id"]) for channel in ("laws", "civil_laws")
                        for t in row["result"][channel]}
            groups = requirements[qid]["original_v2"]["groups"]
            flags = {
                "conditional": any(g["role"] == "conditional" for g in groups),
                "alternative": any(g["role"] == "alternative" for g in groups),
                "part_focus": any(a.get("part_focus") for g in groups for a in g["atoms"]),
            }
            civil = sum(t.startswith("민법-") for t in gold)
            channel = "civil_only" if civil == len(gold) else "both" if civil else "general_only"
            detail = {"qid": qid, "required_count": len(gold), "found_count": len(gold & returned),
                      "channel": channel, "original_requirement_flags": flags,
                      "required": sorted(gold), "found": sorted(gold & returned)}
            details.append(detail)
            buckets["all"].append(detail)
            buckets[f"required_count:{len(gold)}"].append(detail)
            buckets[f"channel:{channel}"].append(detail)
            for flag, present in flags.items():
                buckets[f"metadata:{flag}:{str(bool(present)).lower()}"].append(detail)
        modes[mode] = {"strata": {key: summarize(values) for key, values in sorted(buckets.items())},
                       "details": details}
        assert modes[mode]["strata"]["all"]["all_hit"] == 43
    original_groups = [g for r in requirements.values() for g in r["original_v2"]["groups"]]
    return {
        "schema": "patch042-stratified-fixed-article-retrieval-v1",
        "limits": {"laws": 5, "civil_laws": 3},
        "limitations": [
            "Fixed article targets, not answer accuracy or paragraph correctness.",
            "Any hit is not an OR score; gold targets remain unchanged.",
            "Metadata flags use original_v2 groups only, not resolved claim_overrides or active conditions.",
            "Conditional/alternative/part-focus strata overlap; they are not formal semantic scores.",
            "Same reviewed DEV data; no independent holdout or new retrieval run.",
        ],
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                          for path in SOURCES.values()},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "requirements_inventory": {
            "formal_whole_item_score_ready": sum(bool(r["formal_whole_item_score_ready"]) for r in requirements.values()),
            "questions": len(requirements),
            "group_operator_counts": dict(sorted(Counter(g["operator"] for g in original_groups).items())),
        },
        "modes": modes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = analyze()
    if args.check:
        assert result == json.loads(OUTPUT.read_text(encoding="utf-8")), "Saved analysis differs"
        print("Stratified analysis verified")
    else:
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({mode: {key: {k: v for k, v in stat.items() if k != "qids"}
                                for key, stat in value["strata"].items()}
                          for mode, value in result["modes"].items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
