"""Contracts of the opt-in retrieval experiment; no live model calls."""
from types import SimpleNamespace

import pytest

from scripts.retrieval_experiments.candidate_pipeline import DenseUnion, validate_selection


class DenseStub:
    backend = SimpleNamespace(name='same-model')
    collection = SimpleNamespace(metadata={'hnsw:space': 'cosine'})

    def __init__(self, hits):
        self.hits = hits

    def count(self):
        return len(self.hits)

    def search(self, query, k, where):
        return self.hits[:k]


def test_dense_union_competes_by_score_not_corpus_slots():
    union = DenseUnion([DenseStub([('standard', .9)]), DenseStub([('civil', .4)])])
    assert union.search('query', 1) == [('standard', .9)]


def test_dense_union_rejects_duplicate_documents():
    union = DenseUnion([DenseStub([('same', .9)]), DenseStub([('same', .4)])])
    with pytest.raises(ValueError, match='Duplicate'):
        union.search('query', 2)


@pytest.mark.parametrize('selection', [
    {'selected': [{'id': 'invented', 'query_quote': '실제 질문'}]},
    {'selected': [{'id': 'a', 'query_quote': '없는 인용'}]},
    {'selected': [{'id': 'a', 'query_quote': '실제 질문'}] * 2},
    {'selected': 'a'},
])
def test_invalid_selection_never_becomes_evidence(selection):
    with pytest.raises(ValueError):
        validate_selection(selection, '실제 질문입니다', [{'id': 'a'}])


def test_empty_selection_and_grounded_selection_are_valid():
    assert validate_selection({'selected': []}, '실제 질문입니다', [{'id': 'a'}]) == []
    selection = {'selected': [{'id': 'a', 'query_quote': '실제 질문'}]}
    assert validate_selection(selection, '실제 질문입니다', [{'id': 'a'}]) == selection['selected']
