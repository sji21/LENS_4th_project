"""Read-only, standard-library audit independent of the product/report modules."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import unicodedata


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value))


def selected(row, budget):
    channels = {"case": ("cases",), "general": ("laws",),
                "combined": ("laws", "civil_laws")}[row["scope"]]
    return {norm(e["article_id"]) for channel in channels
            for e in row["results"][str(budget)][channel]}


parser = argparse.ArgumentParser()
for key in ("before", "after", "report", "out", "source-root"):
    parser.add_argument("--" + key, type=Path, required=True)
parser.add_argument("--corpus", type=Path, nargs="+", required=True)
args = parser.parse_args()
assert not args.out.exists(), "Do not overwrite earlier independent evidence"
rows, audits = [], []
manifest_files = {}
for root in (args.before, args.after):
    manifest = read(root / "manifest.json")
    assert {"rows.json", "audit.json", "target-trace.json"} <= manifest.keys()
    for name, expected in manifest.items():
        assert sha(root / name) == expected, (root, name, "manifest mismatch")
    manifest_files[str(root)] = manifest
    audit = read(root / "audit.json")
    assert audit["before"] == audit["after"], "Capture was not stable"
    assert audit["generation_performed"] is False
    assert audit["budgets"] == {"laws": [3, 5], "civil_laws": 3, "cases": 5, "guides": 2}
    audits.append(audit)
    value = read(root / "rows.json")
    assert len(value) == 313
    assert len({(r["qid"], r["mode"]) for r in value}) == 313
    rows.append(value)
for key in ("criteria", "payload", "model", "logical_indices", "settings", "public_inputs",
            "tax_selection", "companion_selection"):
    assert audits[0]["before"][key] == audits[1]["before"][key], (key, "comparison drift")
for name, expected in audits[1]["after"]["sources"].items():
    actual = (args.source_root / name).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(actual).hexdigest() == expected, (name, "current source drift")

corpus, duplicate_corpus_ids = {}, []
for path in args.corpus:
    for line in path.read_text(encoding="utf-8").splitlines():
        chunk = json.loads(line)
        if chunk["chunk_id"] in corpus:
            assert corpus[chunk["chunk_id"]] == chunk, (chunk["chunk_id"], "Conflicting corpus duplicate")
            duplicate_corpus_ids.append(chunk["chunk_id"])
        corpus[chunk["chunk_id"]] = chunk

groups, losses, changes, targets_changed, channel_hits = {}, [], [], [], {}
channel_changes = Counter()
target_totals, focused, all_evidence = {}, [], 0
for old, new in zip(*rows):
    assert {k: v for k, v in old.items() if k not in ("results", "model_calls")} == {
        k: v for k, v in new.items() if k not in ("results", "model_calls")}
    assert hashlib.sha256(old["query"].encode()).hexdigest() == old["query_sha256"]
    assert old["model_calls"] == new["model_calls"], (old["qid"], "model call drift")
    gold = {norm(target) for target in old["targets"]}
    for budget in (3, 5):
        previous, current = selected(old, budget), selected(new, budget)
        for label, row in (("before", old), ("after", new)):
            for channel, evidence in row["results"][str(budget)].items():
                cap = {"laws": budget, "civil_laws": 3, "cases": 5, "guides": 2}[channel]
                assert len(evidence) <= cap
                assert len({e["chunk_id"] for e in evidence}) == len(evidence)
                assert [e["rank"] for e in evidence] == list(range(1, len(evidence) + 1))
                for item in evidence:
                    chunk = corpus[item["chunk_id"]]
                    meta = chunk["metadata"]
                    identity = meta.get("article_id") or meta.get("case_id") or meta.get("guide_id") or chunk["chunk_id"]
                    assert item["article_id"] == identity
                    assert item["text_sha256"] == hashlib.sha256(chunk["text"].encode()).hexdigest()
                    assert item["source_url"] == str(meta.get("source_url", ""))
                    assert meta.get("status") == "current"
                    assert meta["doc_type"] in {"laws": ("law", "rule", "decree"),
                        "civil_laws": ("law",), "cases": ("case",), "guides": ("guide",)}[channel]
                    if channel in ("laws", "civil_laws"):
                        assert (meta["title"] == "민법") == (channel == "civil_laws")
                    all_evidence += 1
        for channel in ("laws", "civil_laws", "cases", "guides"):
            if old["results"][str(budget)][channel] != new["results"][str(budget)][channel]:
                channel_changes[channel] += 1
        if old["results"][str(budget)] != new["results"][str(budget)]:
            changes.append((old["qid"], old["mode"], budget))
        if old["score"]:
            suffix = {"combined": f"law{budget}+civil3", "case": f"law{budget}+case5",
                      "general": f"law{budget}"}[old["scope"]]
            key = old["group"] + ":" + suffix
            bucket = groups.setdefault(key, dict(n=0, before_any=0, after_any=0, before_all=0, after_all=0))
            bucket["n"] += 1
            totals = target_totals.setdefault(key, dict(required=0, before=0, after=0))
            totals["required"] += len(gold)
            for label, found in (("before", previous), ("after", current)):
                bucket[label + "_any"] += bool(gold & found)
                bucket[label + "_all"] += gold <= found
                totals[label] += len(gold & found)
            lost, gained = sorted((gold & previous) - current), sorted((gold & current) - previous)
            if lost:
                losses.append(dict(qid=old["qid"], mode=old["mode"], k=budget, lost=lost))
            if gained or lost:
                targets_changed.append(dict(qid=old["qid"], mode=old["mode"], budget=budget, gained=gained, lost=lost))
            channels = {"case": ("cases",), "general": ("laws",), "combined": ("laws", "civil_laws")}[old["scope"]]
            for channel in channels:
                channel_gold = {t for t in gold if channel == "cases" or
                                ((t.startswith("민법-")) == (channel == "civil_laws"))}
                if not channel_gold:
                    continue
                for k in ((1, 3) if channel == "civil_laws" else (1, 3, 5)):
                    if channel == "laws" and k > budget:
                        continue
                    metric = f'{old["group"]}:law-budget{budget}:{channel}:hit@{k}'
                    score = channel_hits.setdefault(metric, dict(n=0, before=0, after=0))
                    score["n"] += 1
                    for label, row in (("before", old), ("after", new)):
                        evidence = row["results"][str(budget)][channel][:k]
                        score[label] += bool(channel_gold & {norm(e["article_id"]) for e in evidence})
        if old["qid"] in {"DEV-096", "DEV-005", "DEV-033", "DEV-038", "DEV-041", "DEV-046"}:
            focused.append(dict(qid=old["qid"], mode=old["mode"], budget=budget,
                                before=sorted(previous), after=sorted(current),
                                before_required=sorted(gold & previous), after_required=sorted(gold & current)))

report = read(args.report)
assert groups == report["groups"], "Group aggregate mismatch"
assert losses == report["required_target_losses"], "Loss aggregate mismatch"
assert dict(channel_changes) == report["channel_result_changes"]
assert channel_hits == report["channel_hit_metrics"]
assert set(changes) == {(r["qid"], r["mode"], r["k"]) for r in report["changes"]}
assert targets_changed == [{k: r[k] for k in ("qid", "mode", "budget", "gained", "lost")}
                          for r in report["target_changes"]]
details = read(args.report.with_name("details.json"))
assert details["model_usage"] == {label: audit["model_usage"]
                                  for label, audit in zip(("before", "after"), audits)}
expected_recall = {}
for key, value in target_totals.items():
    group, label = key.split(":", 1)
    budget = re.search(r"law(\d+)", label)[1]
    expected_recall[f"{group}:law-budget{budget}"] = {
        "targets": value["required"], "before": value["before"], "after": value["after"]}
assert details["target_recall"] == expected_recall
size_key, = [key for key, value in details.items() if isinstance(value, list)]
body_chars = []
for old, new in zip(*rows):
    for budget in (3, 5):
        if (old["qid"], old["mode"], budget) not in changes:
            continue
        entry = dict(qid=old["qid"], mode=old["mode"], law_budget=budget)
        for label, row in (("before", old), ("after", new)):
            entry[label + "_evidence_chars"] = sum(len(corpus[e["chunk_id"]]["text"])
                for evidence in row["results"][str(budget)].values() for e in evidence)
        body_chars.append(entry)
assert details[size_key] == body_chars, "Body-character count mismatch"
summary = dict(status="passed", manifest_files=manifest_files, report_sha256=sha(args.report),
               input_rows_each=313, budget_comparisons=626, evidence_instances_checked=all_evidence,
               groups=groups, target_totals=target_totals, losses=losses,
               changed_comparisons=len(changes), target_changed_comparisons=len(targets_changed),
               channel_result_changes=dict(channel_changes), focused=focused,
               details_sha256=sha(args.report.with_name("details.json")), body_chars=body_chars,
               model_usage={label: audit["model_usage"] for label, audit in zip(("before", "after"), audits)},
               identical_corpus_duplicates=len(duplicate_corpus_ids),
               note="Independent stdlib recalculation of shared regression; no model/index access")
args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: summary[k] for k in ("status", "input_rows_each", "budget_comparisons",
    "evidence_instances_checked", "losses", "changed_comparisons", "target_changed_comparisons")}, ensure_ascii=False))
