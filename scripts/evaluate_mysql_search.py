"""Compare original JSONL and MySQL exports with the same verified indexes.

This isolates migration/routing regressions. Gold retrieval results are reported
separately and never turn a parity pass into an answer-quality certification.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(release, model_dir, baseline_base, baseline_cases, output):
    import torch
    from src.retrieval.mysql_release import load_service, file_hash, runtime_versions
    from src.retrieval.expanded import ExpandedLawRetrievalService
    from src.retrieval.case_profile import CaseCorpusRetrievalService
    torch.set_num_threads(4)
    current = load_service(release, model_dir)
    base_paths = [Path(baseline_base) / "chunks" / name
                  for name in ("chunks.jsonl", "cases.jsonl", "guides.jsonl")]
    case_paths = [Path(baseline_cases) / "chunks" / name
                  for name in ("laws.jsonl", "cases.jsonl", "guides.jsonl")]
    original_base = [r for p in base_paths for r in rows(p)]
    original_cases = [r for p in case_paths for r in rows(p)]
    base = ExpandedLawRetrievalService(original_base, current.base_service.dense,
                                      current.base_service.civil_dense)
    baseline = CaseCorpusRetrievalService(original_cases, current.dense,
                                          current.case_profile, base_service=base)
    # Memoize identical query embeddings across the two services, not retrieval
    # results, so source filtering/ranking and evidence are still recomputed.
    backend = current.dense.backend
    embed = backend.embed
    cache = {}
    def cached(texts):
        key = tuple(texts)
        if key not in cache:
            cache[key] = embed(texts)
        return cache[key]
    backend.embed = cached
    datasets = [ROOT / "data/eval" / name for name in (
        "dev.jsonl", "holdout.jsonl", "case_holdout_current_20.jsonl",
        "minbeop_review_holdout_20260901.jsonl")]
    questions = [dict(q, dataset=p.name) for p in datasets for q in rows(p)]
    questions += [
        {"qid": "guide-smoke-hug", "question": "HUG 전세보증금 반환보증 이행 청구 절차는?", "dataset": "guide-smoke"},
        {"qid": "guide-smoke-tax", "question": "임대인의 미납국세를 열람하려면 어떤 서류가 필요한가요?", "dataset": "guide-smoke"}]
    channels = ("laws", "civil_laws", "cases", "guides")
    all_chunks = {"cases": current._chunks, "laws": current.base_service._chunks,
                  "civil_laws": current.base_service._chunks, "guides": current.base_service._chunks}
    available_cases = {str(r["metadata"].get("case_id")) for r in original_cases
                       if r["metadata"].get("doc_type") == "case"}
    available_articles = {r["metadata"].get("article_id") for r in original_base}
    outcomes, channel_counts = [], dict.fromkeys(channels, 0)
    for index, q in enumerate(questions, 1):
        left = baseline.search(q["question"], k_case=2)
        right = current.search(q["question"], k_case=2)
        identical = asdict(left) == asdict(right)
        ids = {c: [e.chunk_id for e in getattr(right, c)] for c in channels}
        for channel in channels:
            channel_counts[channel] += bool(ids[channel])
        gold = q.get("gold_case_ids", q.get("gold_articles", []))
        is_case = "gold_case_ids" in q
        found = ({str(all_chunks["cases"][cid]["metadata"].get("case_id")) for cid in ids["cases"]}
                 if is_case else {all_chunks[c][cid]["metadata"].get("article_id")
                                  for c in ("laws", "civil_laws") for cid in ids[c]})
        available = available_cases if is_case else available_articles
        item = {"qid": q["qid"], "dataset": q["dataset"], "question": q["question"],
                "identical": identical, "ids": ids, "gold": gold,
                "gold_hits": sorted(set(gold) & found),
                "gold_missing_from_corpus": sorted(set(gold) - available)}
        if not identical:
            item["baseline"] = asdict(left)
            item["mysql"] = asdict(right)
        outcomes.append(item)
        if q["dataset"] == "guide-smoke":
            payload_path = Path(output).with_name(q["qid"] + "-evidence.json")
            payload_path.write_text(json.dumps(current.evidence_payload(right), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{index}/{len(questions)} {q['qid']} parity={identical}", flush=True)
    report = {"schema": "lens-mysql-search-regression-v1", "release_id": current.mysql_release["release_id"],
              "scope": "original-JSONL-vs-MySQL-exports-with-identical-verified-indexes",
              "runtime_versions": runtime_versions(),
              "inputs": {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): file_hash(p)
                         for p in base_paths + case_paths + datasets},
              "questions": len(outcomes), "identical_results": sum(r["identical"] for r in outcomes),
              "queries_with_channel_results": channel_counts,
              "gold_questions": sum(bool(r["gold"]) for r in outcomes),
              "gold_questions_with_any_hit": sum(bool(r["gold_hits"]) for r in outcomes),
              "gold_questions_with_all_hits": sum(bool(r["gold"]) and set(r["gold"]) == set(r["gold_hits"]) for r in outcomes),
              "gold_questions_with_missing_corpus_evidence": sum(bool(r["gold_missing_from_corpus"]) for r in outcomes),
              "parity_pass": all(r["identical"] for r in outcomes), "llm_verified": False,
              "application_quality_verified": False, "results": outcomes}
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--release", type=Path, required=True)
    p.add_argument("--model-dir", type=Path, required=True)
    p.add_argument("--baseline-base", type=Path, required=True)
    p.add_argument("--baseline-cases", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error("기존 평가 결과는 덮어쓰지 않습니다.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = evaluate(args.release, args.model_dir, args.baseline_base, args.baseline_cases, args.output)
    if not result["parity_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
