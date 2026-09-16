"""Keep connected requirements together for housing protection questions.

Only body-confirmed, same-edition provisions already retrieved by this request
can be selected. This policy does not decide whether the user's facts satisfy
those provisions and does not add documents or rewrite their text.
"""
import re
import unicodedata
from types import MappingProxyType

from src.retrieval.retriever import matches


COMPANION_POLICY_CONFIG = MappingProxyType({
    "name": "housing-protection-companion-v1", "candidate_depth": 20,
    "supplement": False, "preserve_first": True, "minimum_budget": 3,
})

_QUOTED = re.compile(r'```[\s\S]*?(?:```|\Z)|"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」|\'[^\'\n]*\'|`[^`\n]*`')
_DATE = r"확정\s*일자"
_RESIDENT = r"전입\s*신고|주민\s*등록|주소.{0,8}(?:옮|이전)"
_EFFECT = r"효력|대항력|우선\s*변제|보호|권리|불이익|위험|안전|보증금.{0,20}(?:돌려받|변제)"
_PROCEDURE = r"어디|언제|신청|발급|방법|절차|수수료|서류|양식|기록|내역|열람|조회|기한|현황"


def _current_request(query):
    # The current-input boundary owns everything after it. Never reparse a
    # legacy-looking label supplied inside that input as another boundary.
    for marker in ("사용자 입력:", "사용자 질문:"):
        found = list(re.finditer(r"(?:\A|\n)" + marker + r"[ \t]*", query))
        if found:
            return query[found[0].end():].strip() if len(found) == 1 else ""
    if re.search(r"이전 대화|직전 답변 질문:|이어서 상담할 사용자 질문:", query):
        return ""
    return query


def requests_lease_protection(query: str) -> bool:
    """Recognize present housing-rights questions, excluding topic quotations."""
    request = _current_request(unicodedata.normalize("NFKC", query))
    request = _QUOTED.sub(" ", request)
    request = "\n".join(line for line in request.splitlines() if not line.lstrip().startswith(">"))
    current = list(re.finditer(r"(?:^|[.!?\n;])\s*(?:지금은?|이번에는|현재는|이제는?)\s+", request))
    if current:
        request = request[current[-1].end():]
    if re.search(r"상가|점포|가게|사무실|권리금|환산\s*보증금", request):
        return False
    request = re.sub(r"주택\s*임대차\s*보호법", " ", request)
    request = re.sub(r"제외하지\s*말(?:고|아(?:\s*주)?)", " ", request)
    if re.search(
        rf"(?:{_DATE}|효력|대항력|우선\s*변제|보호)(?:은|는|을|를|이|가)?"
        r"[^.!?\n;]{0,18}(?:묻지|관계\s*없|제외|말고|아니라|원하지)", request,
    ):
        return False
    date = bool(re.search(_DATE, request))
    resident = bool(re.search(_RESIDENT, request))
    effect = bool(re.search(_EFFECT, request))
    if not date:
        sufficient_alone = re.search(
            rf"(?:{_RESIDENT})(?:을|를)?\s*만|(?:{_RESIDENT}).{{0,20}}충분", request,
        )
        return bool(resident and "보증금" in request and effect and sufficient_alone)
    # An explicit effect can coexist with an issuance procedure. Without it,
    # a missing-date fact alone must not reroute a fee/record/application task.
    if effect:
        return True
    if re.search(_PROCEDURE, request):
        return False
    if resident and "차이" in request:
        return True
    missing = re.search(
        rf"{_DATE}[^.!?\n;]{{0,18}}(?:없|없이|안\s*받|못\s*받|받지\s*(?:않|못))",
        request,
    )
    consequence = re.search(r"어떻게\s*(?:되|돼)|어떤\s*문제|괜찮|상관|충분", request)
    return bool(missing and consequence)


def _body_paragraphs(chunk):
    meta = chunk["metadata"]
    title, article = meta.get("title"), meta.get("article_no")
    if (meta.get("doc_type") != "law" or meta.get("status") != "current"
            or not title or not article or not meta.get("version")
            or not meta.get("effective_date")
            or meta.get("article_id") != title + "-" + article):
        return []
    header = re.match(r"\[" + re.escape(title + " " + article) + r"(?:\([^\[\]]*\))?\]", chunk["text"])
    if not header:
        return []
    body = chunk["text"][header.end():].strip()
    if re.search(r"(?m)^\s*\[", body):
        return []
    result = []
    for paragraph in re.split(r"(?m)(?=^[①-⑳㉑-㉟㊱-㊿])", body):
        paragraph = re.sub(r"\([^)]*\)|\s", "", paragraph)
        if re.match(r"^[①-⑳㉑-㉟㊱-㊿]", paragraph):
            result.append(paragraph)
    return result


def _opposition(paragraph):
    return all(term in paragraph for term in (
        "주택의인도", "주민등록", "제삼자에대하여효력이생긴다",
    ))


def _priority_reference(paragraph):
    if not all(term in paragraph for term in (
        "대항요건", "확정일자", "우선하여보증금을변제받을권리가있다",
    )):
        return None
    # A reference at the start of the requirement paragraph is owned by the
    # same law. A foreign-law label or a mere mention elsewhere is insufficient.
    reference = re.match(r"^[①-⑳㉑-㉟㊱-㊿](제\d+조(?:의\d+)?)제(\d+)항", paragraph)
    return (reference[1], int(reference[2])) if reference else None


class LeaseProtectionSelector:
    """Select an existing connected pair while retaining the first fused hit."""

    def __init__(self, chunks):
        self.chunks = {chunk["chunk_id"]: chunk for chunk in chunks}
        opposition, priority = [], []
        for chunk in chunks:
            for paragraph in _body_paragraphs(chunk):
                if _opposition(paragraph):
                    opposition.append((chunk, int(unicodedata.numeric(paragraph[0]))))
                reference = _priority_reference(paragraph)
                if reference:
                    priority.append((chunk, reference))
        self.pairs = []
        for source, paragraph_number in opposition:
            for target, reference in priority:
                first, second = source["metadata"], target["metadata"]
                if (source["chunk_id"] != target["chunk_id"]
                        and (first["article_no"], paragraph_number) == reference
                        and all(first[key] == second[key] for key in ("title", "version", "effective_date"))):
                    self.pairs.append((source["chunk_id"], target["chunk_id"]))

    def select(self, query, ranked, k, where=None):
        if k <= 0:
            return []
        if k < 3 or not requests_lease_protection(query):
            return ranked[:k]
        allowed = [hit for hit in ranked if hit[0] in self.chunks
                   and matches(self.chunks[hit[0]]["metadata"], where)]
        positions = {cid: rank for rank, (cid, _) in enumerate(allowed)}
        pairs = [pair for pair in self.pairs if all(cid in positions for cid in pair)]
        if not pairs:
            return allowed[:k]
        pair = min(pairs, key=lambda pair: (
            max(positions[cid] for cid in pair), sum(positions[cid] for cid in pair), pair,
        ))
        pair = sorted(pair, key=positions.get)
        selected = list(dict.fromkeys([allowed[0][0]] + pair + [cid for cid, _ in allowed]))[:k]
        scores = dict(allowed)
        return [(cid, scores[cid]) for cid in selected]
