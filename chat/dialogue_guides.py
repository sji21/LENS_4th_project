"""Opt-in, source-attributed guidance supplement; never an answer fallback."""
import json
from dataclasses import replace
from pathlib import Path

from src.retrieval.service import Evidence


def renewal_guides():
    catalog = json.loads(Path(__file__).with_name("renewal_guides.json").read_text(encoding="utf-8"))
    return [Evidence(rank=index, chunk_id=row["id"], doc_type="guide",
                     citation=row["citation"], text=row["text"], score=1.0,
                     source_url=row["source_url"])
            for index, row in enumerate(catalog["guides"], 1)]


class GuidedSearch:
    def __init__(self, service):
        self.service = service

    def search(self, question, k_law=5, k_case=5, k_guide=2):
        result = self.service.search(question, k_law=k_law, k_case=k_case, k_guide=k_guide)
        if k_guide <= 0:
            return result
        # Return a new result: the shared retriever/cache must stay untouched.
        guides = renewal_guides() + list(result.guides)
        unique = {item.chunk_id: item for item in guides}
        return replace(result, guides=list(unique.values())[:k_guide])


def with_dialogue_guides(service, decision):
    if decision.topic == "계약갱신" and decision.action == "rag" and decision.document_id is None:
        return GuidedSearch(service)
    return service
