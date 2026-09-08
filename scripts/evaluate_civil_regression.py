"""Compare two prepared Chroma corpora on published law/Civil Act regression sets."""

import argparse
import json
from pathlib import Path
import subprocess

from src.evaluation.baseline import evaluate, fingerprint, load_dataset, prepare_questions, settings
from src.retrieval.dense import ChromaRetriever, SentenceTransformerEmbedding
from src.retrieval.index import clean_metadata
from src.retrieval.retriever import load_chunks
from src.retrieval.service import CASE_CHUNKS, GUIDE_CHUNKS, DEFAULT_MODEL, RetrievalService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-chunks", required=True, type=Path)
    parser.add_argument("--before-index", required=True, type=Path)
    parser.add_argument("--after-chunks", required=True, type=Path)
    parser.add_argument("--after-index", required=True, type=Path)
    parser.add_argument("--civil-index", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output already exists")
    for index in (args.before_index, args.after_index, args.civil_index):
        if args.out.resolve().is_relative_to(index.resolve()):
            parser.error("Output must be outside both indexes")
        if not (index / "chroma.sqlite3").is_file():
            parser.error("Prepared index is missing")
    civil_path = Path("data/eval/minbeop_review_holdout_20260901.jsonl")
    civil_rows = load_dataset(civil_path, "law")
    # This published 3rd-project set calls non-answerable rows 'abstain'.
    # Normalize only in memory; preserve the frozen source and its fingerprint.
    civil_rows = [{**row, "answer_type": "unanswerable"}
                  if row.get("answer_type") == "abstain" else row for row in civil_rows]
    backend = SentenceTransformerEmbedding(DEFAULT_MODEL)
    common = load_chunks(CASE_CHUNKS) + load_chunks(GUIDE_CHUNKS)
    services, corpora = {}, {}
    for name, path, index in (("before", args.before_chunks, args.before_index),
                              ("after", args.after_chunks, args.after_index)):
        chunks = load_chunks(path) + common
        dense = ChromaRetriever(backend, index)
        found = dense.collection.get(include=["documents", "metadatas"])
        civil_dense = ChromaRetriever(backend, args.civil_index) if name == "after" else None
        if civil_dense is not None:
            civil_found = civil_dense.collection.get(include=["documents", "metadatas"])
            for key in ("ids", "documents", "metadatas"):
                found[key] += civil_found[key]
        expected = {c["chunk_id"]: c for c in chunks}
        if len(expected) != len(chunks) or len(found["ids"]) != len(expected) or set(found["ids"]) != set(expected):
            raise ValueError(f"{name}: index/chunk ID mismatch")
        for cid, body, metadata in zip(found["ids"], found["documents"], found["metadatas"]):
            if body != expected[cid]["text"] or metadata != clean_metadata(expected[cid]["metadata"]):
                raise ValueError(f"{name}: index/chunk content mismatch")
        services[name] = RetrievalService(chunks, dense, civil_dense=civil_dense)
        corpora[name] = chunks
    results = {}
    for split in ("dev", "holdout"):
        path = Path(f"data/eval/{split}.jsonl")
        rows = load_dataset(path, "law")
        results[split] = {"input": fingerprint(path)}
        for name in services:
            selected, excluded = prepare_questions(rows, corpora[name], "law")
            results[split][name] = evaluate(services[name], selected, corpora[name], "law")
            results[split][name]["exclusions"] = excluded
        changes = []
        for row in rows:
            a, b = [services[name].search(row["question"]) for name in ("before", "after")]
            for kind, limit in (("laws", 3), ("cases", 5), ("guides", 2)):
                old = [e.chunk_id for e in getattr(a, kind)[:limit]]
                new = [e.chunk_id for e in getattr(b, kind)[:limit]]
                if old != new:
                    changes.append({"qid": row["qid"], "kind": kind, "before": old, "after": new})
        results[split]["ranking_changes"] = changes
    path = civil_path
    selected, excluded = prepare_questions(civil_rows, corpora["after"], "law")
    results["civil_published_regression"] = evaluate(services["after"], selected, corpora["after"], "law")
    results["civil_published_regression"].update(input=fingerprint(path), exclusions=excluded)
    results["civil_published_regression"]["label_adapter"] = "abstain -> unanswerable in memory only"
    results["civil_published_regression"]["excluded_civil_exposure"] = [
        {"qid": row["qid"], "civil_topics": list(services["after"].search(row["question"]).civil_topics)}
        for row in civil_rows if not row["gold_articles"]]
    payload = {"purpose": "published regression, not independent evaluation",
               "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
               "model": DEFAULT_MODEL, "settings": settings(services["after"]),
               "inputs": [fingerprint(p) for p in (args.before_chunks, args.after_chunks, CASE_CHUNKS, GUIDE_CHUNKS)],
               "results": results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({name: value.get("metrics", {key: value[key]["metrics"] for key in ("before", "after") if key in value})
                      for name, value in results.items()}))


if __name__ == "__main__":
    main()
