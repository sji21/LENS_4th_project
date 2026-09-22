"""PATCH-034 단계형 근거 라우팅 회귀 테스트."""

from __future__ import annotations

from src.generation.evidence_routing import (
    classify_question_type,
    primary_evidence_is_sufficient,
    retrieve_staged,
)
from src.retrieval.service import Evidence, RetrievalResult


LAW = Evidence(1, "law-1", "law", "주택임대차보호법 제3조", "법령 본문", 1.0)
CASE = Evidence(1, "case-1", "case", "대법원 2011다49523", "판례 본문", 1.0)
GUIDE = Evidence(1, "guide-1", "guide", "국세청 안내", "기관 안내 본문", 1.0)


class RecordingService:
    def __init__(self, *, laws=(), cases=(), guides=()) -> None:
        self.laws = list(laws)
        self.cases = list(cases)
        self.guides = list(guides)
        self.calls: list[dict[str, int]] = []

    def search(self, question, k_law=5, k_case=5, k_guide=2):
        self.calls.append(
            {"k_law": k_law, "k_case": k_case, "k_guide": k_guide}
        )
        return RetrievalResult(
            question=question,
            laws=self.laws[:k_law],
            cases=self.cases[:k_case],
            guides=self.guides[:k_guide],
        )


class NonCompliantService(RecordingService):
    """검색 건수 인자를 무시하는 대역으로 생성 경계의 방어를 확인한다."""

    def search(self, question, k_law=5, k_case=5, k_guide=2):
        self.calls.append(
            {"k_law": k_law, "k_case": k_case, "k_guide": k_guide}
        )
        return RetrievalResult(
            question=question,
            laws=list(self.laws),
            cases=list(self.cases),
            guides=list(self.guides),
        )


def test_question_type_prefers_explicit_case_request() -> None:
    assert classify_question_type("미납국세 관련 대법원 판례도 있나요?") == "case"


def test_question_type_detects_official_guide_topic() -> None:
    assert classify_question_type("집주인 미납국세는 어디서 확인하나요?") == "guide"


def test_general_lease_question_is_law_type() -> None:
    assert classify_question_type("대항력은 언제부터 생기나요?") == "law"


def test_law_primary_evidence_skips_case_search() -> None:
    service = RecordingService(laws=[LAW], cases=[CASE])

    routed = retrieve_staged(
        service,
        "대항력은 언제부터 생기나요?",
        k_law=3,
        k_case=2,
        k_guide=2,
    )

    assert service.calls == [{"k_law": 3, "k_case": 0, "k_guide": 2}]
    assert routed.result.laws == [LAW]
    assert routed.result.cases == []
    assert routed.route.primary_sufficient is True
    assert routed.route.cases_added is False


def test_guide_primary_evidence_skips_case_search() -> None:
    service = RecordingService(laws=[LAW], cases=[CASE], guides=[GUIDE])

    routed = retrieve_staged(
        service,
        "집주인 미납국세는 어디서 확인하나요?",
        k_law=3,
        k_case=2,
        k_guide=2,
    )

    assert len(service.calls) == 1
    assert routed.result.guides == [GUIDE]
    assert routed.result.cases == []


def test_primary_stage_discards_cases_even_if_service_ignores_zero_limit() -> None:
    service = NonCompliantService(laws=[LAW], cases=[CASE])

    routed = retrieve_staged(
        service,
        "대항력은 언제부터 생기나요?",
        k_law=3,
        k_case=2,
        k_guide=2,
    )

    assert len(service.calls) == 1
    assert routed.result.laws == [LAW]
    assert routed.result.cases == []


def test_explicit_case_request_adds_cases_after_primary_search() -> None:
    service = RecordingService(laws=[LAW], cases=[CASE])

    routed = retrieve_staged(
        service,
        "집주인이 바뀐 경우 대법원 판례는 어떻게 보나요?",
        k_law=3,
        k_case=2,
        k_guide=2,
    )

    assert service.calls == [
        {"k_law": 3, "k_case": 0, "k_guide": 2},
        {"k_law": 0, "k_case": 2, "k_guide": 0},
    ]
    assert routed.result.laws == [LAW]
    assert routed.result.cases == [CASE]
    assert routed.route.cases_added is True


def test_missing_primary_evidence_falls_back_to_cases() -> None:
    service = RecordingService(cases=[CASE])

    routed = retrieve_staged(
        service,
        "임대차 권리 승계는 어떻게 되나요?",
        k_law=3,
        k_case=2,
        k_guide=2,
    )

    assert len(service.calls) == 2
    assert routed.result.laws == []
    assert routed.result.cases == [CASE]
    assert routed.route.primary_sufficient is False


def test_primary_sufficiency_matches_question_type() -> None:
    result = RetrievalResult("q", laws=[LAW], guides=[])
    assert primary_evidence_is_sufficient("law", result) is True
    assert primary_evidence_is_sufficient("guide", result) is False
    assert primary_evidence_is_sufficient("case", result) is False


def test_routing_sees_the_same_guide_topics_as_retrieval() -> None:
    """주택 유형이 상황 정보에만 있어도 경로와 검색이 같은 주제를 본다.

    검색은 질문 전체로 안내 주제를 찾는다. 경로 판정만 현재 요청으로 좁히면
    서식을 실제로 전달하면서 법령 질문으로 분류해 단계형 경로가 어긋난다.
    """
    from src.retrieval.service import detect_guide_topics

    question = (
        "사용자 추가 상황: 지금 국민임대주택에 살고 있습니다.\n"
        "사용자 질문: 재계약할 때 금융정보 제공 동의서를 내야 하나요?"
    )

    assert detect_guide_topics(question)
    assert classify_question_type(question) == "guide"


def test_previous_dialogue_topic_alone_does_not_route_to_cases() -> None:
    """상황 정보를 다시 보더라도 지난 대화의 판례 요청까지 살아나지는 않는다."""
    question = (
        "이전 대화: 관련 판례를 알려 주세요.\n"
        "사용자 질문: 계약 기간은 최소 몇 년인가요?"
    )

    assert classify_question_type(question) == "law"
