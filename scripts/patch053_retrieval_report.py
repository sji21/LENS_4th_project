"""Report paired retrieval changes without calling models or changing gold labels."""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts import patch041_retrieval_eval as base
from scripts import patch051_companion_eval as paired


def report(before: Path, after: Path, out: Path) -> None:
    if out.exists():
        raise ValueError("Preserve previous reports; choose a new output path")
    paired.compare(before, after, out)
    summary = base.read(out)
    rows = [base.read(path / "rows.json") for path in (before, after)]
    audits = [base.read(path / "audit.json") for path in (before, after)]
    channel_metrics, details, channel_changes = {}, [], {}
    for old, new in zip(*rows):
        targets = set(map(base.norm, old["targets"]))
        for budget in (3, 5):
            previous, current = (row["results"][str(budget)] for row in (old, new))
            for channel in ("laws", "civil_laws", "cases", "guides"):
                if previous[channel] != current[channel]:
                    channel_changes[channel] = channel_changes.get(channel, 0) + 1
            if not old["score"]:
                continue
            scope_channels = (("cases",) if old["scope"] == "case" else
                              ("laws", "civil_laws") if old["scope"] == "combined" else ("laws",))
            old_found, new_found = (set(paired.retrieved(row, budget)) for row in (old, new))
            details.append({
                "qid": old["qid"], "mode": old["mode"], "group": old["group"],
                "budget": budget, "targets": sorted(targets),
                "before": sorted(targets & old_found), "after": sorted(targets & new_found),
                "gained": sorted((targets & new_found) - old_found),
                "lost": sorted((targets & old_found) - new_found),
                "before_complete": targets <= old_found, "after_complete": targets <= new_found,
                "counts": {channel: len(current[channel]) for channel in current},
            })
            for channel in scope_channels:
                relevant = {target for target in targets if
                            (channel == "cases" or
                             (target.startswith("민법-") if channel == "civil_laws" else
                              not target.startswith("민법-")))}
                if not relevant:
                    continue
                for k in ((1, 3) if channel == "civil_laws" else (1, 3, 5)):
                    if channel == "laws" and k > budget:
                        continue
                    key = f'{old["group"]}:law-budget{budget}:{channel}:hit@{k}'
                    bucket = channel_metrics.setdefault(key, {"n": 0, "before": 0, "after": 0})
                    bucket["n"] += 1
                    for name, result in (("before", previous), ("after", current)):
                        selected = {base.norm(item["article_id"]) for item in result[channel][:k]}
                        bucket[name] += bool(relevant & selected)
    old_sources, new_sources = (audit["before"]["sources"] for audit in audits)
    summary.update(
        channel_hit_metrics=channel_metrics,
        channel_result_changes=channel_changes,
        complete_to_incomplete=[r for r in details if r["before_complete"] and not r["after_complete"]],
        target_changes=[r for r in details if r["gained"] or r["lost"]],
        changed_sources=[name for name in sorted(old_sources.keys() | new_sources.keys())
                         if old_sources.get(name) != new_sources.get(name)],
        note="Channel Hit@K counts any fixed target in that channel; all-required uses each original scope. "
             "Published development/regression only. No generated-answer quality measurement.",
    )
    base.write(out, summary)
    print({"target_changed": len(summary["target_changes"]),
           "lost": len(summary["required_target_losses"]),
           "complete_to_incomplete": len(summary["complete_to_incomplete"])})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("before", "after", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    report(args.before, args.after, args.out)
