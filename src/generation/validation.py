"""LLM 원문이 검색 근거와 안전 정책을 벗어나지 않았는지 검증한다.

출처 provenance는 citation.py를 재사용한다. 직접 인용문, 금액·비율·기간·날짜,
조문의 항 번호, 금액의 법적 역할과 명백한 안전성 단정은 코드로 먼저 검사한다.
결정론적 검증을 통과한 답변은 런타임에서 semantic judge를 연결해 근거 충실도와
질문 적합성을 LLM으로 한 번 더 검증할 수 있다. 실제 LLM 호출은 이 모듈의 책임이 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import Callable, Literal

from src.generation.citation import audit_citations
from src.generation.models import Answer
from src.retrieval.service import Evidence


ValidationKind = Literal[
    "citation",
    "quote",
    "value",
    "condition",
    "amount_role",
    "paragraph",
    "safety_verdict",
    "semantic",
]


@dataclass(frozen=True)
class ValidationIssue:
    kind: ValidationKind
    text: str
    detail: str
    evidence_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues

    def by_kind(self, kind: ValidationKind) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.kind == kind)


@dataclass(frozen=True)
class SemanticJudgement:
    """외부 semantic judge가 반환하는 최소 계약."""

    supported: bool
    detail: str = ""


SemanticJudge = Callable[
    [str, str, tuple[Evidence, ...]],
    SemanticJudgement | bool,
]


SEMANTIC_JUDGE_SYSTEM = """당신은 LENS의 답변 검증기입니다.

사용자 질문, 검색 근거, 생성 답변을 비교해 답변을 사용자에게 보내도 되는지
판정하십시오. 다음 중 하나라도 해당하면 FAIL입니다.
- 답변의 핵심 결론이 검색 근거로 뒷받침되지 않음
- 질문의 핵심 요구에 답하지 않거나 조건을 빠뜨려 의미가 달라짐
- 검색 근거의 시점·전후관계, 부정·예외, 주체를 바꾸어 의미가 달라짐
  (예: "그 다음 날부터"를 "당일부터"라고 바꿈)
- 법령·판례·기관 안내의 성격을 뒤섞음
- 특정 계약의 안전·위험 여부를 최종 판정함

검색 근거에 없는 법 지식을 새로 보태지 마십시오. 결정론적 출처·숫자 검증은
이미 앞 단계에서 수행됐으므로 의미와 질문 적합성에 집중하십시오.
검색 결과에 포함됐더라도 모든 문서를 답변에 사용할 필요는 없습니다. 질문에 직접
답하는 근거 하나 이상이 답변을 뒷받침하고 답변이 다른 근거와 모순되지 않으면 PASS입니다.
특히 보증금의 대상·요건을 정한 조문과 실제 우선변제 금액을 정한 조문처럼 서로 다른
역할의 자료가 함께 검색된 경우, 질문이 묻는 한쪽을 정확히 답했다는 이유만으로
다른 쪽을 설명하지 않았다고 FAIL로 판정하지 마십시오.

`[업로드 문서]`는 사용자가 제공한 계약서·등기사항증명서의 OCR 근거입니다.
- 보증금, 특약, 당사자, 등기 항목처럼 문서에 적힌 사실을 묻는 질문은 업로드 문서
  근거만으로 답해도 됩니다. 공식 법령을 인용하지 않았다는 이유로 FAIL하지 마십시오.
- 문서 분석 요청에서 발견된 권리나 문구와 추가 확인사항을 설명하는 것은 허용됩니다.
  계약이 안전하다거나 위험하다고 최종 단정한 경우에만 FAIL하십시오.
- 질문과 무관한 공식 검색 결과를 답변에서 사용하지 않은 것은 실패 사유가 아닙니다.

출력은 첫 줄에 PASS 또는 FAIL만 쓰십시오. 이유 설명이나 다른 문장은 쓰지 마십시오."""


DOCUMENT_SEMANTIC_JUDGE_SYSTEM = """당신은 업로드 문서 답변 검증기입니다.

질문, 업로드 문서 OCR 근거, 생성 답변을 비교하십시오.
- 답변의 금액·날짜·당사자·특약·등기 항목이 OCR 근거에 있고 질문에 답하면 PASS입니다.
- OCR 문구를 의미가 같게 짧게 정리한 것은 허용합니다.
- 등기에서 확인된 근저당권·압류·가압류·신탁·임차권 등을 주의해서 확인하라고 설명한 것은 허용합니다.
- 문서에 없는 법령·판례·금액·사실을 추가하거나 계약의 안전·위험을 최종 판정하면 FAIL입니다.
- 질문에서 요청한 핵심 항목을 빠뜨리면 FAIL입니다.

출력은 PASS 또는 FAIL 한 단어만 쓰십시오."""


def build_semantic_judge_prompt(answer: Answer) -> str:
    """사후 LLM 검증기에 넘길 사용자 메시지를 만든다."""

    evidence_blocks = []
    for evidence in answer.evidences:
        evidence_blocks.append(
            f"[{evidence.doc_type}] {evidence.citation}\n{evidence.text}"
        )
    for evidence in answer.document_evidences:
        evidence_blocks.append(
            "[업로드 문서] "
            f"{evidence.filename} {evidence.page_number}쪽\n{evidence.text}"
        )
    context = "\n\n".join(evidence_blocks) or "검색 근거 없음"
    return (
        f"[질문]\n{answer.question}\n\n"
        f"[검색 근거]\n{context}\n\n"
        f"[생성 답변]\n{answer.raw_text}"
    )


_SEMANTIC_CONDITION_CUES = (
    "다음 날",
    "다음날",
    "당일",
    "이전",
    "이후",
    "전까지",
    "후부터",
    "경우에는",
    "경우에만",
    "다만",
    "예외",
    "제외",
    "하지 않",
    "하지 아니",
    "할 수 없",
    "받지 못",
)

_SEMANTIC_QUESTION_CUES = (
    "언제",
    "어떻게",
    "누가",
    "얼마",
    "몇 번",
    "몇번",
    "몇 프로",
    "퍼센트",
    "효력",
    "권리",
    "요건",
    "조건",
    "필요",
    "신청",
    "부담",
    "계산",
    "보호",
    "종료",
    "갱신",
    "거절",
    "돌려받",
    "받을 수",
    "할 수",
    "해야",
)


def requires_semantic_validation(answer: Answer) -> bool:
    """결정론적 검사만으로 의미 변형 위험을 충분히 낮출 수 없는 답변인지 판정한다.

    숫자 자체와 출처 존재 여부는 코드가 검사하지만, 여러 근거의 관계나 판례·기관
    안내의 법적 성격, 조건·예외의 의미는 작은 표현 차이로도 달라질 수 있어 Qwen이
    한 번 더 확인한다. 단일 법령의 단순 설명만 의미 검증을 생략한다.
    """

    if answer.cases or answer.guides:
        return True

    citation_audit = audit_citations(answer)
    supported_mentions = {
        (mention.kind, mention.text)
        for mention in citation_audit.mentions
        if mention.supported
    }
    if len(supported_mentions) > 1:
        return True

    if _extract_values(answer.raw_text):
        return True

    normalized = unicodedata.normalize("NFKC", answer.raw_text or "").casefold()
    if any(cue in normalized for cue in _SEMANTIC_CONDITION_CUES):
        return True

    question = unicodedata.normalize("NFKC", answer.question or "").casefold()
    return any(cue in question for cue in _SEMANTIC_QUESTION_CUES)


@dataclass(frozen=True)
class _ValueMention:
    kind: str
    raw: str
    canonical: str
    start: int
    end: int


# 금액 한 토막의 길이를 제한한다. 등기 문서관리번호처럼 수십 자리인 숫자열을
# 금액 후보로 끝없이 분할해 보지 않게 하면서, 18자리 원 단위와 소수 표기는 받는다.
_MONEY_NUMBER = r"(?:\d{1,3}(?:,\d{3}){1,5}|\d{1,18})(?:\.\d{1,4})?"
_MONEY_RE = re.compile(
    rf"(?<![\d,])(?P<value>(?=\d)"
    rf"(?:{_MONEY_NUMBER}\s*억\s*)?"
    rf"(?:{_MONEY_NUMBER}\s*천\s*)?"
    rf"(?:{_MONEY_NUMBER}\s*백\s*)?"
    rf"(?:{_MONEY_NUMBER}\s*십\s*)?"
    rf"(?:{_MONEY_NUMBER}\s*만\s*)?"
    rf"(?:{_MONEY_NUMBER}\s*)?원)"
)

_PERCENT_RE = re.compile(
    r"(?<![\d.])(?P<number>\d{1,6}(?:\.\d{1,4})?)\s*"
    r"(?P<unit>%|퍼센트|할)"
)

_PERIOD_RE = re.compile(
    r"(?<!\d)(?P<number>\d{1,6})\s*(?P<unit>일|개월|년)"
)

_FRACTION_RE = re.compile(
    r"(?<!\d)(?P<denominator>\d{1,6})\s*분의\s*"
    r"(?P<numerator>\d{1,6})(?!\d)"
)

_DATE_RE = re.compile(
    r"(?P<year>(?:19|20)\d{2})\s*(?:년|[./-])\s*"
    r"(?P<month>\d{1,2})\s*(?:월|[./-])\s*"
    r"(?P<day>\d{1,2})\s*(?:일)?"
)

_QUOTE_RE = re.compile(
    r'"(?P<double>[^"\n]{8,})"'
    r"|“(?P<curly>[^”\n]{8,})”"
    r"|‘(?P<single>[^’\n]{8,})’"
)

_PARAGRAPH_RE = re.compile(
    r"(?P<article>제\s*\d+\s*조(?:\s*의\s*\d+)?)\s*"
    r"제\s*(?P<paragraph>\d+)\s*항"
)

_LAW_REFERENCE_RE = re.compile(
    r"[가-힣A-Za-z0-9· ]+법(?:\s*시행령|\s*시행규칙)?\s*"
    r"제\s*\d+\s*조(?:\s*의\s*\d+)?"
)

_ELIGIBILITY_PATTERNS = (
    re.compile(r"우선변제를\s*받을\s*임차인"),
    re.compile(r"임차인의\s*(?:범위|대상|요건)"),
    re.compile(r"대상.{0,10}임차인"),
    re.compile(r"보증금.{0,20}이하.{0,12}임차인"),
    re.compile(r"대상.{0,10}보증금"),
    re.compile(r"보증금.{0,10}상한"),
)

_PAYOUT_PATTERNS = (
    re.compile(r"보증금\s*중\s*일정액"),
    re.compile(r"최우선변제(?:액|금액)?"),
    re.compile(r"우선변제(?:액|금액)"),
    re.compile(r"변제받(?:을|는).{0,12}(?:금액|보증금)"),
)

_SOURCE_QUOTE_SIGNS = (
    "라고 규정",
    "라고 명시",
    "조문",
    "판결",
    "안내에 따르면",
    "안내에 의하면",
    "법에 따르면",
)

_SAFETY_VERDICT_PATTERNS = (
    re.compile(
        r"(?:이\s*)?(?:집|주택|아파트|매물|계약).{0,30}"
        r"(?:안전합니다|안전해요|안전합니다만|위험합니다|"
        r"위험하지\s*않습니다|문제없습니다)"
    ),
    re.compile(
        r"(?:계약해도|계약하셔도)\s*"
        r"(?:됩니다|괜찮습니다|안전합니다)"
    ),
    re.compile(
        r"(?:계약하지\s*마세요|계약하면\s*안\s*됩니다|"
        r"계약하지\s*않는\s*게\s*좋습니다)"
    ),
)

_LAW_TYPES = frozenset({"law", "decree", "rule"})


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", _normalize(text), flags=re.UNICODE)


def _parse_small_number(text: str) -> int:
    remaining = text.replace(",", "").replace(" ", "")
    if not remaining:
        return 0

    if not any(unit in remaining for unit in ("천", "백", "십")):
        return int(Decimal(remaining))

    total = 0
    for unit, multiplier in (("천", 1000), ("백", 100), ("십", 10)):
        if unit not in remaining:
            continue
        left, remaining = remaining.split(unit, 1)
        total += int(left or "1") * multiplier

    if remaining:
        total += int(remaining)
    return total


def _money_to_won(text: str) -> int | None:
    value = _compact(text)
    if not value.endswith("원"):
        return None

    value = value[:-1]
    total = Decimal(0)

    try:
        if "억" in value:
            left, value = value.split("억", 1)
            total += Decimal(left or "1") * Decimal(100_000_000)

        if value.endswith("만"):
            total += Decimal(_parse_small_number(value[:-1] or "1")) * Decimal(10_000)
        elif value:
            total += Decimal(_parse_small_number(value))
    except (InvalidOperation, ValueError):
        return None

    if total != total.to_integral_value():
        return None
    return int(total)


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(
        start < other_end and end > other_start
        for other_start, other_end in spans
    )


def _extract_values(text: str) -> tuple[_ValueMention, ...]:
    found: list[_ValueMention] = []
    date_spans: list[tuple[int, int]] = []

    for match in _DATE_RE.finditer(text or ""):
        canonical = (
            f"{int(match.group('year')):04d}-"
            f"{int(match.group('month')):02d}-"
            f"{int(match.group('day')):02d}"
        )
        found.append(
            _ValueMention(
                kind="date",
                raw=match.group(0),
                canonical=canonical,
                start=match.start(),
                end=match.end(),
            )
        )
        date_spans.append(match.span())

    for match in _MONEY_RE.finditer(text or ""):
        amount = _money_to_won(match.group("value"))
        if amount is None:
            continue
        found.append(
            _ValueMention(
                kind="money",
                raw=match.group("value"),
                canonical=str(amount),
                start=match.start(),
                end=match.end(),
            )
        )

    for match in _PERCENT_RE.finditer(text or ""):
        value = Decimal(match.group("number"))
        if match.group("unit") == "할":
            value *= Decimal("10")
        found.append(
            _ValueMention(
                kind="percent",
                raw=match.group(0),
                canonical=_decimal_text(value),
                start=match.start(),
                end=match.end(),
            )
        )

    for match in _FRACTION_RE.finditer(text or ""):
        found.append(
            _ValueMention(
                kind="fraction",
                raw=match.group(0),
                canonical=(
                    f"{int(match.group('numerator'))}/"
                    f"{int(match.group('denominator'))}"
                ),
                start=match.start(),
                end=match.end(),
            )
        )

    for match in _PERIOD_RE.finditer(text or ""):
        if _overlaps(match.start(), match.end(), date_spans):
            continue
        found.append(
            _ValueMention(
                kind="period",
                raw=match.group(0),
                canonical=f"{int(match.group('number'))}{match.group('unit')}",
                start=match.start(),
                end=match.end(),
            )
        )

    found.sort(key=lambda value: value.start)
    return tuple(found)


def _evidence_values(evidences: tuple[Evidence, ...]) -> set[tuple[str, str]]:
    values: set[tuple[str, str]] = set()
    for evidence in evidences:
        combined = f"{evidence.citation}\n{evidence.text}"
        values.update(
            (value.kind, value.canonical)
            for value in _extract_values(combined)
        )
    return values


def _sentence_context(text: str, start: int, end: int) -> str:
    """대상 표현이 들어 있는 문장만 잘라 인접 문장의 신호가 섞이지 않게 한다."""

    separators = ".!?;\n"
    left = max((text.rfind(separator, 0, start) for separator in separators), default=-1)
    right_candidates = [
        position
        for separator in separators
        if (position := text.find(separator, end)) >= 0
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1:right]


def _looks_like_source_quote(text: str, start: int, end: int) -> bool:
    context = _sentence_context(text, start, end)
    normalized = _normalize(context)
    return (
        any(sign in normalized for sign in _SOURCE_QUOTE_SIGNS)
        or _LAW_REFERENCE_RE.search(context) is not None
    )


def _quote_issues(answer: Answer) -> list[ValidationIssue]:
    evidence_texts = tuple(
        _compact(evidence.text)
        for evidence in answer.evidences + answer.document_evidences
    )
    issues = []

    for match in _QUOTE_RE.finditer(answer.raw_text):
        quote = next(group for group in match.groups() if group is not None)
        if not _looks_like_source_quote(answer.raw_text, match.start(), match.end()):
            continue

        normalized = _compact(quote)
        if any(normalized in evidence for evidence in evidence_texts):
            continue

        issues.append(
            ValidationIssue(
                kind="quote",
                text=quote,
                detail="직접 인용한 문장을 검색 근거 원문에서 찾을 수 없습니다.",
            )
        )

    return issues


def _value_issues(answer: Answer) -> list[ValidationIssue]:
    supported = _evidence_values(answer.evidences)
    supported.update(
        (value.kind, value.canonical)
        for evidence in answer.document_evidences
        for value in _extract_values(evidence.text)
    )
    issues = []

    for value in _extract_values(answer.raw_text):
        if (value.kind, value.canonical) in supported:
            continue
        issues.append(
            ValidationIssue(
                kind="value",
                text=value.raw,
                detail=f"{value.kind} 값이 검색 근거에 존재하지 않습니다.",
            )
        )

    return issues


_NEXT_DAY_ERROR_RE = re.compile(r"마친\s*(?:당일|날|때)부터")


def _has_next_day_evidence(evidences: tuple[Evidence, ...]) -> bool:
    return any(
        "마친때에는그다음날부터" in _compact(evidence.text)
        for evidence in evidences
    )


def _is_next_day_effect_sentence(sentence: str) -> bool:
    compact = _compact(sentence)
    registration = "주민등록" in compact or "전입신고" in compact
    legal_effect = any(
        marker in compact
        for marker in ("효력", "대항력", "제삼자", "제3자", "보호")
    )
    return registration and legal_effect


def ground_answer_conditions(text: str, evidences: tuple[Evidence, ...]) -> str:
    """근거에 명시된 대항력 발생 시점의 한정적 오기만 교정한다.

    주민등록·전입신고와 효력을 같이 언급한 문장에만 적용해,
    신고기한 등 다른 문맥의 `마친 날부터`는 바꾸지 않는다.
    """
    if not text or not _has_next_day_evidence(evidences):
        return text

    sentences = re.split(r"(?<=[.!?])", text)
    for index, sentence in enumerate(sentences):
        if not _is_next_day_effect_sentence(sentence):
            continue
        sentences[index] = _NEXT_DAY_ERROR_RE.sub("마친 그 다음 날부터", sentence)
    return "".join(sentences)


def _condition_issues(answer: Answer) -> list[ValidationIssue]:
    """숫자로 표현되지 않는 핵심 시점·요건의 반전을 코드로 차단한다."""
    evidence_texts = tuple(_compact(evidence.text) for evidence in answer.evidences)
    issues: list[ValidationIssue] = []

    if _has_next_day_evidence(answer.evidences):
        for sentence in re.split(r"[.!?;\n]+", answer.raw_text):
            if not _is_next_day_effect_sentence(sentence):
                continue
            match = _NEXT_DAY_ERROR_RE.search(sentence)
            if match is None:
                continue
            issues.append(
                ValidationIssue(
                    kind="condition",
                    text=match.group(0),
                    detail="근거의 '그 다음 날부터'를 같은 날부터로 바꾸었습니다.",
                )
            )
            break

    registration_is_optional = any(
        "등기" in text and "없는경우에도" in text
        for text in evidence_texts
    )
    if registration_is_optional:
        for sentence in re.split(r"[.!?;\n]+", answer.raw_text):
            compact = _compact(sentence)
            if not compact or "등기" not in compact:
                continue
            if not any(right in compact for right in ("대항력", "우선변제권")):
                continue
            if not any(sign in compact for sign in ("필요", "필수", "해야", "하여야", "마쳐야", "통해서만")):
                continue
            if any(sign in compact for sign in ("필요하지않", "필수가아니", "하지않아도", "없어도", "등기없이")):
                continue
            issues.append(
                ValidationIssue(
                    kind="condition",
                    text=sentence.strip(),
                    detail="근거는 등기 없이도 효력이 생길 수 있다고 하는데 등기가 필요하다고 바꾸었습니다.",
                )
            )
            break

    return issues


def _money_role(text: str) -> str | None:
    normalized = _normalize(text)

    if any(pattern.search(normalized) for pattern in _ELIGIBILITY_PATTERNS):
        return "eligibility"
    if any(pattern.search(normalized) for pattern in _PAYOUT_PATTERNS):
        return "payout"
    return None


def _money_role_issues(answer: Answer) -> list[ValidationIssue]:
    evidence_roles: dict[str, list[tuple[str, str | None]]] = {}

    for evidence in answer.evidences:
        role = _money_role(evidence.text)
        values = _extract_values(evidence.text)
        for value in values:
            if value.kind != "money":
                continue
            evidence_roles.setdefault(value.canonical, []).append(
                (evidence.chunk_id, role)
            )

    issues = []
    for value in _extract_values(answer.raw_text):
        if value.kind != "money":
            continue

        context = _sentence_context(
            answer.raw_text,
            value.start,
            value.end,
        )
        answer_role = _money_role(context)

        # 목록형 답변은 역할 설명이 바로 위 줄에 있고 금액은 다음 줄부터
        # 나오는 경우가 있다. 현재 문장에 역할 표현이 없을 때만 직전
        # 비어 있지 않은 한 줄을 함께 확인한다.
        if answer_role is None:
            line_start = answer.raw_text.rfind("\n", 0, value.start) + 1
            previous_lines = [
                line.strip()
                for line in answer.raw_text[:line_start].splitlines()
                if line.strip()
            ]
            if previous_lines:
                answer_role = _money_role(
                    f"{previous_lines[-1]} {context}"
                )

        if answer_role is None:
            continue

        candidates = evidence_roles.get(value.canonical, [])
        if not candidates:
            continue

        known_roles = {role for _, role in candidates if role is not None}
        if not known_roles or answer_role in known_roles:
            continue

        issues.append(
            ValidationIssue(
                kind="amount_role",
                text=value.raw,
                detail="금액 자체는 근거에 있지만 그 금액의 의미가 다릅니다.",
                evidence_chunk_ids=tuple(
                    sorted({chunk_id for chunk_id, _ in candidates})
                ),
            )
        )

    return issues


def _paragraph_issues(answer: Answer) -> list[ValidationIssue]:
    law_evidences = tuple(
        evidence
        for evidence in answer.evidences
        if evidence.doc_type in _LAW_TYPES
    )
    issues = []

    for match in _PARAGRAPH_RE.finditer(answer.raw_text):
        article = _compact(match.group("article"))
        paragraph = int(match.group("paragraph"))
        full_reference = _compact(match.group(0))
        supported = False

        for evidence in law_evidences:
            evidence_text_compact = _compact(evidence.text)
            if full_reference in evidence_text_compact:
                supported = True
                break

            if article not in _compact(evidence.citation):
                continue

            explicit = f"제{paragraph}항" in evidence_text_compact
            circled = chr(0x2460 + paragraph - 1) if 1 <= paragraph <= 20 else ""
            if explicit or (circled and circled in evidence.text):
                supported = True
                break

        if not supported:
            issues.append(
                ValidationIssue(
                    kind="paragraph",
                    text=match.group(0),
                    detail="해당 조문의 항 번호를 검색 근거에서 확인할 수 없습니다.",
                )
            )

    return issues


def _safety_verdict_issues(answer: Answer) -> list[ValidationIssue]:
    normalized = _normalize(answer.raw_text)
    for pattern in _SAFETY_VERDICT_PATTERNS:
        match = pattern.search(normalized)
        if match is not None:
            return [
                ValidationIssue(
                    kind="safety_verdict",
                    text=match.group(0),
                    detail=(
                        "개별 계약의 안전·위험 여부를 단정하는 답변은 "
                        "서비스 정책상 사용자에게 전달할 수 없습니다."
                    ),
                )
            ]
    return []


def _semantic_issue(
    answer: Answer,
    semantic_judge: SemanticJudge,
) -> ValidationIssue | None:
    try:
        result = semantic_judge(
            answer.question,
            answer.raw_text,
            answer.evidences,
        )
    except Exception:
        return ValidationIssue(
            kind="semantic",
            text="",
            detail="semantic judge가 답변을 검증하지 못했습니다.",
        )

    judgement = (
        result
        if isinstance(result, SemanticJudgement)
        else SemanticJudgement(supported=bool(result))
    )
    if judgement.supported:
        return None

    return ValidationIssue(
        kind="semantic",
        text=answer.raw_text,
        detail=(
            judgement.detail
            or "답변의 의미가 검색 근거와 일치한다고 확인되지 않았습니다."
        ),
    )


def audit_answer(
    answer: Answer,
    semantic_judge: SemanticJudge | None = None,
) -> ValidationReport:
    """결정론적 검증을 먼저 수행하고, 통과한 답만 semantic judge에 넘긴다.

    ``semantic_judge`` 인자는 단위 테스트와 오케스트레이션 분리를 위해 선택값으로
    두지만, 사후 LLM 검증을 사용하는 실제 서비스에서는 런타임에서 주입하는 것을
    전제로 한다.
    """

    if answer.status != "answered" or not answer.raw_text.strip():
        return ValidationReport(issues=())

    issues: list[ValidationIssue] = []

    citation = audit_citations(answer)
    if citation.missing_required:
        issues.append(
            ValidationIssue(
                kind="citation",
                text="",
                detail="답변에 확인 가능한 출처가 명시되지 않았습니다.",
            )
        )

    issues.extend(
        ValidationIssue(
            kind="citation",
            text=mention.text,
            detail="검색되지 않은 출처를 인용했습니다.",
        )
        for mention in citation.unsupported
    )

    issues.extend(_safety_verdict_issues(answer))
    issues.extend(_quote_issues(answer))
    issues.extend(_value_issues(answer))
    issues.extend(_condition_issues(answer))
    issues.extend(_money_role_issues(answer))
    issues.extend(_paragraph_issues(answer))

    # 명확한 코드 오류가 있으면 LLM을 다시 부를 이유가 없다.
    if not issues and semantic_judge is not None:
        semantic = _semantic_issue(answer, semantic_judge)
        if semantic is not None:
            issues.append(semantic)

    return ValidationReport(issues=tuple(issues))


def validate_answer(
    answer: Answer,
    semantic_judge: SemanticJudge | None = None,
) -> bool:
    return audit_answer(answer, semantic_judge=semantic_judge).is_valid
