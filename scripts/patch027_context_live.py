"""Development-only service adapter and fresh verification for PATCH-027.

No saved rankings or gold answers are accepted by the adapter. The inherited
service performs case/guide routing and builds the normal RetrievalResult.
"""
from __future__ import annotations

import hashlib
import subprocess
import time

from scripts.patch015_baseline import ROOT, read, sha, write
from src.retrieval.hybrid import HybridRetriever, Member
from src.retrieval.service import RetrievalService, _to_evidence


def execution_spec():
    from scripts.patch027_context_tuning import DEPENDENCIES, FINAL_POLICIES
    tracked = subprocess.check_output(
        ["git", "ls-files", "src", "scripts", "requirements*.txt"], cwd=ROOT, text=True,
    ).splitlines()
    paths = sorted(set(tracked + ["scripts/patch027_context_tuning.py",
                                  "scripts/patch027_context_live.py"]))
    return {
        "policies": list(FINAL_POLICIES), "inputs": 235,
        "search_k": {"k_law": 5, "k_case": 5, "k_guide": 2, "k_civil": 3},
        "source_hashes": {p: sha(ROOT / p) for p in paths if (ROOT / p).is_file()},
        "dependencies": {p: sha(ROOT / p) for p in DEPENDENCIES},
        "measurement": "fresh service calls; query cache resets per service/input",
    }


class _ContextDense:
    def __init__(self, dense, channel):
        self.dense, self.channel = dense, channel

    def search(self, query, k, where=None):
        from scripts.patch027_context_tuning import dense_context_query
        return self.dense.search(dense_context_query(query, self.channel), k, where)


class ContextRetrievalService(RetrievalService):
    """Frozen context_both + context_reference, using the real service API."""

    def __init__(self, chunks, dense, civil_dense):
        from scripts.patch027_context_tuning import (
            CANDIDATE_CIVIL, LAW, _law_bm25, _civil_bm25, context_terms, _reference_graph,
        )
        super().__init__(chunks, dense, civil=CANDIDATE_CIVIL, civil_dense=civil_dense)
        self._anchors = {c["chunk_id"]: c["metadata"].get("article_id", "") for c in chunks}
        self._reference_graph, _ = _reference_graph(chunks)
        self._context_law = HybridRetriever([
            Member(_law_bm25(chunks, lambda q: context_terms(q, "general")),
                   "bm25_context", 1, LAW.expand_weight),
            Member(_ContextDense(dense, "general"), "dense_context", 1),
        ], rrf_k=5, depth=20)
        self._context_civil = HybridRetriever([
            Member(_civil_bm25(chunks, lambda q: context_terms(q, "civil")),
                   "bm25_context", 1, CANDIDATE_CIVIL.expand_weight),
            Member(_ContextDense(civil_dense, "civil"), "dense_context", 1),
        ], rrf_k=5, depth=26)

    def _search_one(self, corpus, question, k):
        if corpus.name != self.corpora[0].name:
            return super()._search_one(corpus, question, k)
        if k <= 0:
            return []
        return [_to_evidence(i, self._chunks[cid], score)
                for i, (cid, score) in enumerate(
                    self._context_law.search(question, k, corpus.where()), 1)]

    def _search_civil_candidates(self, question, topics, limit):
        from scripts.patch027_context_tuning import civil_select
        if limit <= 0:
            return []
        # Topic seeds use the unchanged original-query retriever, just as in
        # the comparison control. Expansion applies to the full candidate pool.
        seed = self._search_civil(question, topics, min(2, limit))
        ranked, members = self._context_civil.search_with_member_hits(
            question, len(self.civil.include_ids), self.civil.where(),
        )
        row = {"civil_seed": [e.chunk_id for e in seed], "civil": members}
        selected, _ = civil_select(row, "context_reference", self._anchors, self._reference_graph)
        scores = dict(ranked)
        return [_to_evidence(i, self._chunks[cid], scores[cid])
                for i, cid in enumerate(selected[:limit], 1)]


def evidence_identity(evidence):
    return {"chunk_id": evidence.chunk_id, "doc_type": evidence.doc_type,
            "citation": evidence.citation, "source_url": evidence.source_url,
            "text_sha256": hashlib.sha256(evidence.text.encode()).hexdigest()}


CHANNELS = ("laws", "civil_laws", "cases", "guides")


def _serialize(result):
    return {name: [{**evidence_identity(e), "rank": e.rank} for e in getattr(result, name)]
            for name in CHANNELS}


def _expected_ids(row, anchors, graph, baseline):
    from scripts.patch027_context_tuning import general_select, civil_select, FINAL_POLICIES
    return {"laws": general_select(row, FINAL_POLICIES[0]),
            "civil_laws": civil_select(row, FINAL_POLICIES[1], anchors, graph)[0],
            "cases": baseline["cases"], "guides": baseline["guides"]}


def validate_live_row(observed, expected_ids, catalog):
    if set(observed) != set(CHANNELS):
        raise ValueError("Missing live channel")
    for channel, limit in zip(CHANNELS, (5, 3, 5, 2)):
        values = observed[channel]
        if [e["chunk_id"] for e in values] != expected_ids[channel] or len(values) > limit:
            raise ValueError("Live ranking differs from selected policy")
        for rank, evidence in enumerate(values, 1):
            if evidence != {**catalog[evidence["chunk_id"]], "rank": rank}:
                raise ValueError("Live body/source/rank mismatch")


def verify_live(chunks, dense, civil_dense, traces, anchors, graph, out, original_embed):
    from scripts.patch027_context_tuning import CANDIDATE_CIVIL
    from src.retrieval.index import clean_metadata

    # Verify the candidate source text/metadata against the actual vectors once,
    # before checking each service result against that catalog.
    chunk_map = {c["chunk_id"]: c for c in chunks}
    seen = set()
    for retriever in (dense, civil_dense):
        content = retriever.collection.get(include=["documents", "metadatas"])
        for cid, body, meta in zip(content["ids"], content["documents"], content["metadatas"]):
            if (cid in seen or cid not in chunk_map or body != chunk_map[cid]["text"]
                    or meta != clean_metadata(chunk_map[cid]["metadata"])):
                raise ValueError("Candidate body/source differs from indexed document")
            seen.add(cid)
    if seen != set(chunk_map):
        raise ValueError("Incomplete candidate indexes")
    catalog = {cid: evidence_identity(_to_evidence(1, c, 0)) for cid, c in chunk_map.items()}
    base = RetrievalService(chunks, dense, civil=CANDIDATE_CIVIL, civil_dense=civil_dense)
    tuned = ContextRetrievalService(chunks, dense, civil_dense)
    previous = read(ROOT / "data/eval/patch026-full/capture/after.json")
    queries = read(ROOT / "data/eval/patch015-baseline/capture/results.json")
    inverse = {anchor: cid for cid, anchor in anchors.items()}
    rows = []
    for query, trace, prior in zip(queries, traces, previous):
        saved = {"qid": query["qid"], "mode": query["mode"], "query_sha256": query["query_sha256"]}
        for label, service in (("baseline", base), ("candidate", tuned)):
            cache, calls = {}, {"embed_requests": 0, "model_calls": 0}

            def measured(texts):
                calls["embed_requests"] += 1
                key = tuple(texts)
                if key not in cache:
                    calls["model_calls"] += 1
                    cache[key] = original_embed(texts)
                return cache[key]

            dense.backend.embed = measured
            started = time.perf_counter()
            result = service.search(query["query"], k_law=5, k_case=5, k_guide=2, k_civil=3)
            elapsed = time.perf_counter() - started
            saved[label] = _serialize(result)
            saved[label + "_cost"] = {**calls, "seconds": elapsed}
            expected = (_expected_ids(trace, anchors, graph, prior) if label == "candidate" else
                        {name: [inverse[a] for a in prior[name]] if name in ("laws", "civil_laws")
                         else prior[name] for name in CHANNELS})
            validate_live_row(saved[label], expected, catalog)
        if any(saved["baseline"][k] != saved["candidate"][k] for k in ("cases", "guides")):
            raise ValueError("Case/guide evidence changed")
        rows.append(saved)
        if len(rows) % 25 == 0:
            print(f"{len(rows)}/235 fresh baseline/adapter matches", flush=True)
    dense.backend.embed = original_embed
    result = {"inputs": len(rows), "policies": ["context_both", "context_reference"],
              "rows": rows, "scope": "development adapter; production policy unchanged"}
    write(out / "evidence-catalog.json", catalog)
    write(out / "live-verification.json", result)
    return result


def check_live(run, traces, anchors, graph):
    from scripts.patch027_context_tuning import FINAL_POLICIES
    live = read(run / "live-verification.json")
    catalog = read(run / "evidence-catalog.json")
    previous = read(ROOT / "data/eval/patch026-full/capture/after.json")
    inverse = {anchor: cid for cid, anchor in anchors.items()}
    if live["inputs"] != 235 or len(live["rows"]) != 235 or live["policies"] != list(FINAL_POLICIES):
        raise ValueError("Incomplete live run or changed finalist")
    for actual, trace, prior in zip(live["rows"], traces, previous):
        if any(actual[k] != trace[k] for k in ("qid", "mode", "query_sha256")):
            raise ValueError("Live input identity changed")
        validate_live_row(actual["candidate"], _expected_ids(trace, anchors, graph, prior), catalog)
        expected_base = {name: [inverse[a] for a in prior[name]] if name in ("laws", "civil_laws")
                         else prior[name] for name in CHANNELS}
        validate_live_row(actual["baseline"], expected_base, catalog)
        if any(actual["baseline"][k] != actual["candidate"][k] for k in ("cases", "guides")):
            raise ValueError("Case/guide live results changed")
