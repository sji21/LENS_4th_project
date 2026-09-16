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
    # Comparison identity is independent of the original display/issue text.
    law_key: LawKey | None = None


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
    r"(?<![가-힣A-Za-z0-9])"
    r"(?:HUG|NTS|[가-힣A-Za-z0-9·]{2,30}?"
    r"(?:위원\s*회|부|청|공사|공단|원(?!\s*회)))"
    r"(?=$|[\s.,·!?;:()「」『』]|안내|자료|가이드|에\s*(?:따르면|의하면)|"
    r"(?:으로부터|로부터|에서는|에서|에게|의|에|은|는|이|가|을|를|와|과|으로|로)"
    r"(?=$|[\s.,!?;:()「」『』]))",
    re.IGNORECASE,
)

# 기관명 사이가 순수 접속(쉼표/및/과/와/그리고/또는)으로만 이어질 때,
# "국세청과 HUG의 안내에 따르면"처럼 공동으로 인용된 각 기관이 모두
# 검증 대상이 되도록 묶어서 판단하는 데 쓴다.
_CONJUNCTION_GAP_RE = re.compile(
    r"^\s*(?:[,·]\s*(?:(?:및|그리고|또는)\s*)?|(?:및|과|와|그리고|또는)\s*)$"
)


def _is_guide_citation(tail: str) -> bool:
    """자료 인용과 방문·신청·안내받기 같은 절차 설명을 구분한다."""
    tail = re.split(r"[.!?;\n]", tail, maxsplit=1)[0].strip()
    if re.match(r"에\s*(?:따르면|의하면)", tail):
        return True
    # 발행 주체를 나타내는 절만 제거한다. '에서 신청하고'까지 지우면
    # 일반 절차 설명 뒤의 '안내'를 다시 출처로 오인하게 된다.
    tail = re.sub(
        r"^(?:에서는|에서|가|이)?\s*(?:제공|발간|발행|배포|발표|게시)"
        r"(?:하고\s*있는|되고\s*있는|되어\s*있는|돼\s*있는|\s*중인|하는|되는|한|된)\s*", "", tail,
    )
    tail = re.sub(r"^의\s*", "", tail)
    if re.match(r"(?:으로부터|로부터|에서는|에서|에게|에|으로|로|을|를|와|과)(?:\s|$)", tail):
        return False
    # 긴 자료명을 먼저 소비한다. '안내서'를 '안내'까지만 읽으면
    # 뒤의 '서를 받으세요'를 수령 안내로 인식하지 못한다.
    material = re.match(
        r"[가-힣A-Za-z0-9·()「」『』\"' \t-]{0,60}?"
        r"(?:안내자료|안내문|안내서|자료집|가이드북|안내|자료|가이드)"
        r"(?=$|[\s「」『』\"')]|에|의|을|를|은|는|이|가|상)", tail,
    )
    if material is None:
        return False
    remainder = tail[material.end():].lstrip(" \t」』\"')")
    # 인용/근거 표지는 행동 안내보다 먼저 확인한다.
    if re.match(r"(?:에\s*(?:따르|의하|의해|근거)|(?:을|를)\s*(?:근거|인용)|상(?:으로)?\s)", remainder):
        return True
    if re.match(r"(?:을|를)?\s*(?:받|수령|다운로드|내려받|신청|요청)", remainder):
        return False
    # 명시적으로 자료를 지목한 나머지는 기존처럼 출처 확인 대상으로 둔다.
    return True

_LAW_DOC_TYPES = frozenset({"law", "decree", "rule"})

_EMPHASIS_RE = re.compile(
    r"(?<![\\*_])(?P<marker>\*{1,3}|_{1,3})(?=\S)"
    r"(?P<body>[^\n]+?)(?<=\S)(?P=marker)(?![*_])"
)


def citation_scan_text(text: str) -> str:
    """Mask paired emphasis delimiters without changing source offsets.

    This is a parsing view only. Answers and issue text retain the original
    spelling/formatting; unpaired punctuation is not silently removed.
    """
    chars = list(text or "")
    for match in _EMPHASIS_RE.finditer(text or ""):
        for start, end in ((match.start(), match.start("body")),
                           (match.end("body"), match.end())):
            chars[start:end] = " " * (end - start)
    # Korean law-name quotation marks are presentation, not part of the name.
    return "".join(chars).translate(str.maketrans({c: " " for c in "「」『』"}))

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
        if (any(token.endswith(suffix) for suffix in _GRAMMATICAL_TOKEN_SUFFIXES)
                or re.fullmatch(r"제\d+[항호목]", token)):
            tokens = tokens[index + 1:]
            break

    # Only leading conjunctions belong to the previous citation. Keep internal
    # conjunctions in real multiword law names (e.g. "... 및 ...법").
    while len(tokens) > 1 and tokens[0] in {"및", "또는", "그리고"}:
        tokens = tokens[1:]
    return " ".join(tokens)


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
    scan = citation_scan_text(text)
    start = 0
    for article_match in _ARTICLE_RE.finditer(scan):
        # Limit each search to the next article, so the law-name regex cannot
        # swallow an earlier citation while searching for a later law name.
        # pos/endpos retain absolute source offsets for paragraph validation.
        match = _LAW_MENTION_RE.search(scan, start, article_match.end())
        start = article_match.end()
        if match is not None and match.span("article") == article_match.span():
            article = _article_key(match.group("article"))
            if article is not None:
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


def _law_key_from_label(label: str) -> LawKey | None:
    """Parse the identity before citation_of()'s optional article title.

    Only one balanced, terminal parenthesized title is removed. Do not hide
    another citation following it, or accept an incomplete/mixed label.
    """
    head, opening, tail = label.strip().partition("(")
    if opening:
        depth = 1
        for index, char in enumerate(tail):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    if tail[index + 1:].strip():
                        return None
                    break
        if depth != 0:
            return None
    mentions = _law_mentions(head)
    if len(mentions) != 1 or len(_ARTICLE_RE.findall(citation_scan_text(head))) != 1:
        return None
    return mentions[0][1:]


def _retrieved_law_key(evidence: Evidence) -> LawKey | None:
    """Identify this chunk, never articles merely referenced in its prose.

    Legacy evidence without citation metadata may use its leading retrieval
    header. Arbitrary body mentions are never a substitute for that identity.
    """
    label = evidence.citation.strip()
    if not label:
        header = re.match(r"\[([^\]\n]+)\]", evidence.text.lstrip())
        if header is None:
            return None
        label = header[1]
    return _law_key_from_label(label)


def _build_law_index(
    evidences: tuple[Evidence, ...],
) -> tuple[dict[LawKey, set[str]], set[str]]:
    index: dict[LawKey, set[str]] = {}
    known_laws: set[str] = set()

    for evidence in evidences:
        if evidence.doc_type not in _LAW_DOC_TYPES:
            continue

        # Body references help resolve names, but never grant provenance.
        known_laws.update(law for _, law, _ in _law_mentions(evidence.text))
        law_key = _retrieved_law_key(evidence)
        if law_key is not None:
            known_laws.add(law_key[0])
            index.setdefault(law_key, set()).add(evidence.chunk_id)

    return index, known_laws


def answer_citation_scan_text(text: str, evidences: tuple[Evidence, ...]) -> str:
    """Shared offset-preserving view for answer validation and display.

    A retrieved label's copied title is descriptive, not another claim. Only
    an exact title (apart from presentation whitespace/emphasis) attached to
    its own law/article is masked; other parenthetical claims remain visible.
    """
    scan = citation_scan_text(text)
    titles: dict[LawKey, set[str]] = {}
    for evidence in evidences:
        if evidence.doc_type not in _LAW_DOC_TYPES:
            continue
        key = _retrieved_law_key(evidence)
        label = evidence.citation.strip()
        if not label:
            header = re.match(r"\[([^\]\n]+)\]", evidence.text.lstrip())
            label = header[1] if header else ""
        if key is not None and "(" in label:
            title = citation_scan_text(label[label.index("("):])
            titles.setdefault(key, set()).add(re.sub(r"\s+", "", title))

    chars = list(scan)
    masked_until = 0
    for match, law, article in _law_mentions(scan):
        if match.start() < masked_until or (law, article) not in titles:
            continue
        start = match.end()
        while start < len(scan) and scan[start].isspace():
            start += 1
        if start == len(scan) or scan[start] != "(":
            continue
        depth = 0
        for end in range(start, len(scan)):
            if scan[end] == "(":
                depth += 1
            elif scan[end] == ")":
                depth -= 1
                if depth == 0:
                    title = re.sub(r"\s+", "", scan[start:end + 1])
                    if title in titles[(law, article)]:
                        # Keep parentheses as lexical boundaries.
                        chars[start + 1:end] = " " * (end - start - 1)
                        masked_until = end + 1
                    break
    return "".join(chars)


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

        # 답변과 근거에 동일한 기관명 경계를 적용한다. 부분 문자열만으로
        # '국세청장' 같은 다른 이름을 '국세청'의 근거로 연결하지 않는다.
        for agency_match in _AGENCY_RE.finditer(citation_scan_text(evidence.citation)):
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

    for match, law_name, article in _law_mentions(answer_citation_scan_text(raw_text, evidences)):
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
                    text=raw_text[match.start():match.end()].strip(),
                    supported=bool(chunk_ids),
                    evidence_chunk_ids=chunk_ids,
                    law_key=key,
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

    guide_scan = citation_scan_text(raw_text or "")
    agencies = list(_AGENCY_RE.finditer(guide_scan))

    # 공동 기관 인용("국세청과 HUG의 안내에 따르면")은 마지막 기관 뒤의
    # 서술부만 보고도 앞선 기관들이 같은 서술을 공유한다는 걸 알아야 한다.
    # 접속사로만 이어진 구간은 뒤쪽 기관의 tail 판정을 그대로 물려받는다.
    group_end = [len(guide_scan)] * len(agencies)
    tail_source = list(range(len(agencies)))
    for i in range(len(agencies) - 2, -1, -1):
        gap = guide_scan[agencies[i].end():agencies[i + 1].start()]
        if _CONJUNCTION_GAP_RE.match(gap):
            group_end[i] = group_end[i + 1]
            tail_source[i] = tail_source[i + 1]
        else:
            group_end[i] = agencies[i + 1].start()

    for index, match in enumerate(agencies):
        agency = match.group(0)
        source = agencies[tail_source[index]]
        end = group_end[tail_source[index]]
        if not _is_guide_citation(guide_scan[source.end():min(end, source.end() + 100)]):
            continue
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
        document_laws: dict[LawKey, list[str]] = {}
        for evidence in answer.document_evidences:
            keys = {(law, article) for _, law, article in _law_mentions(evidence.text)}
            for key in keys:
                document_laws.setdefault(key, []).append(evidence.chunk_id)
        grounded_mentions = []
        for mention in mentions:
            if mention.supported:
                grounded_mentions.append(mention)
                continue

            if mention.kind == "law":
                # Exact law/article/branch equality: display text may include
                # a conjunction, and substrings confuse 제4조 with 제40조/제4조의2.
                document_chunk_ids = tuple(document_laws.get(mention.law_key, ()))
            else:
                # Keep the existing case/agency document matching policy.
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
