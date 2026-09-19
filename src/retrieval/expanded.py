"""Product service for the explicitly activated expanded-law data profile."""
from dataclasses import replace
from contextvars import ContextVar
from copy import copy

from src.retrieval.context_policy import (
    _reference_graph, _reference_select, context_terms, dense_context_query,
    final_dense_query, final_law_terms,
)
from src.retrieval.hybrid import HybridRetriever, Member
from src.retrieval.companion_evidence import (
    COMPANION_POLICY_CONFIG, LeaseProtectionSelector, requests_lease_protection,
)
from src.retrieval.multi_evidence import POLICY_CONFIG, TaxLookupSelector, requested_tax_scopes
from src.retrieval.partitioned import PartitionedBM25Retriever
from src.retrieval.retriever import BM25Retriever
from src.retrieval.retrieval_intent import (
    INTENT_POLICY_CONFIG, LawIntentSelector, existing_member_union, requested_law_intents,
)
from src.retrieval.service import CIVIL, LAW, PROCEDURE_TITLES, RetrievalService, _to_evidence

POLICY = "expanded-laws-record-v1"
CIVIL_IDS = tuple(f"민법-제{n}조" for n in (
    105, 111, 113, 114, 118, 147, 186, 187, 265, 357, 390, 393, 470,
    536, 543, 548, 615, 623, 626, 627, 629, 632, 634, 636, 640, 654,
))


class ContextDense:
    def __init__(self, dense, channel):
        self.dense, self.channel = dense, channel

    def search(self, query, k, where=None):
        expanded = final_dense_query(query) if self.channel == "general" else dense_context_query(query, "civil")
        return self.dense.search(expanded, k, where)


def civil_terms(query):
    return context_terms(query, "civil")


class ExpandedLawRetrievalService(RetrievalService):
    profile_name = POLICY

    def __init__(self, chunks, dense=None, civil_dense=None):
        self._embedding_cache = ContextVar("expanded_law_embedding_cache", default=None)
        def cached(retriever):
            if retriever is None or not hasattr(retriever, "backend"):
                return retriever
            cloned = copy(retriever)
            cloned.backend = RequestEmbeddingCache(retriever.backend, self._embedding_cache)
            return cloned
        dense, civil_dense = cached(dense), cached(civil_dense)
        civil = replace(CIVIL, include_ids=CIVIL_IDS)
        super().__init__(chunks, dense, civil=civil, civil_dense=civil_dense)
        self._anchors = {c["chunk_id"]: c["metadata"].get("article_id", "") for c in chunks}
        self._reference_graph, _ = _reference_graph(chunks, CIVIL_IDS)
        laws = [c for c in chunks if c["metadata"].get("doc_type") in LAW.doc_types
                and c["metadata"].get("title") != "민법"]
        self._tax_lookup_selector = TaxLookupSelector(laws)
        self.selection_config = POLICY_CONFIG
        self._lease_protection_selector = LeaseProtectionSelector(laws)
        self.companion_selection_config = COMPANION_POLICY_CONFIG
        self._law_intent_selector = LawIntentSelector(laws)
        self.intent_selection_config = INTENT_POLICY_CONFIG
        lexical = lambda part: BM25Retriever(part, b=LAW.bm25_b, query_expander=final_law_terms)
        law_bm25 = PartitionedBM25Retriever({
            "core": lexical([c for c in laws if c["metadata"].get("title") not in PROCEDURE_TITLES]),
            "procedure": lexical([c for c in laws if c["metadata"].get("title") in PROCEDURE_TITLES]),
        }, PROCEDURE_TITLES)
        civil_bm25 = BM25Retriever([
            c for c in chunks if c["metadata"].get("title") == "민법"
            and c["metadata"].get("article_id") in CIVIL_IDS
        ], b=civil.bm25_b, query_expander=civil_terms)
        self._context_law = self._context_retriever(law_bm25, dense, "general", LAW.expand_weight, 20)
        self._context_civil = self._context_retriever(civil_bm25, civil_dense or dense, "civil", civil.expand_weight, 26)

    def search(self, *args, **kwargs):
        token = self._embedding_cache.set({})
        try:
            return super().search(*args, **kwargs)
        finally:
            self._embedding_cache.reset(token)

    @staticmethod
    def _context_retriever(bm25, dense, channel, expand_weight, depth):
        members = [Member(bm25, "bm25_context", 1, expand_weight)]
        if dense is not None:
            members.append(Member(ContextDense(dense, channel), "dense_context", 1))
        return HybridRetriever(members, rrf_k=5, depth=depth)

    def _search_one(self, corpus, question, k):
        if corpus.name != self.corpora[0].name:
            return super()._search_one(corpus, question, k)
        if k <= 0:
            return []
        # Both members already search depth20. Retain more of that same fused
        # pool only when a direct lookup request needs its companion evidence.
        if requested_tax_scopes(question):
            hits = self._context_law.search(question, max(k, POLICY_CONFIG["candidate_depth"]), corpus.where())
            hits = self._tax_lookup_selector.select(question, hits, k, corpus.where())
        elif k >= COMPANION_POLICY_CONFIG["minimum_budget"] and requests_lease_protection(question):
            hits = self._context_law.search(question, max(k, COMPANION_POLICY_CONFIG["candidate_depth"]), corpus.where())
            hits = self._lease_protection_selector.select(question, hits, k, corpus.where())
        elif k >= INTENT_POLICY_CONFIG["minimum_budget"] and requested_law_intents(question):
            hits = existing_member_union(self._context_law, question, k, corpus.where())
            hits = self._law_intent_selector.select(question, hits, k, corpus.where())
        else:
            hits = self._context_law.search(question, k, corpus.where())
        return [_to_evidence(i, self._chunks[cid], score) for i, (cid, score) in enumerate(hits, 1)]

    def _search_civil_candidates(self, question, topics, limit):
        if limit <= 0:
            return []
        seed = self._search_civil(question, topics, min(2, limit))
        ranked, members = self._context_civil.search_with_member_hits(
            question, len(self.civil.include_ids), self.civil.where(),
        )
        members.setdefault("dense_context", [])
        row = {"civil_seed": [e.chunk_id for e in seed], "civil": members}
        selected, _ = _reference_select(row, self._anchors, self._reference_graph)
        scores = dict(ranked)
        return [_to_evidence(i, self._chunks[cid], scores[cid])
                for i, cid in enumerate(selected[:limit], 1)]


class RequestEmbeddingCache:
    """Share vectors within one search only, without mutating a shared backend."""
    def __init__(self, delegate, context):
        self.delegate, self.context = delegate, context
        self.name = delegate.name

    def embed(self, texts):
        cache = self.context.get()
        if cache is None:
            return self.delegate.embed(texts)
        key = (id(self.delegate), tuple(texts))
        if key not in cache:
            cache[key] = self.delegate.embed(texts)
        return cache[key]
