"""Retain distinct, explicitly requested law topics in one evidence budget.

Only candidates returned by the existing lexical/dense searches are eligible.
The policy recognizes legal concepts in verified article bodies, not article
numbers, neighbours, evaluation identities, or references to other provisions.
"""
import re
import unicodedata
from types import MappingProxyType

from src.retrieval.companion_evidence import _QUOTED, _current_request
from src.retrieval.retriever import matches


INTENT_POLICY_CONFIG = MappingProxyType({
    "name": "explicit-law-intents-v1", "member_depth": 20,
    "candidate_pool": "complete-existing-member-union", "supplement": False,
    "minimum_budget": 3, "preserve_unmatched_query": True,
})

_PRIVATE = r"(?:등록\s*(?:된\s*)?)?민간\s*임대|등록\s*임대\s*사업자"
_RESIDENCE = r"전입\s*신고|주민\s*등록|주소.{0,8}(?:옮|이전)"
_RENEWAL = r"재계약|갱신|계약\s*연장"
_REPORT = r"(?:임대차|계약)\s*신고|변경\s*신고"
_FORM = r"서류|서식|양식|계약서"
_LEASE_ACTION = r"(?:임대차\s*)?계약(?!서|\s*(?:신고|연장))|보증금"
_LEASE_PAPERWORK = (r"(?<!재)(?:임대차\s*)?계약(?=(?:을|를)?\s*(?:체결|작성)"
                    r"|(?:할|하는)\s*때|(?:에|의)?\s*(?:필요한\s*)?(?:서류|서식|양식))")
_TOPIC_JOIN = r"\s*(?:(?:의\s*)?(?:조건|절차|방법|요건)?\s*(?:와|과|및|나|이나|랑|하고|[,·])\s*)*"
_PROCEDURE = r"언제|며칠|기한|절차|어디|방법|신청|접수|가능|못\s*하|할\s*수|뒤에\s*하"
_EXCLUDED = r"묻지|관계\s*없|제외|말고|아니라|아닌|아니고|아닙|원하지|필요\s*없"


def _clean(text):
    text = _QUOTED.sub(" ", text)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))


def _request_and_facts(query):
    query = unicodedata.normalize("NFKC", query)
    request = _clean(_current_request(query))
    # Only an explicit current-facts boundary can supply an omitted property
    # type. Earlier dialogue is never a source of new requested topics.
    facts = ""
    extra = re.search(r"(?:^|\n)사용자 추가 상황:[ \t]*(.*?)(?=\n사용자 (?:질문|입력):|\Z)", query, re.S)
    if extra and request:
        facts = _clean(extra[1])
        if re.search(r"과거|예전|이전", facts):
            current = re.search(r"(?:지금|현재|이번)\s", facts)
            facts = facts[current.start():] if current else ""
    switches = list(re.finditer(r"(?:^|[.!?\n;])\s*(?:지금은?|이번에는|현재는|이제는?)\s+", request))
    if switches:
        request, facts = request[switches[-1].end():], ""
    return request, facts


def _intent_clauses(request):
    # Keep a coordinated list together, including commas, until its own
    # request/exclusion ends. A later affirmative request starts a new scope.
    request = re.sub(r"제외하지\s*말고", "포함해서", request)
    return re.split(r"(?<=[.!?\n;])|(?<=제외하고)|(?<=않고)|(?<=않으며)"
                    r"|(?<=말고)|(?<=하지만)|(?<=알려주고)", request)


def _topic_tail(tail, *, forms=True):
    """Keep a shared list predicate, but stop before a separate topic."""
    end = 0
    topics = rf"{_RENEWAL}|{_REPORT}|{_RESIDENCE}|{_LEASE_ACTION}" + ("|" + _FORM if forms else "")
    for topic in re.finditer(topics, tail):
        join = tail[end:topic.start()]
        if not re.fullmatch(_TOPIC_JOIN, join):
            return tail[:topic.start()]
        end = topic.end()
    return tail


def _requested(pattern, request):
    """Exclude a named topic or an explicitly excluded coordinated list."""
    for clause in _intent_clauses(request):
        for match in re.finditer(pattern, clause):
            tail = clause[match.end():]
            if pattern == _PRIVATE:
                tail = re.split(rf"[.!?\n;,]|{_RENEWAL}|{_REPORT}|{_FORM}|{_RESIDENCE}", tail, 1)[0]
            else:
                tail = _topic_tail(tail, forms=pattern not in (_RENEWAL, _REPORT))
            if not re.search(_EXCLUDED, tail):
                return True
    return False


def _renewal_requested(request):
    for clause in _intent_clauses(request):
        if not _requested(_RENEWAL, clause):
            continue
        for match in re.finditer(_RENEWAL, clause):
            # Filing vocabulary in another topic must not turn a background
            # renewal/rent-change event into a request about renewal rights.
            tail = _topic_tail(clause[match.end():], forms=False)
            tail = re.split(r"임대료|보증금|월세|차임|인상|증액", tail, 1)[0]
            if (re.search(r"조건|절차|거절|권리|요건|어떻게|알려|서류|서식", tail)
                    or re.fullmatch(r"\s*(?:은|는)?(?:요)?[?？.]?\s*", tail)):
                return True
    return False


def _private_form_requested(request):
    """Require documents owned by a lease topic, not just a property type."""
    for clause in _intent_clauses(request):
        if not _requested(_FORM, clause):
            continue
        for topic in (_REPORT, _RENEWAL, _LEASE_PAPERWORK):
            active = _renewal_requested(clause) if topic == _RENEWAL else _requested(topic, clause)
            if not active:
                continue
            for match in re.finditer(topic, clause):
                if _requested(_FORM, _topic_tail(clause[match.end():], forms=False)):
                    return True
        # A named lease contract form is itself an explicit topic. A contract
        # mentioned as a registration attachment is not that topic.
        for form in re.finditer(r"(?:임대차\s*)?계약서", clause):
            residents = list(re.finditer(_RESIDENCE, clause[:form.start()]))
            if residents and not re.fullmatch(_TOPIC_JOIN, clause[residents[-1].end():form.start()]):
                continue
            if _requested(_FORM, clause[form.start():]):
                return True
    return False


def requested_law_intents(query: str) -> tuple[str, ...]:
    request, facts = _request_and_facts(query)
    if not request or re.search(r"상가|점포|가게|사무실|권리금|환산\s*보증금", request):
        return ()
    intents = []
    scope = request + "\n" + facts
    private = _requested(_PRIVATE, scope)
    if re.search(r"공공\s*임대|국민\s*임대|행복\s*주택|일반\s*(?:주택|집|임대)", request) and not re.search(_PRIVATE, request):
        private = False
    if private:
        if _renewal_requested(request):
            intents.append("private_renewal")
        if _requested(_REPORT, request):
            intents.append("private_report")
        if _private_form_requested(request):
            intents.append("private_form")
    if _requested(_RESIDENCE, request):
        # '효력이 언제 생기나요' asks about a right, not filing time. A
        # completed registration followed by another task is not a request.
        for clause in _intent_clauses(request):
            if not _requested(_RESIDENCE, clause):
                continue
            completed = re.search(rf"(?:{_RESIDENCE}).{{0,10}}(?:했|마쳤|완료)", clause)
            if completed and not re.search(r"안\s*했|못\s*했|하지\s*(?:않|못)", completed[0]):
                continue
            tails = [_topic_tail(clause[match.end():], forms=False)
                     for match in re.finditer(_RESIDENCE, clause)]
            procedure = any(re.search(_PROCEDURE, tail) for tail in tails)
            if procedure and not re.search(r"(?:효력|대항력|우선\s*변제).{0,12}언제|언제.{0,12}(?:효력|대항력|우선\s*변제)", clause):
                intents.append("residence_procedure")
                break
        # A following sentence can ask who/when about the named procedures.
        if ("residence_procedure" not in intents
                and any(_requested(_RESIDENCE, clause)
                        and re.search(rf"(?:{_RESIDENCE}).{{0,15}}(?:같|차이)", clause)
                        for clause in _intent_clauses(request))
                and re.search(r"누가|언제|절차", request)):
            intents.append("residence_procedure")
    return tuple(intents)


def existing_member_union(retriever, query, k, where):
    """Keep the full RRF union without deepening or repeating member searches."""
    _, members = retriever.search_with_member_hits(query, max(k, retriever.depth), where)
    scores = {}
    for member in retriever.members:
        for rank, cid in enumerate(members[member.name or str(id(member))], 1):
            scores[cid] = scores.get(cid, 0.0) + member.weight / (retriever.rrf_k + rank)
    return sorted(scores.items(), key=lambda hit: (-hit[1], hit[0]))


def _verified_body(chunk):
    meta = chunk["metadata"]
    title, article = meta.get("title"), meta.get("article_no")
    if (meta.get("doc_type") != "law" or meta.get("status") != "current"
            or not title or not article or not meta.get("version") or not meta.get("effective_date")
            or meta.get("article_id") != title + "-" + article):
        return ""
    header = re.match(r"\[" + re.escape(title + " " + article) + r"(?:\([^\[\]]*\))?\]", chunk["text"])
    if not header:
        return ""
    body = chunk["text"][header.end():].strip()
    if re.search(r"(?m)^\s*\[", body):
        return ""
    return re.sub(r"\s", "", _QUOTED.sub(" ", body))


def _body_intents(chunk):
    body = _verified_body(chunk)
    if not body:
        return frozenset()
    found = set()
    private = "민간임대" in chunk["metadata"]["title"]
    # Require an operative predicate, not a list mentioning another article.
    statement = r"(?:^|[①-⑳㉑-㉟㊱-㊿])"
    if private and re.search(statement + r"임대사업자는[^.]*재계약을거절할수없다\.", body):
        found.add("private_renewal")
    if private and re.search(statement + r"임대사업자는[^.]*임대차계약[^.]*신고(?:또는변경신고)?를하여야한다\.", body):
        found.add("private_report")
    if private and re.search(statement + r"임대사업자가[^.]*표준임대차계약서를사용하여야한다\.", body):
        found.add("private_form")
    if (re.search(r"거주지를이동하면[^.]*전입한날부터[^.]*전입신고", body)
            and re.search(r"전입신고[^.]*하여야한다\.", body)):
        found.add("residence_procedure")
    return frozenset(found)


class LawIntentSelector:
    def __init__(self, chunks):
        self.chunks = {chunk["chunk_id"]: chunk for chunk in chunks}
        self.intents = {cid: _body_intents(chunk) for cid, chunk in self.chunks.items()}
        # Registration procedure and registration's housing effect are
        # distinct. Preserve an already selected direct effect provision.
        self.resident_effect = {cid for cid, chunk in self.chunks.items()
                               if all(term in _verified_body(chunk) for term in (
                                   "주택의인도", "주민등록", "제삼자에대하여효력이생긴다"))}

    def select(self, query, ranked, k, where=None):
        if k <= 0:
            return []
        requested = requested_law_intents(query)
        if not requested or k < INTENT_POLICY_CONFIG["minimum_budget"]:
            return ranked[:k]
        allowed = [hit for hit in ranked if hit[0] in self.chunks
                   and matches(self.chunks[hit[0]]["metadata"], where)]
        representatives = []
        for intent in requested:
            cid = next((cid for cid, _ in allowed if intent in self.intents[cid]), None)
            if cid and cid not in representatives:
                representatives.append(cid)
        if not representatives:
            return allowed[:k]
        request, _ = _request_and_facts(query)
        def directly_requested(cid):
            if "residence_procedure" not in requested:
                return False
            if cid in self.resident_effect:
                return True
            # Keep already selected direct procedures when a question asks
            # about several filings. A deeming provision is not the direct
            # filing rule and does not automatically consume another slot.
            meta = self.chunks[cid]["metadata"]
            title = re.sub(r"\s", "", meta.get("article_title", ""))
            return (bool(re.search(r"확정\s*일자", request)) and "확정일자" in title
                    and meta.get("doc_type") == "law"
                    or bool(re.search(_REPORT, request))
                    and bool(re.search(r"임대차.*신고", title)) and "의제" not in title)
        protected = [cid for cid, _ in allowed[:k] if directly_requested(cid)]
        required = list(dict.fromkeys(representatives + protected))
        if len(required) > k:
            return allowed[:k]
        # Retain the first hit whenever all independently requested concepts
        # still fit. Within each group the original fused order is stable.
        if allowed and len(set(required + [allowed[0][0]])) <= k:
            required.append(allowed[0][0])
        required = set(required)
        chosen = [hit for hit in allowed if hit[0] in required][:k]
        chosen.extend(hit for hit in allowed if hit[0] not in required and len(chosen) < k)
        selected = {cid for cid, _ in chosen}
        return [hit for hit in allowed if hit[0] in selected]
