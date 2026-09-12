"""답변 속 조문 인용과 그 근거 청크의 해당 부분을 이어 준다.

새 데이터를 만들지 않는다. ``Evidence``에 이미 있는 ``citation``·``text``·
``chunk_id``만 쓰고, 검색·생성·검증 파이프라인은 건드리지 않는다.

**공식 근거 전용이다.** 업로드 문서 OCR 근거(``Answer.document_evidences``)는
절대 넘기지 않는다. 그쪽은 개인정보가 들어 있어 화면에 원문을 내보내지 않는 것이
기존 정책이고(``Answer.document_sources()`` 참고), 이 모듈은 근거 원문을
돌려주기 때문이다.
"""

from __future__ import annotations

import re

from src.generation.citation import _LAW_MENTION_RE
from src.retrieval.service import Evidence
from src.generation.source_links import citation_url


# 근거 청크를 읽기 좋은 단위로 자른다. 항 번호(①②…)도 경계로 본다.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\n+|(?=[①-⑳])")

# 겹침을 셀 때 쓸 낱말. 한 글자는 우연히 겹치는 일이 많아 두 글자부터 센다.
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")

_ARTICLE_ONLY_RE = re.compile(r"제\s*\d+\s*조(?:\s*의\s*\d+)?")

_MAX_EXCERPT = 160


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _article_key(article: str) -> str:
    return _compact(article)


def _law_name_of(citation: str) -> str:
    """근거 citation에서 법령명만 뽑는다. 없으면 빈 문자열."""

    match = _LAW_MENTION_RE.search(citation or "")
    return _compact(match.group("law")) if match else ""


def _article_of(citation: str) -> str:
    match = _ARTICLE_ONLY_RE.search(citation or "")
    return _article_key(match.group(0)) if match else ""


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text or "")]
    return [part for part in parts if part]


def _answer_sentence(text: str, start: int, end: int) -> str:
    """인용이 들어 있는 문장만 잘라 낸다. 겹침 점수의 기준이 된다."""

    left = max(text.rfind(mark, 0, start) for mark in (". ", "\n", "。")) + 1
    right_candidates = [text.find(mark, end) for mark in (". ", "\n", "。")]
    right_candidates = [pos for pos in right_candidates if pos != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return text[max(left, 0):right].strip()


def _best_excerpt(evidence_text: str, answer_sentence: str) -> str:
    """근거 청크에서 답변 문장과 가장 많이 겹치는 부분을 고른다.

    겹치는 낱말이 하나도 없으면 첫 문장을 쓴다. 관련 없는 대목을 억지로 고르는
    것보다, 청크가 어떤 조문인지 보여 주는 첫 문장이 낫다.
    """

    sentences = _sentences(evidence_text)
    if not sentences:
        return (evidence_text or "").strip()[:_MAX_EXCERPT]

    wanted = set(_TOKEN_RE.findall(answer_sentence))
    best, best_score = sentences[0], 0
    if wanted:
        for sentence in sentences:
            score = len(wanted & set(_TOKEN_RE.findall(sentence)))
            if score > best_score:
                best, best_score = sentence, score

    excerpt = best.strip()
    if len(excerpt) > _MAX_EXCERPT:
        excerpt = excerpt[:_MAX_EXCERPT].rstrip() + "…"
    return excerpt


def _match_evidence(
    law: str,
    article: str,
    evidences: tuple[Evidence, ...],
) -> Evidence | None:
    """법령명은 **완전 일치**로만 본다.

    부분 문자열로 비교하면 "주택임대차보호법"이 "주택임대차보호법 시행령"에도
    걸려, 답변이 인용하지 않은 법령으로 링크가 붙는다.
    """

    if law:
        for evidence in evidences:
            if _law_name_of(evidence.citation) == law and _article_of(evidence.citation) == article:
                return evidence
        return None

    # 법령명 없이 "제3조의2"만 적힌 경우: 그 조문을 가진 근거가 하나뿐일 때만 잇는다.
    matches = [e for e in evidences if _article_of(e.citation) == article]
    return matches[0] if len(matches) == 1 else None


def build_citation_spans(text: str, evidences: tuple[Evidence, ...]) -> tuple[dict, ...]:
    """답변 본문에서 근거와 이어지는 인용 구간을 찾는다.

    ``start``·``end``는 ``text``의 문자 인덱스다. 화면에 그대로 그릴 문자열을
    넘겨야 위치가 어긋나지 않는다.
    """

    if not text or not evidences:
        return ()

    spans: list[dict] = []
    taken: list[tuple[int, int]] = []

    def _add(start: int, end: int, law: str, article: str) -> None:
        if any(start < prior_end and prior_start < end for prior_start, prior_end in taken):
            return
        evidence = _match_evidence(law, article, evidences)
        if evidence is None:
            return
        excerpt = _best_excerpt(evidence.text, _answer_sentence(text, start, end))
        if not excerpt:
            return
        taken.append((start, end))
        spans.append(
            {
                "start": start,
                "end": end,
                "citation": evidence.citation,
                "chunk_id": evidence.chunk_id,
                "doc_type": evidence.doc_type,
                "url": evidence.source_url or citation_url(evidence.citation, evidence.doc_type),
                "current_url": citation_url(evidence.citation, evidence.doc_type),
                "excerpt": excerpt,
            }
        )

    for match in _LAW_MENTION_RE.finditer(text):
        _add(
            match.start(),
            match.end(),
            _compact(match.group("law")),
            _article_key(match.group("article")),
        )

    for match in _ARTICLE_ONLY_RE.finditer(text):
        _add(match.start(), match.end(), "", _article_key(match.group(0)))

    for match in re.finditer(r"\d{2,4}[가-힣]{1,4}\d+", text):
        matches = [e for e in evidences if e.doc_type == "case" and match.group() in e.citation]
        if len(matches) == 1:
            evidence = matches[0]
            spans.append(dict(start=match.start(), end=match.end(), citation=evidence.citation,
                              chunk_id=evidence.chunk_id, doc_type="case", excerpt=_best_excerpt(evidence.text, text),
                              url=evidence.source_url or citation_url(evidence.citation, "case"),
                              current_url=citation_url(evidence.citation, "case")))

    spans.sort(key=lambda span: span["start"])
    return tuple(spans)
