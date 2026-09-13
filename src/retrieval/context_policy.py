"""Validated law expansion, record lookup, and same-edition civil reference policy.

Runtime code has no dependency on evaluation bundles or scripts.
"""


import hashlib
import re

from src.retrieval.terms import expand_civil, expand_law


_CIVIL_STATIC_RULES = (
    (r"공동\s*(?:명의|소유)|공유자|지분", "공유물의 관리 보존 공유자 지분 과반수"),
    (r"대리|위임|대신.{0,15}계약", "대리행위 대리권의 범위 본인 권한"),
    (r"등기|소유자|소유권", "부동산 물권변동 등기 물권취득"),
    (r"원상\s*(?:복구|회복)|벽지|장판|도배", "원상회복의무 철거권 임대차 준용규정"),
    (r"실수|과실|깨뜨|파손", "채무불이행 손해배상 고의 과실"),
    (r"특약|구두\s*합의|약정", "임의규정 당사자 의사표시"),
)


_LAW_STATIC_RULES = (
    (r"임대차\s*신고|계약\s*신고", "주택 임대차 계약 신고 확정일자 부여"),
    (r"중개사|중개\s*보수|중개\s*수수료|확인[·\s]*설명서", "중개대상물 확인 설명 중개보수"),
)


def _current_request(query: str) -> str:
    """Return the current user question, excluding quoted/earlier dialogue."""
    return query.rsplit("사용자 질문:", 1)[-1].strip()


def _current_context(query: str) -> str:
    """Keep current facts, but exclude a quoted earlier conversation."""
    if "사용자 추가 상황:" in query:
        return query.rsplit("사용자 추가 상황:", 1)[-1].strip()
    if "이전 대화" in query:
        return _current_request(query)
    return query


def _communication_concept(query: str) -> bool:
    """Require a positive notice purpose/action in one unquoted clause.

    Ambiguous negation/quotation is withheld rather than interpreted as a legal
    fact. This is a retrieval expansion gate, not a notice-validity judgement.
    """
    current = _current_request(query)
    current = re.sub(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」|\'[^\'\n]*\'', " ", current)
    for clause in re.split(r"[.!?\n;]|(?:지만|는데|고서|반면|대신)\s*", current):
        # Negated notice/purpose is different from a sent notice failing to
        # arrive. Do not reject every occurrence of '않' (e.g. 전달되지 않고).
        if re.search(
            r"(?:해지|종료|갱신|반환\s*요구|통지|통보|발송)(?:를|을|는|은|가|이)?\s*"
            r"(?:하지(?:는)?\s*(?:않|못)|(?:안|못)\s*(?:했|하)|아니)"
            r"|(?:알리|보내)지(?:는)?\s*(?:않|못)", clause,
        ):
            continue
        delivery = re.search(r"내용증명|등기\s*우편|우편|문자|통보|통지|연락", clause)
        purpose = re.search(
            r"(?:계약|임대차).{0,15}(?:끝|종료|해지|갱신|나가|퇴거)"
            r"|(?:끝|종료|해지|갱신|나가|퇴거).{0,15}(?:계약|임대차)"
            r"|보증금.{0,20}(?:돌려|반환|요구|받)"
            r"|(?:돌려|반환|요구).{0,20}보증금", clause,
        )
        action = re.search(r"보내|보낸|전달|도달|반송|돌아왔|수취|알리|통지|통보", clause)
        if delivery and purpose and (action or "내용증명" in clause):
            return True
    return False


def _service_concept(query: str) -> bool:
    current = _current_request(query)
    return bool(re.search(
        r"반송|(?:우편|편지|내용증명|서류).{0,40}돌아왔"
        r"|소재.{0,8}모르|주소.{0,8}모르|행방",
        current,
    ))


def civil_concepts(query: str) -> list[str]:
    found = [term for pattern, term in _CIVIL_STATIC_RULES if re.search(pattern, query)]
    if _communication_concept(query):
        found.append("의사표시 도달 효력발생시기")
    if _service_concept(query):
        found.append("의사표시 공시송달 상대방 소재")
    if re.search(r"보증금", query) and re.search(r"이사|퇴거|비우|인도|짐", query):
        found.append("쌍무계약 동시이행의 항변권 채무이행 거절")
    return list(dict.fromkeys(found))


def law_concepts(query: str) -> list[str]:
    current = _current_context(query)
    request = _current_request(query)
    found = [term for pattern, term in _LAW_STATIC_RULES if re.search(pattern, request)]
    if re.search(r"체납|미납.{0,8}(?:세금|국세|지방세)|세금.{0,8}(?:밀|안\s*낸|미납)", current):
        found.append("미납국세 미납지방세 열람 납세증명")
    if re.search(r"전입|주민등록|주소.{0,8}(?:옮|이전)", current):
        found.append("주민등록 전입신고 주택 인도 대항력")
    public = re.search(r"공공\s*임대|국민\s*임대|행복\s*주택", current)
    if public:
        found.append("공공임대주택 입주자 자격 임대차계약")
    if (re.search(r"등록\s*민간\s*임대|민간\s*임대", current)
            or (not public and re.search(r"임대\s*사업자", current))):
        found.append("민간임대주택 임대사업자 임대료 임대차계약")
    if "확정일자" in request:
        found.append(
            "확정일자 부여 현황 정보제공"
            if re.search(r"어디|언제|신청|발급|방법|현황|열람|다시.{0,8}(?:받|챙)", request)
            else "확정일자 우선변제 주택 인도 주민등록"
        )
    return list(dict.fromkeys(found))


def context_terms(query: str, channel: str) -> list[str]:
    base = expand_law(query) if channel == "general" else expand_civil(query)
    extra = law_concepts(query) if channel == "general" else civil_concepts(query)
    return list(dict.fromkeys(base + extra))


def dense_context_query(query: str, channel: str) -> str:
    extra = law_concepts(query) if channel == "general" else civil_concepts(query)
    return query + "\n관련 검색 개념: " + "; ".join(extra) if extra else query


def fuse(bm25, dense, dense_weight=1):
    scores = {}
    for ids, weight in ((bm25, 1), (dense, dense_weight)):
        for rank, cid in enumerate(ids, 1):
            scores[cid] = scores.get(cid, 0) + weight / (5 + rank)
    return sorted(scores, key=lambda cid: (-scores[cid], cid))


_ARTICLE_REF = r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?"


_REF_ITEM = _ARTICLE_REF + r"(?:\s*제\s*\d+\s*항)?"


def _direct_references(chunk: dict) -> set[str]:
    """Accept only same-law, complete adoption clauses, never arbitrary mentions."""
    from src.generation.paragraph import _HISTORY

    meta = chunk["metadata"]
    header = re.escape("[민법 " + meta["article_no"])
    matched = re.match(header + r"(?:\([^\[\]]*\))?\]", chunk["text"])
    if not matched:
        return set()
    body = []
    for line in chunk["text"][matched.end():].splitlines():
        line = line.strip()
        if not line or _HISTORY.fullmatch(line):
            continue
        if any(mark in line for mark in ('[', ']', '「', '」', '“', '”', '"', "'")):
            return set()
        body.append(line)
    # Restrict the entire body to an explicit same-law adoption statement.
    # Unsupported prose is intentionally unlinked, not guessed from numbers.
    item = r"(?:민법\s*)?" + _REF_ITEM
    references = item + r"(?:\s*(?:,|및|와|과|내지)\s*" + item + r")*"
    match = re.fullmatch(
        r"(?P<refs>" + references + r")\s*의\s*규정은\s+"
        r"[^.\[\]제]*준용한다\.", " ".join(body),
    )
    if not match:
        return set()
    text = match["refs"]
    found = {f"민법-제{int(n)}조" + (f"의{int(b)}" if b else "")
             for n, b in re.findall(_ARTICLE_REF, text)}
    for start, branch, end, end_branch in re.findall(
        _ARTICLE_REF + r"\s*내지\s*" + _ARTICLE_REF, text,
    ):
        if branch or end_branch or not 0 < int(start) <= int(end) <= int(start) + 200:
            return set()
        found.update(f"민법-제{n}조" for n in range(int(start), int(end) + 1))
    return found


def _reference_graph(chunks: list[dict], civil_ids: tuple[str, ...]) -> tuple[dict[str, set[str]], list[dict]]:
    """Link only exact identities in the same verified current edition."""
    civil = {c["metadata"]["article_id"]: c for c in chunks
             if c["metadata"].get("title") == "민법"
             and c["metadata"].get("article_id") in set(civil_ids)}
    graph = {article: set() for article in civil}
    evidence = []
    for source, chunk in civil.items():
        meta = chunk["metadata"]
        if (meta.get("article_title") != "준용규정" or meta.get("status") != "current"
                or not meta.get("version") or not meta.get("effective_date")
                or source != "민법-" + meta.get("article_no", "")):
            continue
        for target in sorted(_direct_references(chunk)):
            if target not in civil or target == source:
                continue
            target_meta = civil[target]["metadata"]
            if (target_meta.get("status") != "current"
                    or target != "민법-" + target_meta.get("article_no", "")
                    or any(target_meta.get(k) != meta[k] for k in ("version", "effective_date"))):
                continue
            graph[source].add(target)
            graph[target].add(source)
            evidence.append({"source": source, "target": target,
                              "source_chunk": chunk["chunk_id"],
                              "body_sha256": hashlib.sha256(chunk["text"].encode()).hexdigest(),
                              "target_chunk": civil[target]["chunk_id"],
                              "version": meta["version"], "effective_date": meta["effective_date"]})
    return graph, evidence


def _tail_select(row: dict, bm25: list[str], dense: list[str]) -> list[str]:
    ranked = fuse(bm25, dense)
    picked = list(dict.fromkeys(row["civil_seed"][:2] + ranked))[:3]
    if len(picked) == 3:
        tail = next((cid for cid in fuse(bm25, dense, 2) if cid not in picked[:2]), None)
        if tail:
            picked[2] = tail
    return picked


def _reference_select(row: dict, anchors: dict[str, str], graph: dict[str, set[str]]) -> tuple[list[str], list[dict]]:
    bm25 = row["civil"]["bm25_context"]
    # Step 2 extends the surviving Step 1 candidate; it does not mix the best
    # numbers from separate policies after evaluation.
    dense = row["civil"]["dense_context"]
    ranked = fuse(bm25, dense)
    positions = {anchors[cid]: i for i, cid in enumerate(ranked, 1)}
    by_anchor = {anchor: cid for cid, anchor in anchors.items()}
    pairs = []
    for source, linked in graph.items():
        for target in linked:
            if source >= target or source not in positions or target not in positions:
                continue
            ranks = (positions[source], positions[target])
            if min(ranks) <= 3 and max(ranks) <= 10:
                pairs.append((max(ranks), sum(ranks), source, target))
    if not pairs:
        return _tail_select(row, bm25, dense), []
    _, _, source, target = min(pairs)
    pair_ids = sorted((by_anchor[source], by_anchor[target]), key=ranked.index)
    selected = list(dict.fromkeys(row["civil_seed"][:1] + pair_ids + ranked))[:3]
    return selected, [{"source": source, "target": target,
                       "source_rank": positions[source], "target_rank": positions[target]}]


RECORDS = "확정일자 부여 현황 정보제공"


EFFECT = "확정일자 우선변제 주택 인도 주민등록"


def requests_date_record(query: str) -> bool:
    request = _current_request(query)
    request = re.sub(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」', " ", request)
    previous_date_record = False
    for clause in filter(str.strip, re.split(r"[.!?\n;]", request)):
        explicit = bool(re.search(
            r"확정일자(?:의|를|는|가)?\s*(?:부여\s*)?(?:기록|내역|여부|받았는지|유무)", clause,
        ))
        reference = previous_date_record and re.search(r"(?:예전|그때|당시)(?:의)?\s*(?:기록|내역)", clause)
        if ((explicit or reference) and re.search(r"확인|조회|열람|찾", clause)
                and not re.search(r"효력|대항력|우선\s*변제|보호|아니라", clause)):
            return True
        previous_date_record = explicit and not re.search(r"아니라|묻지|관계없", clause)
    return False


def final_law_concepts(query: str) -> list[str]:
    terms = law_concepts(query)
    if requests_date_record(query):
        terms = [term for term in terms if term != EFFECT]
        terms.append(RECORDS)
    return list(dict.fromkeys(terms))


def final_law_terms(query: str) -> list[str]:
    return list(dict.fromkeys(expand_law(query) + final_law_concepts(query)))


def final_dense_query(query: str) -> str:
    extra = final_law_concepts(query)
    return query + "\n관련 검색 개념: " + "; ".join(extra) if extra else query
