"""Preserve direct evidence for an explicitly requested unpaid-tax lookup.

This is a lexical retrieval policy, not a judgement about whether a tenant is
eligible to inspect tax records. Conditions in the retrieved articles remain
for the caller to interpret. No evaluation identities are used here.
"""
import re
from types import MappingProxyType

from src.retrieval.context_policy import _current_request
from src.retrieval.retriever import matches


POLICY_CONFIG = MappingProxyType({"name": "explicit-unpaid-tax-lookup-v1",
                                "supplement": False, "candidate_depth": 20,
                                "preserve_unmatched_query": True})

_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」|\'[^\'\n]*\'')
_TAX = re.compile(r"체납|미납|세금.{0,8}(?:밀|안\s*낸)|(?:국세|지방세).{0,8}납부하지\s*않")
_LOOKUP = re.compile(r"확인|조회|열람|알아보|알아볼|보여|증명")
_EXCLUDED = re.compile(
    r"(?:확인|조회|열람|체납|세금).{0,12}(?:묻지|관계없|제외|필요\s*없|말고|아니라)"
    r"|(?:확인|조회|열람)(?:은|는|을|를)?\s*(?:하지\s*않|안\s*하|원하지\s*않)"
)
_COMPLETED = re.compile(r"(?:확인|조회|열람)(?:을|를)?\s*(?:했|하였|완료|마쳤)|확인해\s*(?:뒀|두었)")
_REQUEST = re.compile(r"[?？]|방법|절차|어떻게|어디|궁금|알려\s*주|(?:확인|조회|열람)\s*$|(?:확인|조회|열람).{0,15}(?:할|하나|해야|해도|싶|가능|필요|해\s*주)")


def requested_tax_scopes(query: str) -> tuple[str, ...]:
    """Recognize the current lookup request; ignore quoted or excluded topics."""
    if "이전 대화" in query and "사용자 질문:" not in query:
        return ()
    request = _QUOTED.sub(" ", _current_request(query))
    # A narrow specialization must not consume the entire evidence budget of
    # a separate, simultaneous procedure question.
    if re.search(r"전입|확정일자|임대차\s*신고|계약\s*신고|보증금.{0,12}(?:반환|돌려)"
                 r"|갱신|해지|수리|중개\s*(?:보수|수수료)|경매|배당|우선\s*변제|대항력"
                 r"|사기|고소|신고\s*접수|중개사|중개대상물|확인[·ㆍ\s]*설명"
                 r"|환급|공제|세율|세금\s*계산|납부\s*확인", request):
        return ()
    scopes = set()
    for clause in re.split(r"[.!\n;,]|(?:지만|반면|대신)\s*|(?:하고|고|며)\s+(?=(?:미납)?(?:국세|지방세))", request):
        if not (_TAX.search(clause) and _LOOKUP.search(clause)) or _EXCLUDED.search(clause):
            continue
        if (_COMPLETED.search(clause) or not _REQUEST.search(clause)
                or not re.search(r"집주인|임대인|건물주|세금|국세|지방세|납세", clause)
                or re.search(r"(?:월세|차임|관리비).{0,6}(?:체납|미납)|(?:체납|미납).{0,6}(?:월세|차임|관리비)", clause)):
            continue
        national, local = "국세" in clause, "지방세" in clause
        if national:
            scopes.add("national")
        if local:
            scopes.add("local")
        if not (national or local):
            scopes.update(("national", "local"))
    return tuple(scope for scope in ("national", "local") if scope in scopes)


def tax_lookup_match(text: str, scopes: tuple[str, ...]) -> bool:
    """Require unpaid-tax inspection language in the body, not a title/ID."""
    body = re.sub(r"^\[[^\]\n]+\]\s*", "", text).replace(" ", "")
    if "열람" not in body:
        return False
    return any(
        ("미납" + tax in body or (tax in body and re.search(r"납부하지(?:아니한|않은)", body)))
        for scope, tax in (("national", "국세"), ("local", "지방세"))
        if scope in scopes
    )


class TaxLookupSelector:
    """Rerank body-confirmed lookup evidence without adding dense searches."""

    def __init__(self, chunks: list[dict]):
        self.chunks = {chunk["chunk_id"]: chunk for chunk in chunks}

    def select(self, query, ranked, k, where=None):
        if k <= 0:
            return []
        scopes = requested_tax_scopes(query)
        if not scopes:
            return ranked[:k]
        direct, other = [], []
        for hit in ranked:
            if not matches(self.chunks[hit[0]]["metadata"], where):
                continue
            target = direct if tax_lookup_match(self.chunks[hit[0]]["text"], scopes) else other
            target.append(hit)
        return (direct + other)[:k]
