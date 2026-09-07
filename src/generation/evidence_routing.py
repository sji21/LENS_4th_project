"""질문 유형과 1차 근거 충분성에 따른 단계형 검색.

일반 주택임대차 질문에 법령과 판례를 항상 함께 넘기면, 법령만으로 충분한
질문에서도 작은 생성 모델이 판례를 억지로 인용할 수 있다. 이 모듈은 법령과
기관 안내를 먼저 검색하고 다음 경우에만 판례를 추가한다.

* 사용자가 판례·판결·법원의 판단을 명시적으로 요청한 경우
* 질문 유형에 맞는 1차 근거를 찾지 못한 경우

질문 유형 판별과 충분성 검사는 결정론적으로 수행한다. 검색 전에 별도 LLM을
호출하면 응답 시간이 늘고 분류 실패가 전체 답변 실패로 이어지기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from src.retrieval.service import RetrievalResult, detect_guide_topics


QuestionType = Literal["law", "guide", "case"]

_CASE_REQUEST_SIGNALS = (
    "판례",
    "판결",
    "재판",
    "법원",
    "대법원",
    "고등법원",
    "지방법원",
    "사건번호",
    "결정례",
)


class SearchService(Protocol):
    def search(
        self,
        question: str,
        k_law: int = 5,
        k_case: int = 5,
        k_guide: int = 2,
    ) -> RetrievalResult: ...


@dataclass(frozen=True)
class EvidenceRoute:
    question_type: QuestionType
    primary_sufficient: bool
    cases_added: bool


@dataclass(frozen=True)
class RoutedRetrieval:
    result: RetrievalResult
    route: EvidenceRoute


def classify_question_type(question: str) -> QuestionType:
    """판례 직접 요청, 기관 안내 주제, 일반 법령 질문 순으로 분류한다."""

    normalized = " ".join((question or "").split())
    if any(signal in normalized for signal in _CASE_REQUEST_SIGNALS):
        return "case"
    if detect_guide_topics(normalized):
        return "guide"
    return "law"


def primary_evidence_is_sufficient(
    question_type: QuestionType,
    result: RetrievalResult,
) -> bool:
    """질문 유형에 직접 대응하는 1차 근거가 반환됐는지 확인한다.

    검색 점수는 BM25와 dense 순위를 RRF로 합친 값이라 법령·안내 묶음 사이의
    절대 임계값으로 비교할 수 없다. 따라서 현재 코퍼스에서 검증 가능한 기준인
    '질문 유형에 해당하는 공식 근거가 한 건 이상 있는가'만 사용한다.
    """

    if question_type == "case":
        return False
    if question_type == "guide":
        return bool(result.guides)
    return bool(result.laws)


def retrieve_staged(
    service: SearchService,
    question: str,
    *,
    k_law: int,
    k_case: int,
    k_guide: int,
) -> RoutedRetrieval:
    """법령·안내를 먼저 찾고 필요할 때만 판례를 더한 최종 근거를 반환한다."""

    question_type = classify_question_type(question)
    primary_raw = service.search(
        question,
        k_law=k_law,
        k_case=0,
        k_guide=k_guide,
    )
    # 호출자가 k_case=0 계약을 잘못 구현해도 1차 결과에 판례를 통과시키지 않는다.
    primary = RetrievalResult(
        question=question,
        laws=list(primary_raw.laws),
        guides=list(primary_raw.guides),
    )
    sufficient = primary_evidence_is_sufficient(question_type, primary)
    should_add_cases = not sufficient and k_case > 0

    if not should_add_cases:
        return RoutedRetrieval(
            result=primary,
            route=EvidenceRoute(question_type, sufficient, False),
        )

    secondary_raw = service.search(
        question,
        k_law=0,
        k_case=k_case,
        k_guide=0,
    )
    selected = RetrievalResult(
        question=question,
        laws=list(primary.laws),
        cases=list(secondary_raw.cases),
        guides=list(primary.guides),
    )
    return RoutedRetrieval(
        result=selected,
        route=EvidenceRoute(question_type, sufficient, bool(secondary_raw.cases)),
    )
