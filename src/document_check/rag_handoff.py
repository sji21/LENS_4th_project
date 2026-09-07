"""등기 점검 결과를 후속 LangChain Retriever와 LangGraph 상태로 변환한다."""

from __future__ import annotations

from typing import Literal, TypedDict

from .models import RiskSignal


class RegistryGraphState(TypedDict):
    document_status: str
    risk_signal_ids: list[str]
    retrieval_queries: list[str]
    next_node: Literal["retrieve", "abstain"]


def build_rag_queries(signals: tuple[RiskSignal, ...]) -> tuple[str, ...]:
    """공식 법령·정부 가이드 Retriever에 전달할 검색 질의를 만든다."""

    queries = []
    for signal in signals:
        query = f"전세계약 전 등기사항증명서 {signal.matched_keyword} 확인사항 관련 법령 정부 가이드"
        if query not in queries:
            queries.append(query)
    queries.extend(
        query
        for query in (
            "전세계약 전 등기부등본 소유자 확인 정부 가이드",
            "전세계약 잔금 전 등기부등본 재확인 정부 가이드",
        )
        if query not in queries
    )
    return tuple(queries)


def build_graph_state(status: str, signals: tuple[RiskSignal, ...]) -> RegistryGraphState:
    """OCR 판독 실패만 즉시 보류하고 나머지는 공식 근거 검색으로 보낸다."""

    queries = build_rag_queries(signals)
    return RegistryGraphState(
        document_status=status,
        risk_signal_ids=[signal.rule_id for signal in signals],
        retrieval_queries=list(queries),
        next_node="abstain" if status == "abstain" else "retrieve",
    )
