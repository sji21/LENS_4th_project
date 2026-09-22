"""Check that a cited law is the source of the claim in its sentence.

This is deliberately a conservative lexical guard.  It never supplies legal
knowledge or decides that a claim is true; it only flags an answer when another
retrieved law plainly matches the cited sentence better than the law named in
that sentence.  Semantic review remains responsible for ambiguous cases.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from src.generation.citation import _retrieved_law_key, audit_citations
from src.generation.models import Answer


_LAW_TYPES = frozenset({"law", "decree", "rule"})
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")
_WORD_RE = re.compile(r"[가-힣]{2,}")
_STOP = frozenset({
    "따르면", "따라", "경우", "관련", "규정", "조문", "법률", "내용", "자료",
    "근거", "대하여", "대해서", "있습니다", "없습니다", "합니다", "됩니다", "것입니다",
    "임차인", "임대인", "주택", "계약", "대항력", "보증금",
})
_ENDING = re.compile(r"(?:합니다|됩니다|있습니다|없습니다|하여야|해야|하는|한다|되는|에서|으로|에게|에는|은|는|이|가|을|를|의|에|와|과|도|만|로)$")


@dataclass(frozen=True)
class BindingIssue:
    sentence: str
    cited_chunk_ids: tuple[str, ...]
    supporting_chunk_ids: tuple[str, ...]


def _terms(text: str) -> set[str]:
    result: set[str] = set()
    for word in _WORD_RE.findall(unicodedata.normalize("NFKC", text or "")):
        stem = _ENDING.sub("", word)
        if len(stem) < 2 or stem in _STOP:
            continue
        result.update(stem[index:index + 2] for index in range(len(stem) - 1))
    return result


def _coverage(claim: set[str], source: set[str]) -> float:
    return len(claim & source) / len(claim) if claim else 0.0


def binding_issues(answer: Answer) -> tuple[BindingIssue, ...]:
    """Return only high-confidence wrong-law bindings in ``answer.raw_text``."""
    laws = [item for item in answer.evidences if item.doc_type in _LAW_TYPES]
    if len(laws) < 2 or not answer.raw_text.strip():
        return ()
    by_chunk = {item.chunk_id: item for item in laws}
    source_terms = {item.chunk_id: _terms(item.text) for item in laws}
    citations = audit_citations(answer)
    issues: list[BindingIssue] = []
    for match in _SENTENCE_RE.finditer(answer.raw_text):
        sentence = match.group().strip()
        claim = _terms(sentence)
        if len(claim) < 4:
            continue
        mentions = [mention for mention in citations.mentions
                    if mention.kind == "law" and mention.supported
                    and answer.raw_text.find(mention.text, match.start(), match.end()) >= 0]
        for mention in mentions:
            cited = tuple(chunk_id for chunk_id in mention.evidence_chunk_ids if chunk_id in by_chunk)
            if not cited:
                continue
            cited_score = max(_coverage(claim, source_terms[chunk_id]) for chunk_id in cited)
            alternatives = [item.chunk_id for item in laws if item.chunk_id not in cited]
            best_score = max((_coverage(claim, source_terms[chunk_id]) for chunk_id in alternatives), default=0.0)
            # Do not reject vague sentences.  Only flag a material mismatch: the
            # cited source explains less than a third, while another source
            # explains at least two thirds and is clearly better.
            if cited_score <= 0.30 and best_score >= 0.60 and best_score - cited_score >= 0.37:
                supporting = tuple(chunk_id for chunk_id in alternatives
                                   if _coverage(claim, source_terms[chunk_id]) == best_score)
                issues.append(BindingIssue(sentence, cited, supporting))
    return tuple(issues)
