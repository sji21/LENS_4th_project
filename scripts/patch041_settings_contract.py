"""Frozen execution settings contract for the PATCH-041 retrieval measurement.

Any intentional retrieval retuning requires review and an explicit update to this
contract before a new PATCH-041-compatible capture can be produced.
"""

from __future__ import annotations

import json


EXPECTED_SETTINGS = {
    "search_k": {"k_law": 5, "k_case": 5, "k_guide": 2, "k_civil": 3},
    "corpora": {
        "law": {
            "name": "법령",
            "doc_types": ["law", "decree", "rule"],
            "bm25_b": 0.75,
            "expand_weight": 1.0,
            "bm25_weight": 1.0,
            "dense_weight": 1.0,
            "rrf_k": 5,
            "query_expander": "src.retrieval.context_policy.final_law_terms",
            "exclude_titles": [],
            "status": "current",
            "include_ids": [],
            "retriever": {
                "rrf_k": 5,
                "depth": 20,
                "bm25": {
                    "kind": "partitioned_raw_score_pool",
                    "score_weight": 1.0,
                    "procedure_titles": [
                        "주민등록법",
                        "국세징수법",
                        "국세징수법 시행령",
                        "지방세징수법",
                        "지방세징수법 시행령",
                    ],
                    "partitions": {
                        "core": {"k1": 1.5, "b": 0.75, "char_ngram": 2},
                        "procedure": {"k1": 1.5, "b": 0.75, "char_ngram": 2},
                    },
                },
                "members": [
                    {"name": "bm25_context", "weight": 1, "expand_weight": 1.0},
                    {"name": "dense_context", "weight": 1, "expand_weight": 0.0},
                ],
            },
        },
        "case": {
            "name": "판례",
            "doc_types": ["case"],
            "bm25_b": 0.75,
            "expand_weight": 1.0,
            "bm25_weight": 1.0,
            "dense_weight": 1.0,
            "rrf_k": 60,
            "query_expander": "src.retrieval.terms.expand",
            "exclude_titles": [],
            "status": "current",
            "include_ids": [],
            "retriever": {
                "rrf_k": 60,
                "depth": 20,
                "bm25": {"k1": 1.5, "b": 0.75, "char_ngram": 2},
            },
        },
        "guide": {
            "name": "안내",
            "doc_types": ["guide"],
            "bm25_b": 0.75,
            "expand_weight": 1.0,
            "bm25_weight": 1.0,
            "dense_weight": 1.0,
            "rrf_k": 60,
            "query_expander": "src.retrieval.terms.expand",
            "exclude_titles": [],
            "status": "current",
            "include_ids": [],
            "retriever": {
                "rrf_k": 60,
                "depth": 20,
                "bm25": {"k1": 1.5, "b": 0.75, "char_ngram": 2},
            },
        },
        "civil": {
            "name": "민법",
            "doc_types": ["law", "decree", "rule"],
            "bm25_b": 0.75,
            "expand_weight": 1.0,
            "bm25_weight": 1.0,
            "dense_weight": 1.0,
            "rrf_k": 5,
            "query_expander": "src.retrieval.expanded.civil_terms",
            "exclude_titles": [],
            "status": "current",
            "include_ids": [
                "민법-제105조",
                "민법-제111조",
                "민법-제113조",
                "민법-제114조",
                "민법-제118조",
                "민법-제147조",
                "민법-제186조",
                "민법-제187조",
                "민법-제265조",
                "민법-제357조",
                "민법-제390조",
                "민법-제393조",
                "민법-제470조",
                "민법-제536조",
                "민법-제543조",
                "민법-제548조",
                "민법-제615조",
                "민법-제623조",
                "민법-제626조",
                "민법-제627조",
                "민법-제629조",
                "민법-제632조",
                "민법-제634조",
                "민법-제636조",
                "민법-제640조",
                "민법-제654조",
            ],
            "retriever": {
                "rrf_k": 5,
                "depth": 26,
                "bm25": {"k1": 1.5, "b": 0.75, "char_ngram": 2},
                "members": [
                    {"name": "bm25_context", "weight": 1, "expand_weight": 1.0},
                    {"name": "dense_context", "weight": 1, "expand_weight": 0.0},
                ],
            },
            "seed_retriever": {
                "rrf_k": 5,
                "depth": 20,
                "bm25": {"k1": 1.5, "b": 0.75, "char_ngram": 2},
            },
        },
    },
    "civil_selection": {
        "policy": "expanded-laws-record-v1",
        "method": "same-edition reference pair or topic-seeded RRF with dense tail",
        "request_embedding_cache": True,
    },
    "profile": "expanded-laws-record-v1",
}


def _compare(actual, expected, path: str) -> None:
    if type(actual) is not type(expected):
        raise ValueError(
            f"Retrieval settings contract mismatch at {path}: "
            f"expected {type(expected).__name__}, got {type(actual).__name__}"
        )
    if isinstance(expected, dict):
        missing = expected.keys() - actual.keys()
        extra = actual.keys() - expected.keys()
        if missing or extra:
            raise ValueError(
                f"Retrieval settings contract mismatch at {path}: "
                f"missing={sorted(missing)!r}, extra={sorted(extra)!r}"
            )
        for key, value in expected.items():
            _compare(actual[key], value, f"{path}.{key}")
        return
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise ValueError(
                f"Retrieval settings contract mismatch at {path}: "
                f"expected {len(expected)} items, got {len(actual)}"
            )
        for index, value in enumerate(expected):
            _compare(actual[index], value, f"{path}[{index}]")
        return
    if actual != expected:
        raise ValueError(
            f"Retrieval settings contract mismatch at {path}: "
            f"expected {expected!r}, got {actual!r}"
        )


def validate_settings(actual) -> None:
    """Reject any omission, addition, type drift, reordering, or value drift."""
    _compare(actual, EXPECTED_SETTINGS, "settings")


def snapshot_settings(actual) -> dict:
    """Convert the live Python settings to their audited JSON representation."""
    snapshot = json.loads(json.dumps(actual, ensure_ascii=False))
    validate_settings(snapshot)
    return snapshot
