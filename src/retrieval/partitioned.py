"""Pool lexical scores while keeping corpus statistics independent.

Scores from different BM25 corpora are not calibrated probabilities. Equal
weight is a measured policy for the current procedure bundle, not a guarantee
for arbitrary future corpora. Dense ranks still compete globally in RRF.
"""

from src.retrieval.retriever import BM25Retriever


class PartitionedBM25Retriever:
    def __init__(self, partitions: dict[str, BM25Retriever], procedure_titles: tuple[str, ...]):
        ids = [cid for retriever in partitions.values() for cid in retriever.chunk_ids]
        if len(ids) != len(set(ids)):
            raise ValueError("BM25 partitions must have disjoint chunk IDs")
        self.partitions = dict(partitions)
        self.procedure_titles = procedure_titles

    def search(self, query: str, k: int, where: dict | None = None,
               expand_weight: float = 0.0) -> list[tuple[str, float]]:
        if k <= 0:
            return []
        # Each partition's TOP-k suffices for the pooled TOP-k. No shared
        # last-query state, additional embeddings, or reserved result slots.
        hits = [hit for retriever in self.partitions.values()
                for hit in retriever.search(query, k, where, expand_weight)]
        return sorted(hits, key=lambda hit: (-hit[1], hit[0]))[:k]
