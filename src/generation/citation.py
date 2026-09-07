"""모델이 적은 출처가 실제 검색 근거에 있는지 검증한다.

이 단계는 provenance만 확인한다. 인용한 문장이 근거의 의미와 맞는지, 숫자나
조건이 뒤바뀌었는지는 validation.py가 담당한다. 검증 대상은 화면용 ``text``가
아니라 LLM 원문인 ``Answer.raw_text``다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
import unicodedata
from typing import Literal

from src.generation.models import Answer
from src.retrieval.service import Evidence


CitationKind = Literal["law", "case", "guide"]
ArticleKey = tuple[int, int | None]
LawKey = tuple[str, ArticleKey]


@dataclass(frozen=True)
class CitationMention:
    kind: CitationKind
    text: str
    supported: bool
    evidence_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CitationAudit:
    mentions: tuple[CitationMention, ...]
    missing_required: bool = False

    @property
    def unsupported(self) -> tuple[CitationMention, ...]:
        return tuple(mention for mention in self.mentions if not mention.supported)

    @property
    def is_valid(self) -> bool:
        return not self.missing_required and not self.unsupported


_ARTICLE_RE = re.compile(
    r"제\s*(?P<article>\d+)\s*조"
    r"(?:\s*의\s*(?P<branch>\d+))?"
)

_LAW_MENTION_RE = re.compile(
    r"(?P<law>"
    r"[가-힣A-Za-z0-9·]+"
    r"(?:\s+[가-힣A-Za-z0-9·]+){0,5}?"
    r"(?:법(?:\s*시행령|\s*시행규칙)?)"
    r")\s*"
    r"(?P<article>제\s*\d+\s*조(?:\s*의\s*\d+)?)"
)

_CASE_MENTION_RE = re.compile(
    r"(?:(?P<court>"
    r"대법원|헌법재판소|"
    r"[가-힣]+(?:지방법원|고등법원|가정법원|행정법원)"
    r")\s*)?"
    r"(?P<number>\d{4}[가-힣]{1,4}\d+)"
)

_COURT_NAME_RE = re.compile(
    r"^(?:대법원|헌법재판소|"
    r"[가-힣]+(?:지방법원|고등법원|가정법원|행정법원))$"
)

_AGENCY_RE = re.compile(
    r"HUG|NTS|"
    r"[가-힣A-Za-z0-9·]{2,30}(?:부|청|공사|공단|원)",
    re.IGNORECASE,
)

_GUIDE_MENTION_RE = re.compile(
    r"(?P<agency>"
    r"HUG|NTS|"
    r"[가-힣A-Za-z0-9·]{2,30}(?:부|청|공사|공단|원)"
    r")"
    r"(?P<tail>[^.\n]{0,40}?"
    r"(?:안내|자료|가이드|에\s*따르면|에\s*의하면))",
    re.IGNORECASE,
)

_LAW_DOC_TYPES = frozenset({"law", "decree", "rule"})

_GUIDE_ALIAS_GROUPS = {
    "주택도시보증공사": (
        "주택도시보증공사",
        "HUG",
    ),
    "국세청": (
        "국세청",
        "NTS",
    ),
}


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", _normalize(text), flags=re.UNICODE)


_GRAMMATICAL_TOKEN_SUFFIXES = (
    "은", "는", "이", "가", "을", "를", "의", "에", "에서", "으로", "로", "와", "과",
)


def _clean_law_name(raw_name: str) -> str:
    """정규식이 법령명 앞의 조사 결합 어절까지 잡은 경우 앞부분을 버린다."""

    tokens = raw_name.split()
    if len(tokens) <= 1:
        return raw_name

    # 오른쪽에서 가장 가까운 문법 어절 뒤를 법령명 후보로 쓴다.
    # 예: "이 조문은 민법" -> "민법". 실제 다어절 법령명은 그대로 남는다.
    for index in range(len(tokens) - 2, -1, -1):
        token = tokens[index]
        if any(token.endswith(suffix) for suffix in _GRAMMATICAL_TOKEN_SUFFIXES):
            return " ".join(tokens[index + 1:])

    return raw_name


def _article_key(text: str) -> ArticleKey | None:
    match = _ARTICLE_RE.search(text or "")
    if match is None:
        return None

    branch = match.group("branch")
    return (
        int(match.group("article")),
        int(branch) if branch is not None else None,
    )


def _law_mentions(text: str) -> list[tuple[re.Match[str], str, ArticleKey]]:
    mentions = []
    for match in _LAW_MENTION_RE.finditer(text or ""):
        article = _article_key(match.group("article"))
        if article is None:
            continue
        mentions.append((match, _compact(_clean_law_name(match.group("law"))), article))
    return mentions


def _canonical_law_name(raw_name: str, known_names: set[str]) -> str:
    name = _compact(raw_name)
    if name in known_names:
        return name

    suffix_matches = [known for known in known_names if name.endswith(known)]
    if suffix_matches:
        return max(suffix_matches, key=len)

    return name


def _primary_law_name(evidence: Evidence) -> str:
    citation_mentions = _law_mentions(evidence.citation)
    if citation_mentions:
        return citation_mentions[0][1]

    text_mentions = _law_mentions(evidence.text)
    if text_mentions:
        return text_mentions[0][1]

    return ""


def _law_pairs_from_evidence(evidence: Evidence) -> set[LawKey]:
    """Evidence 안의 법령명-조문 관계를 pair 단위로 보존한다.

    본문에 다른 법률을 명시적으로 참조한 경우 그 조문을 현재 Evidence의 주법령과
    섞지 않는다. 반대로 ``제3조``처럼 법령명이 없는 내부 교차참조는 해당 Evidence의
    주법령으로 해석한다.
    """

    citation_mentions = _law_mentions(evidence.citation)
    text_mentions = _law_mentions(evidence.text)
    all_mentions = citation_mentions + text_mentions

    primary_law = _primary_law_name(evidence)
    known_names = {law_name for _, law_name, _ in all_mentions}
    if primary_law:
        known_names.add(primary_law)

    pairs = {
        (_canonical_law_name(law_name, known_names), article)
        for _, law_name, article in all_mentions
    }

    if not primary_law:
        return pairs

    explicit_article_spans = [
        match.span("article")
        for match, _, _ in text_mentions
    ]

    for article_match in _ARTICLE_RE.finditer(evidence.text or ""):
        start, end = article_match.span()
        if any(
            start >= explicit_start and end <= explicit_end
            for explicit_start, explicit_end in explicit_article_spans
        ):
            continue

        article = _article_key(article_match.group(0))
        if article is not None:
            pairs.add((primary_law, article))

    return pairs


def _build_law_index(
    evidences: tuple[Evidence, ...],
) -> tuple[dict[LawKey, set[str]], set[str]]:
    index: dict[LawKey, set[str]] = {}
    known_laws: set[str] = set()

    for evidence in evidences:
        if evidence.doc_type not in _LAW_DOC_TYPES:
            continue

        primary = _primary_law_name(evidence)
        if primary:
            known_laws.add(primary)

        for law_key in _law_pairs_from_evidence(evidence):
            known_laws.add(law_key[0])
            index.setdefault(law_key, set()).add(evidence.chunk_id)

    return index, known_laws


def _build_case_index(
    evidences: tuple[Evidence, ...],
) -> tuple[dict[tuple[str, str], set[str]], dict[str, set[str]]]:
    exact: dict[tuple[str, str], set[str]] = {}
    by_number: dict[str, set[str]] = {}

    for evidence in evidences:
        if evidence.doc_type != "case":
            continue

        combined = f"{evidence.citation}\n{evidence.text}"
        for match in _CASE_MENTION_RE.finditer(combined):
            court = _compact(match.group("court") or "")
            number = _compact(match.group("number"))

            by_number.setdefault(number, set()).add(evidence.chunk_id)
            if court:
                exact.setdefault((court, number), set()).add(evidence.chunk_id)

    return exact, by_number


def _guide_identity(agency: str) -> str:
    target = _compact(agency)
    for canonical, aliases in _GUIDE_ALIAS_GROUPS.items():
        if target in {_compact(alias) for alias in aliases}:
            return _compact(canonical)
    return target


def _is_court_name(name: str) -> bool:
    """법원명은 안내 기관이 아니라 판례 인용의 일부다."""

    return bool(_COURT_NAME_RE.fullmatch((name or "").strip()))


def _build_guide_index(evidences: tuple[Evidence, ...]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}

    for evidence in evidences:
        if evidence.doc_type != "guide":
            continue

        citation_compact = _compact(evidence.citation)
        matched_alias = False

        for canonical, aliases in _GUIDE_ALIAS_GROUPS.items():
            if any(_compact(alias) in citation_compact for alias in aliases):
                index.setdefault(_compact(canonical), set()).add(evidence.chunk_id)
                matched_alias = True

        if matched_alias:
            continue

        agency_match = _AGENCY_RE.search(evidence.citation)
        if agency_match is not None:
            index.setdefault(
                _guide_identity(agency_match.group(0)),
                set(),
            ).add(evidence.chunk_id)

    return index


def extract_citation_mentions(
    raw_text: str,
    evidences: tuple[Evidence, ...],
) -> tuple[CitationMention, ...]:
    """LLM 원문에 명시된 법령·판례·기관 출처를 추출하고 provenance를 확인한다."""

    law_index, known_laws = _build_law_index(evidences)
    case_exact, case_by_number = _build_case_index(evidences)
    guide_index = _build_guide_index(evidences)

    found: list[tuple[int, CitationMention]] = []
    seen: set[tuple[str, object]] = set()

    for match, law_name, article in _law_mentions(raw_text):
        canonical_law = _canonical_law_name(law_name, known_laws)
        key: LawKey = (canonical_law, article)
        seen_key = ("law", key)
        if seen_key in seen:
            continue
        seen.add(seen_key)

        chunk_ids = tuple(sorted(law_index.get(key, set())))
        found.append(
            (
                match.start(),
                CitationMention(
                    kind="law",
                    text=match.group(0).strip(),
                    supported=bool(chunk_ids),
                    evidence_chunk_ids=chunk_ids,
                ),
            )
        )

    for match in _CASE_MENTION_RE.finditer(raw_text or ""):
        court = _compact(match.group("court") or "")
        number = _compact(match.group("number"))
        key = (court, number)
        seen_key = ("case", key)
        if seen_key in seen:
            continue
        seen.add(seen_key)

        chunks = case_exact.get(key, set()) if court else case_by_number.get(number, set())
        chunk_ids = tuple(sorted(chunks))
        found.append(
            (
                match.start(),
                CitationMention(
                    kind="case",
                    text=match.group(0).strip(),
                    supported=bool(chunk_ids),
                    evidence_chunk_ids=chunk_ids,
                ),
            )
        )

    for match in _GUIDE_MENTION_RE.finditer(raw_text or ""):
        agency = match.group("agency")
        # "대법원 판례에 따르면"의 대법원을 안내 기관으로 해석하면, 사건번호가
        # 있는 정상 판례 인용에도 지원되지 않는 guide 출처 오류가 함께 생긴다.
        # 법원명은 case 인용에서만 다루며, 사건번호 없이는 검증 가능한 출처가 아니다.
        if _is_court_name(agency):
            continue
        identity = _guide_identity(agency)
        seen_key = ("guide", identity)
        if seen_key in seen:
            continue
        seen.add(seen_key)

        chunk_ids = tuple(sorted(guide_index.get(identity, set())))
        found.append(
            (
                match.start(),
                CitationMention(
                    kind="guide",
                    text=agency.strip(),
                    supported=bool(chunk_ids),
                    evidence_chunk_ids=chunk_ids,
                ),
            )
        )

    found.sort(key=lambda item: item[0])
    return tuple(mention for _, mention in found)


def audit_citations(answer: Answer) -> CitationAudit:
    mentions = extract_citation_mentions(answer.raw_text, answer.evidences)
    if answer.document_evidences:
        document_texts = tuple(
            (evidence.chunk_id, _compact(evidence.text))
            for evidence in answer.document_evidences
        )
        grounded_mentions = []
        for mention in mentions:
            if mention.supported:
                grounded_mentions.append(mention)
                continue

            target = _compact(mention.text)
            document_chunk_ids = tuple(
                chunk_id
                for chunk_id, text in document_texts
                if target and target in text
            )
            grounded_mentions.append(
                replace(
                    mention,
                    supported=True,
                    evidence_chunk_ids=document_chunk_ids,
                )
                if document_chunk_ids
                else mention
            )
        mentions = tuple(grounded_mentions)

    missing_required = (
        answer.status == "answered"
        and bool(answer.raw_text.strip())
        and answer.requires_official_citation
        and not mentions
    )
    return CitationAudit(mentions=mentions, missing_required=missing_required)


def validate_citations(answer: Answer) -> bool:
    return audit_citations(answer).is_valid
