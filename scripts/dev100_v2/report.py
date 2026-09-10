"""Validate and replay the saved DEV v2 primary-law-article diagnostic offline.

No model, network, operational DB, or legal-answer grading is involved. The reviewed
plan is an input, not inferred from retrieved rankings or generated answers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "data/eval/dev100-v2"
MODES = ("question_only", "context_diagnostic")
CATEGORIES = (
    "전부 보유·TOP3 전부 반환", "전부 보유·TOP5만 전부 반환",
    "전부 보유·TOP5 검색 누락", "필수 조문 일부 미보유", "필수 조문 전부 미보유",
    "고정 필수 법령 목표 없음", "시점별 근거 별도 확인",
)
REQUIRED_FILES = {
    "raw/01_최종정답_근거통합본.md", "raw/02_리뷰1대비_변경표.md",
    "raw/03_미확인_판단보류목록.md", "raw/04_실제검색_원문확인기록.md",
    "raw/05_리뷰2_최소보완_명제별근거표.md", "raw/06_최소보완_검색확인기록.md",
    "raw/검토진행.md",
    "questions.json", "requirements.json", "source-registry.json",
    "supplement-claims.json", "changes-v1-v2.json", "diagnostic-plan.json",
    "reference-run.json", "expected/summary.json", "expected/article-diagnostics.json",
    "expected/missing-primary-articles.json",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def keyed(rows, field, label):
    result = {r[field]: r for r in rows}
    require(len(result) == len(rows), f"Duplicate {label}")
    return result


def validate_data(questions, plan, reference, sources):
    expected_ids = {f"DEV-{n:03}" for n in range(1, 101)}
    qs = keyed(questions, "qid", "question IDs")
    plans = keyed(plan, "qid", "plan IDs")
    require(set(qs) == set(plans) == expected_ids, "Expected exactly DEV-001..100")
    src = keyed(sources, "source_id", "source IDs")
    for source in sources:
        number = source.get("article_number_from_source")
        if source["kind"] == "law" and number:
            law = source.get("law_name_normalized", "")
            require(bool(law) and law == "".join(law.split()), "Invalid normalized law name")
            parts = number.split("의")
            article = "제" + parts[0] + "조" + ("의" + parts[1] if len(parts) > 1 else "")
            require(source.get("article_anchor_from_source") == law + "-" + article,
                    "Source law/article metadata mismatch")
    inventory = keyed(reference["inventory"], "chunk_id", "inventory chunk IDs")
    require(len(inventory) == 140, "Reference run must have 140 law articles")
    require(len({r["article_anchor"] for r in inventory.values()}) == 140,
            "Duplicate inventory article anchors")
    for row in inventory.values():
        expected = row["title"].replace(" ", "") + "-" + row["article_no"]
        require(row["article_anchor"] == expected, "Inventory title/article mismatch")
        require(bool(re.fullmatch(r"[0-9a-f]{64}", row["body_sha256"])), "Invalid body hash")
    for item in plan:
        require(item["input_modes"] == list(MODES), "Unsupported input modes")
        require(item["whole_item_correctness"] is None, "No whole-item correctness allowed")
        require(set(item["primary_source_ids"]) <= set(src), "Unknown primary source")
        anchors = [t["article_anchor"] for t in item["law_targets"]]
        require(len(anchors) == len(set(anchors)), "Duplicate target article")
        for target in item["law_targets"]:
            sid = target["source_id"]
            require(sid in item["primary_source_ids"], "Target not in primary source group")
            require(src[sid]["kind"] == "law", "Non-law source in law diagnostic")
            match = re.fullmatch(r"(.+)-제(\d+)조(?:의(\d+))?", target["article_anchor"])
            require(match is not None, "Malformed target article")
            article = match[2] + ("의" + match[3] if match[3] else "")
            require(article == src[sid]["article_number_from_source"],
                    f"Wrong branch article for {sid}")
            require(target["article_anchor"] == src[sid].get("article_anchor_from_source"),
                    f"Source/target law article mismatch for {sid}")
    seen = set()
    for row in reference["results"]:
        pair = (row["qid"], row["mode"])
        require(pair not in seen, "Duplicate saved input")
        seen.add(pair)
        require(pair[0] in qs and pair[1] in MODES, "Unknown saved input")
        query = qs[pair[0]]["modes"][pair[1]]["query"]
        require(hashlib.sha256(query.encode("utf-8")).hexdigest() == row["query_sha256"],
                f"Query hash mismatch: {pair}")
        for kind, limit in (("laws", 5), ("cases", 5), ("guides", 2)):
            hits = row[kind]
            require(len(hits) <= limit, f"Too many {kind} hits")
            require([h["rank"] for h in hits] == list(range(1, len(hits) + 1)), "Invalid ranks")
            require(len({h["chunk_id"] for h in hits}) == len(hits), "Duplicate returned chunk")
        for hit in row["laws"]:
            require(hit["chunk_id"] in inventory, "Returned law absent from inventory")
            stored = inventory[hit["chunk_id"]]
            require(hit["body_sha256"] == stored["body_sha256"], "Returned body hash mismatch")
            require(hit["article_anchor"] == stored["article_anchor"], "Returned article mismatch")
    require(seen == {(qid, mode) for qid in qs for mode in MODES}, "Missing saved inputs")


def load_dataset(dataset):
    dataset = Path(dataset).resolve()
    manifest = read_json(dataset / "manifest.json")
    require(manifest["schema"] == "dev100-v2-bundle-1", "Unsupported dataset schema")
    require(manifest["formal_whole_item_score_ready"] is False, "Dataset scope changed")
    require(REQUIRED_FILES <= set(manifest["files"]), "Manifest omits required files")
    for name, digest in manifest["files"].items():
        path = (dataset / name).resolve()
        require(path.is_relative_to(dataset), "Manifest path escapes dataset")
        require(path.is_file() and sha256(path) == digest, f"Dataset hash mismatch: {name}")
    require(sha256(dataset / "questions.json") == manifest["v1_question_sha256"],
            "Question inputs must remain identical to v1")
    questions = read_json(dataset / "questions.json")
    plan = read_json(dataset / "diagnostic-plan.json")
    reference = read_json(dataset / "reference-run.json")
    sources = read_json(dataset / "source-registry.json")
    require(reference["schema"] == "dev100-reference-run-1", "Unsupported run schema")
    validate_data(questions, plan, reference, sources)
    requirements = read_json(dataset / "requirements.json")
    require(set(keyed(requirements, "qid", "requirement IDs")) == {q["qid"] for q in questions},
            "Requirement IDs mismatch")
    require(all(r["formal_whole_item_score_ready"] is False for r in requirements),
            "Do not promote claim review into whole-item accuracy")
    return plan, reference


def classify(targets, available, ranked, historical=False):
    present = targets & available
    absent = targets - available
    if historical:
        return "시점별 근거 별도 확인"
    if not targets:
        return "고정 필수 법령 목표 없음"
    if not present:
        return "필수 조문 전부 미보유"
    if absent:
        return "필수 조문 일부 미보유"
    if targets <= set(ranked[:3]):
        return "전부 보유·TOP3 전부 반환"
    if targets <= set(ranked[:5]):
        return "전부 보유·TOP5만 전부 반환"
    return "전부 보유·TOP5 검색 누락"


def diagnose(plan, reference, plan_hash):
    available = {r["article_anchor"] for r in reference["inventory"]}
    runs = {(r["qid"], r["mode"]): r for r in reference["results"]}
    counts = {mode: Counter() for mode in MODES}
    missing = defaultdict(set)
    details = []
    for p in plan:
        targets = {t["article_anchor"] for t in p["law_targets"]}
        present, absent = targets & available, targets - available
        for target in absent:
            missing[target].add(p["qid"])
        modes = {}
        for mode in MODES:
            run = runs[p["qid"], mode]
            ranked = [h["article_anchor"] for h in run["laws"]]
            ranks = {a: ranked.index(a) + 1 if a in ranked else None for a in sorted(targets)}
            category = classify(targets, available, ranked, p["historical_review_required"])
            counts[mode][category] += 1
            modes[mode] = {"category": category, "ranks": ranks,
                          "retrievable_but_not_top5": sorted(a for a in present if ranks[a] is None),
                          "returned_cases": [h["citation"] for h in run["cases"]],
                          "returned_guides": [h["citation"] for h in run["guides"]]}
        details.append({"qid": p["qid"], "targets": sorted(targets), "present": sorted(present),
                        "absent": sorted(absent), "non_flat_law_targets": p["non_flat_law_targets"],
                        "modes": modes, "whole_item_score": None})
    require(all(sum(c.values()) == 100 for c in counts.values()), "Counts must partition 100 items")
    summary = {"unit": "fixed primary law-article anchors; not whole-question or semantic correctness",
               "items": 100, "saved_inputs": 200, "inventory_law_articles": 140,
               "new_retrieval_run": False, "input_hash_validated": True,
               "target_plan_sha256": plan_hash, "counts": {k: dict(v) for k, v in counts.items()},
               "unique_missing_primary_articles": len(missing), "old_scores_comparable": False,
               "guidance_case_accuracy_computed": False}
    return {"summary.json": summary, "article-diagnostics.json": details,
            "missing-primary-articles.json": [{"article": a, "qids": sorted(qids)}
                                              for a, qids in sorted(missing.items())]}


def check_expected(dataset, results):
    for name, result in results.items():
        require(result == read_json(Path(dataset) / "expected" / name), f"Reference result differs: {name}")


def write_results(out, dataset, results):
    out, dataset = Path(out).resolve(), Path(dataset).resolve()
    require(not out.is_relative_to(dataset) and not dataset.is_relative_to(out),
            "Output must be outside dataset and its ancestors")
    require(not out.exists(), "Use a new output directory")
    out.mkdir(parents=True)
    for name, result in results.items():
        (out / name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# DEV v2 기본 법령 조 단위 진단", "",
             "저장된 검색 200입력의 재대조다. 새 검색·LLM 평가·전체 정답률이 아니다.", "",
             "| 분류 | 질문 단독 | 문맥 포함 |", "|---|---:|---:|"]
    counts = results["summary.json"]["counts"]
    for category in CATEGORIES:
        lines.append(f"| {category} | {counts[MODES[0]].get(category, 0)} | {counts[MODES[1]].get(category, 0)} |")
    lines += ["| 합계 | 100 | 100 |", "",
              "고정 필수 법령 목표가 없는 문항을 자동 성공으로 세지 않는다. 조건부·판례·기관 안내·판본·항호 의미 충족은 별도 평가한다.", ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Validate hashes and replay against expected outputs")
    action.add_argument("--out", type=Path, help="New output folder outside the dataset")
    args = parser.parse_args()
    try:
        plan, reference = load_dataset(args.dataset)
        results = diagnose(plan, reference, sha256(args.dataset / "diagnostic-plan.json"))
        if args.check:
            check_expected(args.dataset, results)
            print("PASS: dataset hashes, 100 items, 200 inputs, 140 articles, three expected outputs")
        else:
            write_results(args.out, args.dataset, results)
            print(json.dumps(results["summary.json"], ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"DEV v2 validation failed: {error}\n")


if __name__ == "__main__":
    main()
