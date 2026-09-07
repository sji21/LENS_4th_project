"""PATCH-004 Retriever 질의와 LangGraph 초기 상태 테스트."""

from __future__ import annotations

from datetime import date

from src.document_check.extraction_models import PageExtraction
from src.document_check.rag_handoff import build_graph_state, build_rag_queries
from src.document_check.risk_rules import detect_risk_signals


def signals_for(text: str):
    page = PageExtraction(1, text, "embedded_text", len(text))
    return detect_risk_signals((page,), today=date(2026, 8, 26))


def test_builds_retriever_query_for_each_detected_signal() -> None:
    signals = signals_for("갑구 신탁 을구 근저당권 설정")

    queries = build_rag_queries(signals)

    assert any("신탁" in query for query in queries)
    assert any("근저당권" in query for query in queries)
    assert any("소유자 확인" in query for query in queries)


def test_unreadable_document_routes_to_abstain() -> None:
    state = build_graph_state("abstain", ())

    assert state["next_node"] == "abstain"
    assert state["risk_signal_ids"] == []


def test_readable_document_routes_to_retriever_without_llm_decision() -> None:
    signals = signals_for("을구 임차권 설정")

    state = build_graph_state("review_required", signals)

    assert state["next_node"] == "retrieve"
    assert "tenant_registration" in state["risk_signal_ids"]
    assert state["retrieval_queries"]
