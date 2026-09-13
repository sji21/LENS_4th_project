"""Fixed service retrieval baseline; published datasets are regression checks only."""

from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from time import perf_counter

from src.evaluation.metrics import hit_at_k, recall_at_k, reciprocal_rank
from src.retrieval.retriever import BM25Retriever, load_chunks
from src.retrieval.index import clean_metadata
from src.retrieval.service import (
    CASE_CHUNKS, DEFAULT_INDEX, DEFAULT_MODEL, GUIDE_CHUNKS,
    LAW_CHUNKS, LAW_TYPES, DEFAULT_CIVIL_INDEX, RetrievalService, CIVIL_TAIL_DENSE_MULTIPLIER,
)

SEARCH_K = {"k_law": 5, "k_case": 5, "k_guide": 2, "k_civil": 3}


def fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def load_dataset(path: Path, kind: str) -> list[dict]:
    gold_key = "gold_articles" if kind == "law" else "gold_case_ids"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not rows:
        raise ValueError("Empty evaluation dataset")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each question must be an object")
        qid = row.get("qid")
        if not isinstance(qid, str) or not qid.strip() or qid in seen:
            raise ValueError("Missing or duplicate qid")
        seen.add(qid)
        if not isinstance(row.get("question"), str) or not row["question"].strip():
            raise ValueError(f"Missing question: {qid}")
        gold = row.get(gold_key)
        if not isinstance(gold, list) or any(not isinstance(x, str) or not x.strip() for x in gold):
            raise ValueError(f"Invalid {gold_key}: {qid}")
        if kind == "case" and not gold:
            raise ValueError(f"Empty gold_case_ids: {qid}")
    return rows


def prepare_questions(rows: list[dict], chunks: list[dict], kind: str) -> tuple[list[dict], list[dict]]:
    """Exclude explicit guide-only/non-answerable law rows, never missing law labels."""
    gold_key = "gold_articles" if kind == "law" else "gold_case_ids"
    types = LAW_TYPES if kind == "law" else ("case",)
    id_key = "article_id" if kind == "law" else "case_id"
    available = {c["metadata"][id_key] for c in chunks if c["metadata"].get("doc_type") in types}
    guides = {c["metadata"].get("article_id") for c in chunks if c["metadata"].get("doc_type") == "guide"}
    selected, excluded = [], []
    for row in rows:
        gold = list(dict.fromkeys(row[gold_key]))
        if kind == "law" and not gold:
            if row.get("answer_type") not in ("unanswerable", "adversarial", "out_of_scope"):
                raise ValueError(f"Unexplained empty gold: {row['qid']}")
            excluded.append({"qid": row["qid"], "reason": "no law gold", "excluded_gold": []})
            continue
        removed = [x for x in gold if x in guides] if kind == "law" else []
        scoped = [x for x in gold if x not in removed]
        missing = set(scoped) - available
        if missing:
            raise ValueError(f"Gold absent from {kind} corpus: {row['qid']}: {sorted(missing)}")
        if removed:
            excluded.append({"qid": row["qid"], "reason": "guide gold excluded", "excluded_gold": removed})
        if scoped:
            selected.append({**row, "gold": scoped})
    if not selected:
        raise ValueError("No scorable questions")
    return selected, excluded


def evaluate(service, questions: list[dict], chunks: list[dict], kind: str,
             *, law_scope: str = "general") -> dict:
    if kind not in ("law", "case") or law_scope not in ("general", "civil", "combined"):
        raise ValueError("Invalid evaluation scope")
    if kind == "case" and law_scope != "general":
        raise ValueError("law_scope only applies to law evaluation")
    combined = kind == "law" and law_scope == "combined"
    civil = kind == "law" and law_scope == "civil"
    cutoffs = (1, 2, 3) if civil else (1, 2, 3, 5)
    for question in questions:
        if not question["gold"]:
            raise ValueError("No gold for scoring")
        if kind == "law" and not combined:
            if any(a.startswith("민법-") != civil for a in question["gold"]):
                raise ValueError("Gold outside law_scope; select civil or combined explicitly")
    id_key = "article_id" if kind == "law" else "case_id"
    types = LAW_TYPES if kind == "law" else ("case",)
    ids = {c["chunk_id"]: c["metadata"][id_key] for c in chunks if c["metadata"].get("doc_type") in types}
    rows = []
    for question in questions:
        started = perf_counter()
        result = service.search(question["question"], **SEARCH_K)
        elapsed = perf_counter() - started
        if combined:
            channels = {"general": result.laws[:5], "civil": result.civil_laws[:3]}
            evidence = channels["general"] + channels["civil"]
        else:
            evidence = (result.civil_laws[:3] if civil else
                        result.laws[:5] if kind == "law" else result.cases[:5])
        ranked = list(dict.fromkeys(ids[e.chunk_id] for e in evidence))
        gold = question["gold"]
        if combined:
            found = set(ranked) & set(gold)
            metrics = {"hit@law5+civil3": int(bool(found)),
                       "all@law5+civil3": int(set(gold) <= set(ranked)),
                       "recall@law5+civil3": len(found) / len(set(gold))}
        else:
            metrics = {f"hit@{k}": int(hit_at_k(ranked, gold, k)) for k in cutoffs}
            metrics.update({f"recall@{k}": recall_at_k(ranked, gold, k) for k in cutoffs})
            metrics["mrr"] = reciprocal_rank(ranked, gold)
        rows.append({"qid": question["qid"], "gold": gold, "retrieved_ids": ranked,
                     **({"channel_retrieved_ids": {
                         name: list(dict.fromkeys(ids[e.chunk_id] for e in items))
                         for name, items in channels.items()}} if combined else
                        {"gold_ranks": {x: ranked.index(x) + 1 if x in ranked else None for x in gold}}),
                     "chunk_ids": [e.chunk_id for e in evidence],
                     "guide_count": len(result.guides), "seconds": elapsed, **metrics})
    if not rows:
        raise ValueError("No scorable questions")
    keys = list(metrics)
    ranking = ("unordered union of general 5 and civil 3 chunks; no merged rank/MRR" if combined else
               f"deduplicate IDs after {3 if civil else 5} returned chunks; MRR truncated at {3 if civil else 5}")
    return {"scope": law_scope if kind == "law" else "case", "ranking": ranking,
            "n": len(rows), "metrics": {k: sum(r[k] for r in rows) / len(rows) for k in keys},
            "mean_search_seconds": sum(r["seconds"] for r in rows) / len(rows), "questions": rows}


def retriever_settings(retriever) -> dict | None:
    """Read the parameters off the built retriever, never a copy of them.

    A literal here would keep reporting the old value after someone retunes
    the retriever, and the report exists to say what the run actually used.
    """
    if retriever is None:
        return None
    bm25 = next((m.retriever for m in retriever.members
                 if isinstance(m.retriever, BM25Retriever)), None)
    return {"rrf_k": retriever.rrf_k, "depth": retriever.depth,
            "bm25": None if bm25 is None
                    else {"k1": bm25.k1, "b": bm25.b, "char_ngram": bm25.char_ngram}}


def settings(service) -> dict:
    corpora = {}
    for key, corpus in zip(("law", "case", "guide", "civil"), (*service.corpora, service.civil)):
        config = {f.name: (f"{value.__module__}.{value.__name__}" if callable(value) else value)
                  for f in fields(corpus) for value in [getattr(corpus, f.name)]}
        config["retriever"] = retriever_settings(service._retrievers.get(corpus.name))
        corpora[key] = config
    return {"search_k": SEARCH_K, "corpora": corpora,
            "civil_selection": {"preserve_top": 2, "tail_dense_multiplier": CIVIL_TAIL_DENSE_MULTIPLIER,
                                "applies_at_limit": 3}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=("law", "case"))
    parser.add_argument("--law-scope", choices=("general", "civil", "combined"), default="general")
    parser.add_argument("--eval-set", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--law-chunks", type=Path, default=LAW_CHUNKS)
    parser.add_argument("--case-chunks", type=Path, default=CASE_CHUNKS)
    parser.add_argument("--guide-chunks", type=Path, default=GUIDE_CHUNKS)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--civil-index", type=Path, default=DEFAULT_CIVIL_INDEX)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    if args.kind == "case" and args.law_scope != "general":
        parser.error("--law-scope only applies to --kind law")
    # Never replace a frozen dataset, baseline report, or index by accident.
    if args.out.exists():
        parser.error("Output already exists; use a new report path")
    paths = (args.law_chunks, args.case_chunks, args.guide_chunks)
    if any(args.out.resolve().is_relative_to(p.resolve()) for p in (args.index, args.civil_index)):
        parser.error("Output must be outside the index")
    try:
        inputs = [fingerprint(p) for p in (*paths, args.eval_set)]
        index_db = fingerprint(args.index / "chroma.sqlite3")
        chunks = [c for p in paths for c in load_chunks(p)]
        if len({c["chunk_id"] for c in chunks}) != len(chunks):
            raise ValueError("Duplicate chunk_id across input files")
        questions, exclusions = prepare_questions(load_dataset(args.eval_set, args.kind), chunks, args.kind)
        service = RetrievalService.from_index(chunk_paths=paths, index_path=args.index, model=args.model,
                                              civil_index_path=args.civil_index)
        # Catch missing or stale indexed records before reporting a misleading score.
        indexed = service.dense.collection.get(include=["documents", "metadatas"])
        civil_index_db = None
        if service.civil_dense is not None:
            civil_index_db = fingerprint(args.civil_index / "chroma.sqlite3")
            civil_indexed = service.civil_dense.collection.get(include=["documents", "metadatas"])
            for key in ("ids", "documents", "metadatas"):
                indexed[key] += civil_indexed[key]
        expected = {c["chunk_id"]: c for c in chunks}
        if len(indexed["ids"]) != len(expected) or set(indexed["ids"]) != set(expected):
            raise ValueError("Index/chunk IDs differ; restore the matching baseline artifacts")
        for cid, document, metadata in zip(indexed["ids"], indexed["documents"], indexed["metadatas"]):
            if document != expected[cid]["text"] or metadata != clean_metadata(expected[cid]["metadata"]):
                raise ValueError(f"Index/chunk content mismatch: {cid}")
        result = evaluate(service, questions, chunks, args.kind, law_scope=args.law_scope)
        revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip())
        payload = {"created_at": datetime.now(timezone.utc).isoformat(), "code_commit": revision,
                   "worktree_dirty": dirty,
                   "python": platform.python_version(), "platform": platform.platform(),
                   "purpose": "published baseline reproduction/regression; not independent holdout",
                   "kind": args.kind, "model": args.model, "settings": settings(service),
                   "inputs": inputs, "index_sqlite_before_run": index_db,
                   "civil_index_sqlite_before_run": civil_index_db,
                   "index_records_verified": len(expected), "exclusions": exclusions,
                   "guide_evaluation": "counts only; no guide accuracy", **result}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as target:
            json.dump(payload, target, ensure_ascii=False, indent=2)
            target.write("\n")
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps({"kind": args.kind, "n": result["n"], "metrics": result["metrics"], "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
