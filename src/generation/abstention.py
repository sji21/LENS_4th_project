"""질문을 ANSWER 후보와 REFUSE 대상으로 분류한다.

명백한 개별 계약 안전성 판정이나 시세 조회는 LLM 호출 전에 코드로 즉시
차단한다. 그 밖의 질문은 런타임에서 semantic judge를 연결하면 주택임대차
서비스 범위와 질문 의도를 LLM이 한 번 더 판정할 수 있다.

오탈자나 구어체만으로 질문을 거절하지 않는다. 의미를 알아볼 수 있으면 후속
Retrieval과 답변 생성으로 넘기고, 실제 LLM 호출 자체는 이 모듈이 담당하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Callable, Literal


ScopeReason = Literal[
    "in_scope",
    "contract_safety_verdict",
    "market_price_lookup",
    "semantic_out_of_scope",
]
DecisionSource = Literal[
    "deterministic",
    "semantic_judge",
    "default_allow",
]
ScopeJudge = Callable[[str], bool]


@dataclass(frozen=True)
class ScopeDecision:
    """질문 범위 판정 결과."""

    out_of_scope: bool
    reason: ScopeReason
    source: DecisionSource
    needs_semantic_review: bool = False


SCOPE_JUDGE_SYSTEM = """당신은 LENS의 질문 범위 분류기입니다.

LENS는 대한민국 주택임대차의 권리·절차·법령·판례·공식 기관 안내를
근거로 설명하는 서비스입니다.

다음 원칙으로 질문을 분류하십시오.
- ALLOW: 주택임대차의 권리, 계약·갱신, 대항력, 우선변제, 보증금 회수,
  임차권등기명령, 미납국세 확인, 보증 제도 등 서비스가 근거 문서로 설명할 수 있는 질문
- REFUSE: 특정 집이나 계약의 안전·위험 여부를 최종 판정해 달라는 요청,
  부동산 시세·투자 추천, 개인정보 조회, 다른 도메인의 질문
- 오탈자, 띄어쓰기 오류, 짧은 구어체가 있어도 의도를 이해할 수 있으면 그것만으로
  REFUSE하지 마십시오.
- 질문을 새 사실로 보충하거나 법률 결론을 만들지 마십시오.

출력은 ALLOW 또는 REFUSE 중 하나만 사용하십시오."""


_PROPERTY_CUES = (
    "이 집",
    "이집",
    "이 주택",
    "이주택",
    "이 아파트",
    "이아파트",
    "이 매물",
    "이매물",
    "이 원룸",
    "이원룸",
    "이 빌라",
    "이빌라",
    "이 오피스텔",
    "이오피스텔",
    "이 계약",
    "이계약",
    "계약하려는 집",
    "보려는 집",
)

_STRONG_VERDICT_CUES = (
    "안전할까",
    "안전할까요",
    "안전한가",
    "안전한지",
    "위험할까",
    "위험할까요",
    "위험한가",
    "위험한지",
    "계약해도 될",
    "계약해도 되",
    "계약하면 안",
    "계약하지 말",
)

_AMBIGUOUS_VERDICT_CUES = (
    "괜찮을까",
    "괜찮을까요",
    "괜찮나요",
    "문제 없",
    "문제없",
    "믿어도",
    "추천해",
    "추천하",
    "좋은 계약",
    "나쁜 계약",
)

_INFORMATIONAL_CUES = (
    "확인할 사항",
    "확인사항",
    "무엇을 확인",
    "뭘 확인",
    "어떤 점",
    "주의사항",
    "위험요소",
    "위험 요소",
    "체크리스트",
    "체크 리스트",
    "법적 근거",
    "절차",
    "방법",
)

_MARKET_TERMS = (
    "시세",
    "실거래가",
    "매매가",
    "전세가격",
    "전세 가격",
    "시장가격",
    "시장 가격",
)

_MARKET_LOOKUP_CUES = (
    "얼마",
    "조회",
    "찾아",
    "알려",
)

_IN_SCOPE_CUES = (
    "전입신고",
    "확정일자",
    "대항력",
    "우선변제",
    "최우선변제",
    "소액임차인",
    "임차권등기",
    "등기부",
    "등기사항증명서",
    "등기사항",
    "임차인",
    "임대인",
    "임대차",
    "집주인",
    "세입자",
    "보증금",
    "전세",
    "월세",
    "반전세",
    "월차임",
    "미납국세",
)


_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)

# "전세가"는 가격 명사(전세가가 얼마예요?)이기도 하고 "전세가 끝난 뒤"의
# '전세+주격 조사 가'이기도 하다. 단순 부분문자열로 보면 보증금 반환 질문까지
# 시세 조회로 오탐하므로, 가격 조회 표현과 가까이 붙어 있을 때만 가격 명사로 본다.
_JEONSE_PRICE_PATTERNS = (
    re.compile(
        r"전세가(?:격)?\s*(?:가|는|를|도)?\s*"
        r"(?:얼마|조회|찾아|알려|어때|어떻게)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:현재|요즘|지금)\s+[^\n.!?]{0,20}?전세가(?:격)?"
        r"(?:\s*(?:가|는|를|도))?\s*"
        r"(?:얼마|조회|찾아|알려|어때|어떻게|어느\s*정도|수준)",
        re.IGNORECASE,
    ),
)

# "시세가 어떻게 되나요?"처럼 조회 동사가 명시되지 않은 자연어 질문도
# 가격 명사 바로 뒤의 표현만 본다. 문장 뒤쪽의 법률 질문(예: 우선변제는
# 어떻게 되나요?)까지 끌어와 시세 조회로 오탐하지 않도록 거리를 짧게 제한한다.
_MARKET_DESCRIPTION_PATTERN = re.compile(
    r"(?:시세|실거래가|매매가|전세가격|전세\s+가격|시장가격|시장\s+가격)"
    r"\s*(?:가|는|를|도)?\s*"
    r"(?:어때|어떻게|어느\s*정도|수준)",
    re.IGNORECASE,
)


def build_scope_judge_prompt(question: str) -> str:
    """LLM 범위 분류기에 넘길 사용자 메시지를 만든다."""

    return f"[질문]\n{question.strip()}"


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return _SPACE_RE.sub(" ", normalized).strip()


def _compact(text: str) -> str:
    return _NON_WORD_RE.sub("", _normalize(text))


def _contains_any(text: str, cues: tuple[str, ...]) -> bool:
    normalized = _normalize(text)
    compact = _compact(text)
    return any(
        _normalize(cue) in normalized or _compact(cue) in compact
        for cue in cues
    )


def _is_specific_property_question(question: str) -> bool:
    return _contains_any(question, _PROPERTY_CUES)


def _is_informational_question(question: str) -> bool:
    return _contains_any(question, _INFORMATIONAL_CUES)


def _is_direct_contract_verdict(question: str) -> bool:
    return (
        _is_specific_property_question(question)
        and _contains_any(question, _STRONG_VERDICT_CUES)
        and not _is_informational_question(question)
    )


def _is_market_price_lookup(question: str) -> bool:
    if (
        _contains_any(question, _MARKET_TERMS)
        and _contains_any(question, _MARKET_LOOKUP_CUES)
    ):
        return True

    normalized = _normalize(question)
    if _MARKET_DESCRIPTION_PATTERN.search(normalized) is not None:
        return True
    return any(pattern.search(normalized) is not None for pattern in _JEONSE_PRICE_PATTERNS)


def _looks_clearly_in_scope(question: str) -> bool:
    return _contains_any(question, _IN_SCOPE_CUES)


def _needs_semantic_scope_review(question: str) -> bool:
    ambiguous_property_verdict = (
        _is_specific_property_question(question)
        and _contains_any(question, _AMBIGUOUS_VERDICT_CUES)
        and not _is_informational_question(question)
    )
    # 명백한 임대차 질문은 Qwen scope 판정을 생략한다. 반대로 임대차 신호가
    # 없는 질문은 다른 도메인일 수 있으므로 semantic judge에 맡긴다.
    return ambiguous_property_verdict or not _looks_clearly_in_scope(question)


def classify_scope(
    question: str,
    semantic_judge: ScopeJudge | None = None,
) -> ScopeDecision:
    """질문의 서비스 범위를 보수적으로 판정한다.

    명백한 금지 요청은 코드가 즉시 거절한다. 명백한 임대차 질문은 semantic judge를
    생략하고, 범위가 애매하거나 임대차 신호가 없는 질문만 LLM이 한 번 더 본다.
    judge가 없거나 실패하면 정상 질문을 과잉 거절하지 않도록 통과시킨다.
    """

    if _is_direct_contract_verdict(question):
        return ScopeDecision(
            out_of_scope=True,
            reason="contract_safety_verdict",
            source="deterministic",
        )

    if _is_market_price_lookup(question):
        return ScopeDecision(
            out_of_scope=True,
            reason="market_price_lookup",
            source="deterministic",
        )

    needs_review = _needs_semantic_scope_review(question)

    if semantic_judge is None or not needs_review:
        return ScopeDecision(
            out_of_scope=False,
            reason="in_scope",
            source="default_allow" if needs_review else "deterministic",
            needs_semantic_review=needs_review,
        )

    try:
        out_of_scope = bool(semantic_judge(question))
    except Exception:
        return ScopeDecision(
            out_of_scope=False,
            reason="in_scope",
            source="default_allow",
            needs_semantic_review=True,
        )

    return ScopeDecision(
        out_of_scope=out_of_scope,
        reason="semantic_out_of_scope" if out_of_scope else "in_scope",
        source="semantic_judge",
    )


def is_out_of_scope(
    question: str,
    semantic_judge: ScopeJudge | None = None,
) -> bool:
    """``chain.answer_question(refuse_check=...)`` 호환용 불리언 진입점."""

    return classify_scope(
        question,
        semantic_judge=semantic_judge,
    ).out_of_scope
