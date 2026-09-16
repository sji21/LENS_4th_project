"""Reproduce the PATCH-042 offline retrieval-gap analysis.

The module reads only verified, frozen evaluation artifacts.  It performs no
retrieval, model loading, search, tuning, legal regrading, or product changes.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
import hashlib
import io
import json
from pathlib import Path

from scripts.patch015_baseline import norm
from scripts.patch041_retrieval_eval import build_jobs, check as check_patch041
from scripts.patch042_trace_diagnosis import analyze as analyze_candidate_trace


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = ROOT / "data/eval/patch041-rebuilt/capture"
DEFAULT_OUT = ROOT / "data/eval/patch042-gap-analysis"
P27_ROWS = ROOT / "data/eval/patch027-final-test/rows.json"
P27_REPORT = ROOT / "data/eval/patch027-final-test/report.json"
P27_AUDIT = ROOT / "data/eval/patch027-final-test/audit.json"
CANDIDATE_DIAGNOSIS = ROOT / "data/eval/patch042-candidate-diagnosis.json"
ARTIFACTS = (
    "analysis.json",
    "missing-inputs.csv",
    "missing-articles.csv",
    "historical-losses.csv",
    "review.md",
)
SOURCE_PATHS = (
    "scripts/patch041_retrieval_eval.py",
    "src/generation/chain.py",
    "data/eval/patch041-rebuilt/capture/manifest.json",
    "data/eval/patch041-rebuilt/capture/audit.json",
    "data/eval/patch041-rebuilt/capture/rows.json",
    "data/eval/patch041-rebuilt/capture/report.json",
    "data/eval/dev100-v2/manifest.json",
    "data/eval/dev100-v2/questions.json",
    "data/eval/dev100-v2/diagnostic-plan.json",
    "data/eval/dev100-v2/requirements.json",
    "data/eval/civil-review2/manifest.json",
    "data/eval/civil-review2/retrieval-plan.json",
    "data/eval/patch015-baseline/capture/results.json",
    "data/eval/patch027-final-test/manifest.json",
    "data/eval/patch027-final-test/audit.json",
    "data/eval/patch027-final-test/rows.json",
    "data/eval/patch027-final-test/report.json",
    "data/eval/patch027-context-tuning/audit.json",
    "scripts/patch042_trace_diagnosis.py",
    "data/eval/patch042-candidate-trace/manifest.json",
    "data/eval/patch042-candidate-diagnosis.json",
)
MODES = ("question_only", "context_diagnostic")
RETURN_LIMITS = {"general": 5, "civil": 3}
CONSUMED_LIMITS = {"general": 3, "civil": 3}


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_sha(path: Path) -> str:
    content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(content).hexdigest()


def dependency_sha(path: Path) -> str:
    return source_sha(path) if path.suffix == ".py" else sha(path)


def stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def csv_text(rows: list[dict], fields: tuple[str, ...]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def channel_for(article: str) -> str:
    return "civil" if norm(article).startswith("민법-") else "general"


def current_articles(row: dict, channel: str, limit: int | None = None) -> list[str]:
    source = row["result"]["civil_laws" if channel == "civil" else "laws"]
    if limit is not None:
        source = source[:limit]
    return [norm(item["article_id"]) for item in source]


def evidence_rows(row: dict) -> dict[str, list[dict]]:
    result = {}
    for channel, values in row["result"].items():
        result[channel] = [
            {
                "rank": item["rank"],
                "chunk_id": item["chunk_id"],
                "article_id": norm(item["article_id"]) if channel in ("laws", "civil_laws") else None,
                "citation": item["citation"],
            }
            for item in values
        ]
    return result


def target_state(job: dict, row: dict, *, general_limit: int = 5) -> dict:
    targets = sorted(map(norm, job["targets"]))
    by_channel = {
        channel: [target for target in targets if channel_for(target) == channel]
        for channel in RETURN_LIMITS
    }
    returned = {
        "general": current_articles(row, "general", general_limit),
        "civil": current_articles(row, "civil"),
    }
    hits = {channel: sorted(set(values) & set(returned[channel]))
            for channel, values in by_channel.items()}
    missing = {channel: sorted(set(values) - set(returned[channel]))
               for channel, values in by_channel.items()}
    all_hits = sorted(hits["general"] + hits["civil"])
    all_missing = sorted(missing["general"] + missing["civil"])
    complete = bool(targets) and not all_missing
    return {
        "targets_by_channel": by_channel,
        "target_counts": {channel: len(values) for channel, values in by_channel.items()},
        "returned_articles_by_channel": returned,
        "hits_by_channel": hits,
        "missing_by_channel": missing,
        "hit_count": len(all_hits),
        "missing_count": len(all_missing),
        "complete": complete,
        "miss_shape": None if complete else ("partial" if all_hits else "zero"),
        "missing_channels": [channel for channel in RETURN_LIMITS if missing[channel]],
        "capacity": {
            channel: {
                "required": len(by_channel[channel]),
                "limit": general_limit if channel == "general" else RETURN_LIMITS[channel],
                "required_exceeds_limit": len(by_channel[channel])
                > (general_limit if channel == "general" else RETURN_LIMITS[channel]),
            }
            for channel in RETURN_LIMITS
        },
    }


def question_catalog() -> dict[str, dict]:
    return {item["qid"]: item for item in read(ROOT / "data/eval/dev100-v2/questions.json")}


def review_input(job: dict, row: dict, questions: dict[str, dict], candidate_trace: dict) -> dict:
    question = questions[job["qid"]]
    returned = target_state(job, row)
    consumed = target_state(job, row, general_limit=CONSUMED_LIMITS["general"])
    returned_beyond_top3 = sorted(
        set(returned["hits_by_channel"]["general"])
        - set(consumed["hits_by_channel"]["general"])
    )
    return {
        "qid": job["qid"],
        "mode": job["mode"],
        "area": question["area"],
        "question": question["question"],
        "context": question["context"],
        "full_query": job["query"],
        "query_sha256": job["query_sha256"],
        "returned_budget": returned,
        "consumed_budget": {
            **consumed,
            "returned_general_targets_beyond_top3": returned_beyond_top3,
        },
        "current_returned_evidence": evidence_rows(row),
        "candidate_visibility": {
            "patch041_capture_only": "unknown outside the stored return",
            "patch041_reason": (
                "PATCH-041 stores only final returned ranks (general 1..5, civil 1..3); "
                "it does not store the untruncated candidate/member ranking."
            ),
            "followup_trace_source": "data/eval/patch042-candidate-diagnosis.json",
            "missing_target_trace": [
                candidate_trace[job["qid"], job["mode"], target]
                for target in sorted(
                    returned["missing_by_channel"]["general"]
                    + returned["missing_by_channel"]["civil"]
                )
                if (job["qid"], job["mode"], target) in candidate_trace
            ],
        },
    }


def count_by_mode(items: list[dict]) -> dict:
    result = {}
    for mode in MODES:
        selected = [item for item in items if item["mode"] == mode]
        shapes = Counter(item["returned_budget"]["miss_shape"] for item in selected)
        channels = Counter(
            "+".join(item["returned_budget"]["missing_channels"]) for item in selected
        )
        result[mode] = {
            "missing_inputs": len(selected),
            "partial": shapes["partial"],
            "zero": shapes["zero"],
            "missing_channel_patterns": dict(sorted(channels.items())),
        }
    return result


def paired_impact(by_key: dict[tuple[str, str], dict], scored_qids: list[str]) -> dict:
    categories: defaultdict[str, list[str]] = defaultdict(list)
    target_hit_delta: defaultdict[str, list[str]] = defaultdict(list)
    details = []
    for qid in scored_qids:
        q = by_key[qid, "question_only"]["returned_budget"]
        c = by_key[qid, "context_diagnostic"]["returned_budget"]
        if not q["complete"] and c["complete"]:
            category = "helped_to_complete"
        elif q["complete"] and not c["complete"]:
            category = "hurt_from_complete"
        elif q["complete"]:
            category = "complete_in_both"
        else:
            category = "missing_in_both"
        delta = c["hit_count"] - q["hit_count"]
        direction = "more_target_hits" if delta > 0 else "fewer_target_hits" if delta < 0 else "same_target_hits"
        categories[category].append(qid)
        target_hit_delta[direction].append(qid)
        details.append({
            "qid": qid,
            "complete_effect": category,
            "target_hit_delta_context_minus_question": delta,
            "question_only_missing": sorted(q["missing_by_channel"]["general"] + q["missing_by_channel"]["civil"]),
            "context_diagnostic_missing": sorted(c["missing_by_channel"]["general"] + c["missing_by_channel"]["civil"]),
        })
    return {
        "complete_effect": {key: {"count": len(value), "qids": value}
                            for key, value in sorted(categories.items())},
        "target_hit_effect": {key: {"count": len(value), "qids": value}
                              for key, value in sorted(target_hit_delta.items())},
        "details": details,
    }


def article_frequency(missing: list[dict]) -> list[dict]:
    values: dict[str, dict] = {}
    for item in missing:
        for channel, articles in item["returned_budget"]["missing_by_channel"].items():
            for article in articles:
                value = values.setdefault(article, {
                    "article_id": article,
                    "channel": channel,
                    "question_only_occurrences": 0,
                    "context_diagnostic_occurrences": 0,
                    "qids": set(),
                })
                value[item["mode"] + "_occurrences"] += 1
                value["qids"].add(item["qid"])
    result = []
    for value in values.values():
        total = value["question_only_occurrences"] + value["context_diagnostic_occurrences"]
        result.append({**value, "total_occurrences": total,
                       "unique_qid_count": len(value["qids"]), "qids": sorted(value["qids"])})
    return sorted(result, key=lambda value: (-value["total_occurrences"], value["article_id"]))


def article_ids_from_p27(result: dict, channel: str, anchors: dict[str, str], limit=None) -> list[str]:
    values = result["civil_laws" if channel == "civil" else "laws"]
    if limit is not None:
        values = values[:limit]
    return [norm(anchors[item["chunk_id"]]) for item in values]


def p27_evidence(result: dict, anchors: dict[str, str]) -> dict[str, list[dict]]:
    output = {}
    for channel, values in result.items():
        output[channel] = []
        for item in values:
            entry = {"rank": item["rank"], "chunk_id": item["chunk_id"], "citation": item["citation"]}
            if channel in ("laws", "civil_laws"):
                entry["article_id"] = norm(anchors[item["chunk_id"]])
            output[channel].append(entry)
    return output


def historical_comparison(jobs: list[dict], current_rows: list[dict], questions: dict[str, dict],
                          candidate_trace: dict) -> dict:
    anchors = read(P27_AUDIT)["anchors"]
    old_rows = {(row["qid"], row["mode"]): row for row in read(P27_ROWS)}
    current = {(row["qid"], row["mode"]): row for row in current_rows}
    returned_losses, top3_losses = [], []
    for job in jobs:
        if job["track"] != "dev100":
            continue
        key = job["qid"], job["mode"]
        old = old_rows[key]["operating"]
        now = current[key]
        targets = set(map(norm, job["targets"]))
        old_all = set(article_ids_from_p27(old, "general", anchors) + article_ids_from_p27(old, "civil", anchors))
        now_all = set(current_articles(now, "general") + current_articles(now, "civil"))
        lost = sorted(targets & old_all - now_all)
        old_top3 = set(article_ids_from_p27(old, "general", anchors, 3))
        now_top3 = set(current_articles(now, "general", 3))
        top3_lost = sorted(targets & old_top3 - now_top3)
        base = {
            "qid": job["qid"], "mode": job["mode"],
            "question": questions[job["qid"]]["question"],
            "context": questions[job["qid"]]["context"],
            "full_query": job["query"], "query_sha256": job["query_sha256"],
            "targets": sorted(targets),
            "old_operating_returned": p27_evidence(old, anchors),
            "current_final_returned": evidence_rows(now),
            "current_missing_candidate_visibility": {
                "patch041_capture_only": "unknown outside the stored return",
                "followup_trace_source": "data/eval/patch042-candidate-diagnosis.json",
                "lost_target_trace": [candidate_trace[key[0], key[1], article]
                                      for article in sorted(set(lost + top3_lost))
                                      if (key[0], key[1], article) in candidate_trace],
            },
        }
        if lost:
            returned_losses.append({**base, "lost_required_articles": lost})
        if top3_lost:
            top3_losses.append({**base, "lost_general_top3_articles": top3_lost})
    expected = read(P27_REPORT)["results"]["record_lookup"]
    def triples(items, field):
        return {(item["qid"], item["mode"], article) for item in items for article in item[field]}
    if triples(returned_losses, "lost_required_articles") != triples(expected["losses"], "lost"):
        raise ValueError("Historical returned-loss replay differs from PATCH-027 final report")
    if triples(top3_losses, "lost_general_top3_articles") != triples(expected["general_top3_losses"], "lost"):
        raise ValueError("Historical TOP3-loss replay differs from PATCH-027 final report")
    returned_keys = {(item["qid"], item["mode"]) for item in returned_losses}
    top3_keys = {(item["qid"], item["mode"]) for item in top3_losses}
    return {
        "meaning": (
            "Historical PATCH-027 operating-to-record_lookup comparison. PATCH-041 is identical "
            "to record_lookup, so these are not new PATCH-041 regressions."
        ),
        "returned_loss_count": len(returned_losses),
        "general_top3_loss_count": len(top3_losses),
        "overlap_input_count": len(returned_keys & top3_keys),
        "returned_loss_only_input_count": len(returned_keys - top3_keys),
        "top3_loss_only_input_count": len(top3_keys - returned_keys),
        "returned_losses": returned_losses,
        "general_top3_losses": top3_losses,
    }


def analyze(run: Path = DEFAULT_RUN) -> dict:
    run = Path(run).resolve()
    verified = check_patch041(run)
    rows = read(run / "rows.json")
    jobs = build_jobs(ROOT)
    keyed_rows = {(row["qid"], row["mode"]): row for row in rows}
    questions = question_catalog()
    candidate_diagnosis = read(CANDIDATE_DIAGNOSIS)
    if candidate_diagnosis != analyze_candidate_trace():
        raise ValueError("Candidate diagnosis does not replay from its verified trace")
    candidate_trace = {
        (item["qid"], item["mode"], norm(item["target"])): item
        for item in candidate_diagnosis["details"]
        if item["track"] == "dev100"
    }
    reviewed = [review_input(job, keyed_rows[job["qid"], job["mode"]], questions, candidate_trace)
                for job in jobs if job["track"] == "dev100"]
    by_key = {(item["qid"], item["mode"]): item for item in reviewed}
    scored_qids = sorted({item["qid"] for item in reviewed
                          if item["returned_budget"]["targets_by_channel"]["general"]
                          or item["returned_budget"]["targets_by_channel"]["civil"]})
    # The report's reviewed denominator excludes two historical-review items.
    report_details = {(item["qid"], item["mode"]): item
                      for item in read(run / "report.json")["details"]}
    scored_qids = [qid for qid in scored_qids
                   if report_details[qid, "question_only"]["category"] != "historical_review"]
    missing = [item for item in reviewed if item["qid"] in scored_qids
               and not item["returned_budget"]["complete"]]
    frequency = article_frequency(missing)
    missing_trace_keys = {
        (item["qid"], item["mode"], target)
        for item in missing
        for channel in RETURN_LIMITS
        for target in item["returned_budget"]["missing_by_channel"][channel]
    }
    if missing_trace_keys != set(candidate_trace):
        raise ValueError("Candidate diagnosis does not cover exactly the current DEV missing targets")
    paired = paired_impact(by_key, scored_qids)
    return_counts = {}
    consumed_counts = {}
    capacity = {}
    for mode in MODES:
        selected = [by_key[qid, mode] for qid in scored_qids]
        return_counts[mode] = {"hits": sum(item["returned_budget"]["complete"] for item in selected),
                               "n": len(selected)}
        consumed_counts[mode] = {
            "hits": sum(item["consumed_budget"]["complete"] for item in selected),
            "n": len(selected),
            "complete_at_return5_but_not_consumed3": [
                item["qid"] for item in selected
                if item["returned_budget"]["complete"] and not item["consumed_budget"]["complete"]
            ],
        }
        capacity[mode] = {
            "returned_budget_overflow_qids": [
                item["qid"] for item in selected
                if any(value["required_exceeds_limit"]
                       for value in item["returned_budget"]["capacity"].values())
            ],
            "consumed_budget_overflow_qids": [
                item["qid"] for item in selected
                if any(value["required_exceeds_limit"]
                       for value in item["consumed_budget"]["capacity"].values())
            ],
            "target_count_distribution": {
                channel: {
                    str(count): frequency for count, frequency in sorted(Counter(
                        item["returned_budget"]["target_counts"][channel] for item in selected
                    ).items())
                } for channel in RETURN_LIMITS
            },
        }
        capacity[mode]["returned_budget_overflow_inputs"] = len(
            capacity[mode]["returned_budget_overflow_qids"]
        )
        capacity[mode]["consumed_budget_overflow_inputs"] = len(
            capacity[mode]["consumed_budget_overflow_qids"]
        )
    if return_counts != {mode: verified["groups"][mode]["union_all_required"] for mode in MODES}:
        raise ValueError("Independent return-budget counts differ from verified PATCH-041 report")
    historical = historical_comparison(jobs, rows, questions, candidate_trace)
    if len(frequency) != 25:
        raise ValueError(f"Expected 25 unique missing articles, got {len(frequency)}")
    source_hashes = {path: dependency_sha(ROOT / path) for path in SOURCE_PATHS}
    return {
        "schema": "patch042-gap-analysis-v1",
        "scope": (
            "Offline development-set retrieval evidence gap analysis only; no tuning, retrieval, "
            "model loading, legal regrading, production change, or independent holdout claim."
        ),
        "units": {
            "success": "all fixed required article identifiers returned",
            "returned_budget": "general law 5 plus civil law 3",
            "consumed_budget": "generation default general law 3 plus the same civil law 3",
            "candidate_visibility": (
                "PATCH-041 alone is unknown outside stored final ranks; the separate PATCH-042 "
                "candidate diagnosis is linked for observed member/fusion visibility."
            ),
        },
        "verification": {
            "patch041_check_passed": True,
            "patch041_inputs": verified["inputs"],
            "patch041_model_usage_recorded": verified["model_usage"],
            "patch041_reference": verified["comparison"]["reference"],
            "patch041_vs_patch027_final_lost_required_inputs": len(verified["comparison"]["lost_required_inputs"]),
            "patch041_vs_patch027_final_all_channel_ranking_changes": len(verified["comparison"]["all_channel_ranking_changes"]),
            "source_sha256": source_hashes,
            "source_hash_policy": {
                "python": "SHA-256 after CRLF and CR normalization to LF",
                "frozen_json_and_manifests": "byte-exact SHA-256; repository attributes preserve bytes",
            },
        },
        "summary": {
            "return_budget_complete": return_counts,
            "missing_by_mode": count_by_mode(missing),
            "unique_missing_qids": len({item["qid"] for item in missing}),
            "missing_in_both_modes": paired["complete_effect"]["missing_in_both"],
            "question_only_missing_only": paired["complete_effect"]["helped_to_complete"],
            "context_diagnostic_missing_only": paired["complete_effect"]["hurt_from_complete"],
            "unique_missing_articles": len(frequency),
        },
        "context_effect": paired,
        "capacity_analysis": {
            "limits": {"returned": RETURN_LIMITS, "consumed": CONSUMED_LIMITS},
            "by_mode": capacity,
            "interpretation": (
                "No fixed target set exceeds the general-law 5 plus civil-law 3 return limits, so target "
                "count cannot force a stored-return miss. Five inputs per mode require more than the "
                "generation general-law 3 limit, making consumed completeness structurally impossible "
                "for those inputs regardless of ranking."
            ),
        },
        "candidate_followup": {
            "source": "data/eval/patch042-candidate-diagnosis.json",
            "source_sha256": sha(CANDIDATE_DIAGNOSIS),
            "unit": candidate_diagnosis["unit"],
            "missing_target_instances": candidate_diagnosis["dev_missing_target_instances"],
            "stages": candidate_diagnosis["dev_stages"],
            "boundary": candidate_diagnosis["limitations"],
        },
        "consumed_budget_analysis": {
            "source": "src/generation/chain.py DEFAULT_K_LAW=3",
            "complete": consumed_counts,
            "complete_at_return5_but_not_consumed3_total": sum(
                len(value["complete_at_return5_but_not_consumed3"]) for value in consumed_counts.values()
            ),
            "interpretation": "Consumption loss is separate from failure to enter the stored return set.",
        },
        "missing_article_frequency": frequency,
        "missing_inputs": missing,
        "historical_patch027_comparison": historical,
        "improvement_possibilities": [
            {
                "priority": 1,
                "focus": "Use traced failure stages before selecting a ranking intervention",
                "basis": "The linked candidate diagnosis separates fusion cutoffs, observed-candidate absence, and civil selection displacement.",
                "next_evidence": "Review source-level causes within each traced stage before testing any policy change.",
            },
            {
                "priority": 2,
                "focus": "Review high-frequency missing articles and zero-hit inputs first",
                "basis": f"{len(frequency)} unique articles account for all missing occurrences; zero-hit and partial misses are separated in the tables.",
                "next_evidence": "Use candidate traces to distinguish candidate-generation gaps from final ranking/truncation gaps.",
            },
            {
                "priority": 3,
                "focus": "Treat context gains and regressions as paired cases",
                "basis": "Five fixed-target questions improve to complete with context and five fall from complete.",
                "next_evidence": "Compare the paired member rankings and query terms; do not infer causality from final ranks alone.",
            },
            {
                "priority": 4,
                "focus": "Evaluate return and generation-consumption objectives separately",
                "basis": "Eight mode inputs are complete at general-law 5 but incomplete at the generation general-law 3 budget.",
                "next_evidence": "Keep TOP3 diagnostics separate when testing any future change.",
            },
            {
                "priority": 5,
                "focus": "Keep return-capacity and generation-capacity decisions separate",
                "basis": "Return 5/3 has zero target-count overflow; consumed 3/3 has five overflow inputs per mode.",
                "next_evidence": "Review answer-context constraints before testing generation-consumption changes.",
            },
        ],
    }


def make_csv_artifacts(result: dict) -> dict[str, str]:
    missing_rows = []
    for item in result["missing_inputs"]:
        returned = item["returned_budget"]
        missing_rows.append({
            "qid": item["qid"], "mode": item["mode"], "area": item["area"],
            "question": item["question"], "context": item["context"], "full_query": item["full_query"],
            "general_targets": " | ".join(returned["targets_by_channel"]["general"]),
            "civil_targets": " | ".join(returned["targets_by_channel"]["civil"]),
            "general_hits": " | ".join(returned["hits_by_channel"]["general"]),
            "civil_hits": " | ".join(returned["hits_by_channel"]["civil"]),
            "general_missing": " | ".join(returned["missing_by_channel"]["general"]),
            "civil_missing": " | ".join(returned["missing_by_channel"]["civil"]),
            "miss_shape": returned["miss_shape"],
            "returned_law_ids_ranked": " | ".join(e["article_id"] for e in item["current_returned_evidence"]["laws"]),
            "returned_civil_ids_ranked": " | ".join(e["article_id"] for e in item["current_returned_evidence"]["civil_laws"]),
            "candidate_visibility": json.dumps(item["candidate_visibility"], ensure_ascii=False),
        })
    fields = tuple(missing_rows[0])
    article_rows = [{**item, "qids": " | ".join(item["qids"])}
                    for item in result["missing_article_frequency"]]
    article_fields = tuple(article_rows[0])
    historical_rows = []
    historical = result["historical_patch027_comparison"]
    for kind, items, field in (
        ("returned", historical["returned_losses"], "lost_required_articles"),
        ("general_top3", historical["general_top3_losses"], "lost_general_top3_articles"),
    ):
        for item in items:
            historical_rows.append({
                "comparison": kind, "qid": item["qid"], "mode": item["mode"],
                "question": item["question"], "context": item["context"], "full_query": item["full_query"],
                "lost_articles": " | ".join(item[field]),
                "candidate_visibility": "unknown in current final outside stored return",
            })
    return {
        "missing-inputs.csv": csv_text(missing_rows, fields),
        "missing-articles.csv": csv_text(article_rows, article_fields),
        "historical-losses.csv": csv_text(historical_rows, tuple(historical_rows[0])),
    }


def make_review(result: dict) -> str:
    summary = result["summary"]
    consumed = result["consumed_budget_analysis"]
    historical = result["historical_patch027_comparison"]
    lines = [
        "# PATCH-042 DEV retrieval gap review", "",
        "This is an offline article-identifier analysis of the verified PATCH-041 capture.", "",
        "## Headline counts", "",
        "| Measure | Question only | Context diagnostic |", "|---|---:|---:|",
        f"| Complete at returned law5+civil3 | {summary['return_budget_complete']['question_only']['hits']}/75 | {summary['return_budget_complete']['context_diagnostic']['hits']}/75 |",
        f"| Missing | {summary['missing_by_mode']['question_only']['missing_inputs']} | {summary['missing_by_mode']['context_diagnostic']['missing_inputs']} |",
        f"| Partial / zero | {summary['missing_by_mode']['question_only']['partial']} / {summary['missing_by_mode']['question_only']['zero']} | {summary['missing_by_mode']['context_diagnostic']['partial']} / {summary['missing_by_mode']['context_diagnostic']['zero']} |",
        f"| Complete at consumed law3+civil3 | {consumed['complete']['question_only']['hits']}/75 | {consumed['complete']['context_diagnostic']['hits']}/75 |",
        "", f"Unique missing questions: {summary['unique_missing_qids']}; unique missing articles: {summary['unique_missing_articles']}.",
        "", "## Interpretation boundaries", "",
        "- Current final PATCH-041 equals the PATCH-027 `record_lookup` finalist on all 235 four-channel rankings.",
        "- The current final context result is 43/75. The older PATCH-027 `verified_context` result was 42/75.",
        "- PATCH-041 alone has unknown visibility beyond final ranks; the linked PATCH-042 candidate diagnosis resolves observed stages for current missing targets.",
        "- General TOP3 consumption is analyzed separately from the stored general TOP5 return.",
        "- No fixed target set exceeds return capacity; five inputs per mode exceed the generation general-law 3 capacity.",
        "", "## Historical PATCH-027 comparison", "",
        f"Returned losses: {historical['returned_loss_count']}; general TOP3 losses: {historical['general_top3_loss_count']}; overlap: {historical['overlap_input_count']}; TOP3-only: {historical['top3_loss_only_input_count']}.",
        "These are historical operating-to-finalist changes, not new PATCH-041 regressions.",
        "", "## Missing input details", "",
    ]
    for item in result["missing_inputs"]:
        state = item["returned_budget"]
        lines += [
            f"### {item['qid']} / {item['mode']}", "",
            f"- Area: {item['area']}",
            f"- Question: {item['question']}",
            f"- Context: {item['context']}",
            f"- Full query: {item['full_query'].replace(chr(10), ' / ')}",
            f"- Miss shape: {state['miss_shape']}; missing channels: {', '.join(state['missing_channels'])}",
            f"- General targets / hits / missing: {state['targets_by_channel']['general']} / {state['hits_by_channel']['general']} / {state['missing_by_channel']['general']}",
            f"- Civil targets / hits / missing: {state['targets_by_channel']['civil']} / {state['hits_by_channel']['civil']} / {state['missing_by_channel']['civil']}",
            f"- Returned general IDs: {[e['article_id'] for e in item['current_returned_evidence']['laws']]}",
            f"- Returned civil IDs: {[e['article_id'] for e in item['current_returned_evidence']['civil_laws']]}",
            f"- Candidate follow-up: {item['candidate_visibility']['missing_target_trace']}", "",
        ]
    return "\n".join(lines) + "\n"


def build(out: Path = DEFAULT_OUT, run: Path = DEFAULT_RUN) -> dict:
    out = Path(out).resolve()
    if out.exists():
        raise ValueError("Use a fresh output directory")
    result = analyze(run)
    payloads = {"analysis.json": stable_json(result), "review.md": make_review(result)}
    payloads.update(make_csv_artifacts(result))
    out.mkdir(parents=True)
    for name, text in payloads.items():
        (out / name).write_text(text, encoding="utf-8", newline="")
    manifest = {
        "schema": "patch042-gap-analysis-manifest-v1",
        "artifacts": {name: sha(out / name) for name in ARTIFACTS},
        "source_sha256": result["verification"]["source_sha256"],
    }
    (out / "manifest.json").write_text(stable_json(manifest), encoding="utf-8", newline="")
    return result


def check(out: Path = DEFAULT_OUT, run: Path = DEFAULT_RUN) -> dict:
    out = Path(out).resolve()
    manifest = read(out / "manifest.json")
    if manifest.get("schema") != "patch042-gap-analysis-manifest-v1":
        raise ValueError("Unsupported output manifest")
    if set(manifest.get("artifacts", {})) != set(ARTIFACTS):
        raise ValueError("Output artifact set changed")
    if any(sha(out / name) != digest for name, digest in manifest["artifacts"].items()):
        raise ValueError("Output artifact hash mismatch")
    replay = analyze(run)
    if replay != read(out / "analysis.json"):
        raise ValueError("Analysis does not replay")
    expected = {"review.md": make_review(replay), **make_csv_artifacts(replay)}
    for name, text in expected.items():
        if (out / name).read_text(encoding="utf-8") != text:
            raise ValueError(f"{name} does not replay")
    if manifest["source_sha256"] != replay["verification"]["source_sha256"]:
        raise ValueError("Source hashes changed")
    return replay


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("build", "check"):
        command = commands.add_parser(name)
        command.add_argument("--run", type=Path, default=DEFAULT_RUN)
        command.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    result = build(args.out, args.run) if args.action == "build" else check(args.out, args.run)
    print(json.dumps({
        "return_complete": result["summary"]["return_budget_complete"],
        "missing": result["summary"]["missing_by_mode"],
        "consumed_complete": result["consumed_budget_analysis"]["complete"],
        "historical_returned_losses": result["historical_patch027_comparison"]["returned_loss_count"],
        "historical_top3_losses": result["historical_patch027_comparison"]["general_top3_loss_count"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
