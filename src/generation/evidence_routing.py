"""질문 유형과 1차 근거 충분성에 따른 단계형 검색.

일반 주택임대차 질문에 법령과 판례를 항상 함께 넘기면, 법령만으로 충분한
질문에서도 작은 생성 모델이 판례를 억지로 인용할 수 있다. 이 모듈은 법령과
기관 안내를 먼저 검색하고 다음 경우에만 판례를 추가한다.

* 사용자가 판례·판결·법원의 판단을 명시적으로 요청한 경우
* 구체적인 권리 충돌·책임 등 사실관계에 따른 법적 해석을 요청한 경우
* 질문 유형에 맞는 1차 근거를 찾지 못한 경우

질문 유형 판별과 충분성 검사는 결정론적으로 수행한다. 검색 전에 별도 LLM을
호출하면 응답 시간이 늘고 분류 실패가 전체 답변 실패로 이어지기 때문이다.
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Literal, Protocol

from src.retrieval.service import RetrievalResult, detect_guide_topics
from src.retrieval.retrieval_intent import _request_and_facts


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


_LEGAL_OUTCOME = r"효력|유효|무효|인정|대항|승계|책임|청구|공제|거절|거부|권리|의무|보호|순위|기다려|기다리"
_ADMINISTRATIVE_TOPIC = r"서식|양식|신청\s*서|서류|접수|제출|발급|수수료|다운로드|내려받|(?:신청|청구)\s*(?:방법|절차)|신청"


def _only_administrative_request(request: str) -> bool:
    """Do not turn the title of a requested form into a legal-outcome request.

    A separate legal question still takes precedence over its accompanying
    paperwork request, regardless of which one is written first.
    """
    if not re.search(_ADMINISTRATIVE_TOPIC, request):
        return False
    for outcome in re.finditer(_LEGAL_OUTCOME, request):
        tail = re.split(r"[.!?\n;]", request[outcome.end():], 1)[0]
        # The first administrative noun owns the following filing question:
        # '반환 청구 신청서를 어디서 받나요' asks for the form, not entitlement.
        pieces = re.split(_ADMINISTRATIVE_TOPIC, tail, 1)
        legal_tail = pieces[0]
        if len(pieces) > 1:
            legal_tail = re.sub(r"(?:어떻게|어디(?:서|에)?|언제|어떤|무슨)\s*$", "", legal_tail)
        if re.search(
            r"(?:있|없|되|돼|하|한|인|받|물|지|야|달라|밀리|생기|사라지|잃|지켜)"
            r"[^.!?\n;]{0,18}(?:나요|까요|는지|은지|인지|할\s*수|해야|하나요)"
            r"|가능(?:한가요|합니까|한지|할까요|하나요)"
            r"|어떻게|왜|설명|판단|인정\s*여부|^\s*(?:한지|할지|될지|인지)", legal_tail,
        ):
            return False
    return True


def requires_case_interpretation(question: str) -> bool:
    """Recognize a requested legal outcome under competing concrete facts.

    Filing steps, statutory counts/deadlines and general definitions alone do
    not require case law. A dispute word alone is likewise insufficient.
    """
    request, facts = _request_and_facts(question or "")
    if re.search(r"(?:판례|판결|재판|법원)(?:는|은|를|을)?[^.!?\n;]{0,12}(?:제외|말고|묻지|필요\s*없)", request):
        return False
    outcome = re.search(_LEGAL_OUTCOME, request)
    if not outcome or _only_administrative_request(request):
        return False
    scope = request + "\n" + facts
    if (re.search(r"뜻|정의|의미|무엇인가|뭔가|개념", request)
            and not re.search(r"경우|했|됐|았|었|인데|지만|없이|몰래|바뀌|바뀐|넘겼|받은", scope)):
        return False
    competing = (
        r"제3자|제삼자|양도담보|담보\s*목적|이전\s*임대인|새\s*소유자|근저당|다른\s*채권자",
        r"보험금|손해배상|지체책임|이행제공|선정당사자|거소신고|체류지|외국인등록",
        r"(?:주소|지번).{0,25}(?:바뀌|변경|분할|불일치)|(?:형식|실제\s*거주|거주\s*의사|꾸며).{0,30}(?:전입|임대차|보호|대항)",
        r"(?:점유|인도).{0,30}(?:인정|물리|사실상)|(?:열쇠|출입수단).{0,30}(?:넘|반납|지배)",
        r"(?:보증금).{0,35}(?:못\s*받|안\s*주|주지\s*않|반환하지|반환\s*거부)|(?:보증금).{0,25}(?:와야|구해야)",
        r"(?:실거주|들어와\s*산|직접\s*거주).{0,35}(?:거절|거부)|(?:갱신).{0,35}(?:실거주|들어와\s*산|직접\s*거주)",
        r"(?:양도|소유권).{0,30}(?:금지|해제)|(?:월세|차임).{0,25}(?:연체|공제)|(?:안전|도난).{0,20}(?:보호|의무)",
    )
    return any(re.search(pattern, scope) for pattern in competing)


def classify_question_type(question: str) -> QuestionType:
    """판례·사실관계 해석 요청, 기관 안내, 일반 법령 질문 순으로 분류한다."""

    request, _ = _request_and_facts(question or "")
    explicit = any(signal in request for signal in _CASE_REQUEST_SIGNALS)
    excluded = re.search(r"(?:판례|판결|재판|법원)(?:는|은|를|을)?[^.!?\n;]{0,12}(?:제외|말고|묻지|필요\s*없)", request)
    if (explicit and not excluded) or requires_case_interpretation(question):
        return "case"
    # 안내 주제는 검색과 같은 입력으로 판별한다. `_search_guides` 는 질문 전체를
    # 보므로 여기서만 상황 정보를 떼면, 주택 유형이 상황에만 적힌 서식 요청에서
    # 검색은 서식을 전달하는데 경로는 법령 질문으로 판정한다.
    if detect_guide_topics(question or ""):
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
    return bool(result.laws or result.civil_laws)


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
        civil_laws=list(primary_raw.civil_laws),
        civil_topics=primary_raw.civil_topics,
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
        civil_laws=list(primary.civil_laws),
        civil_topics=primary.civil_topics,
        cases=list(secondary_raw.cases),
        guides=list(primary.guides),
    )
    return RoutedRetrieval(
        result=selected,
        route=EvidenceRoute(question_type, sufficient, bool(secondary_raw.cases)),
    )
