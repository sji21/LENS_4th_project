"""One bounded final candidate; no gold, item IDs, or saved rankings as inputs."""
from __future__ import annotations

import re

from scripts.patch027_context_live import ContextRetrievalService
from scripts.patch027_context_tuning import (
    LAW, _current_request, _law_bm25, law_concepts,
)
from src.retrieval.hybrid import HybridRetriever, Member
from src.retrieval.terms import expand_law

RECORDS = "확정일자 부여 현황 정보제공"
EFFECT = "확정일자 우선변제 주택 인도 주민등록"


def requests_date_record(query: str) -> bool:
    request = _current_request(query)
    request = re.sub(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」', " ", request)
    previous_date_record = False
    for clause in filter(str.strip, re.split(r"[.!?\n;]", request)):
        explicit = bool(re.search(
            r"확정일자(?:의|를|는|가)?\s*(?:부여\s*)?(?:기록|내역|여부|받았는지|유무)", clause,
        ))
        reference = previous_date_record and re.search(r"(?:예전|그때|당시)(?:의)?\s*(?:기록|내역)", clause)
        if ((explicit or reference) and re.search(r"확인|조회|열람|찾", clause)
                and not re.search(r"효력|대항력|우선\s*변제|보호|아니라", clause)):
            return True
        previous_date_record = explicit and not re.search(r"아니라|묻지|관계없", clause)
    return False


def final_law_concepts(query: str) -> list[str]:
    terms = law_concepts(query)
    if requests_date_record(query):
        terms = [term for term in terms if term != EFFECT]
        terms.append(RECORDS)
    return list(dict.fromkeys(terms))


def final_law_terms(query: str) -> list[str]:
    return list(dict.fromkeys(expand_law(query) + final_law_concepts(query)))


def final_dense_query(query: str) -> str:
    extra = final_law_concepts(query)
    return query + "\n관련 검색 개념: " + "; ".join(extra) if extra else query


class _FinalDense:
    def __init__(self, dense):
        self.dense = dense

    def search(self, query, k, where=None):
        return self.dense.search(final_dense_query(query), k, where)


class RecordLookupService(ContextRetrievalService):
    def __init__(self, chunks, dense, civil_dense):
        super().__init__(chunks, dense, civil_dense)
        self._context_law = HybridRetriever([
            Member(_law_bm25(chunks, final_law_terms), "bm25_context", 1, LAW.expand_weight),
            Member(_FinalDense(dense), "dense_context", 1),
        ], rrf_k=5, depth=20)
