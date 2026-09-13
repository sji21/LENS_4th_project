"""Evaluate context-gated expansion and one-hop Civil Act reference candidates.

This remains a development-only PATCH-027 experiment.  It never reads a gold
article while selecting a result and it does not mutate the operating corpus.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from scripts.patch015_baseline import ROOT, norm, read, sha, write
from scripts.patch025_ranking import close
from scripts.patch026_expand import score
from src.retrieval.partitioned import PartitionedBM25Retriever
from src.retrieval.retriever import BM25Retriever, load_chunks
from src.retrieval.service import CIVIL as CIVIL_CORPUS, LAW, PROCEDURE_TITLES, route_law_corpus
from src.retrieval.terms import expand_civil, expand_law


BUNDLE = ROOT / "data/eval/patch027-context-tuning"
DEPENDENCIES = (
    "data/eval/patch015-baseline/capture/results.json",
    "data/eval/patch026-full/capture/audit.json",
    "data/eval/patch026-full/capture/before.json",
    "data/eval/patch026-full/capture/after.json",
    "data/eval/patch026-full/capture/new-chunks.json",
    "data/eval/patch027-concept-tuning/manifest.json",
    "data/eval/patch027-full-ranking/manifest.json",
)
GENERAL = ("current", "context_both", "context_blend")
CIVIL_POLICIES = ("lexical_control", "context_lexical", "context_both", "context_reference")
# Freeze the reviewed finalist before corrected measurements; do not reselect
# it using the new scores. Other policies remain diagnostic controls.
FINAL_POLICIES = ("context_both", "context_reference")
CANDIDATE_CIVIL_IDS = tuple(read(
    ROOT / "data/eval/patch026-full/capture/audit.json"
)["candidate_settings"]["corpora"]["civil"]["include_ids"])
CANDIDATE_CIVIL = replace(CIVIL_CORPUS, include_ids=CANDIDATE_CIVIL_IDS)

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


def merge_ranks(*lists):
    scores = {}
    for ids in lists:
        for rank, cid in enumerate(ids, 1):
            scores[cid] = scores.get(cid, 0) + 1 / (5 + rank)
    return sorted(scores, key=lambda cid: (-scores[cid], cid))


def adoption_check(result):
    groups = result["groups"]
    required = groups["required_law"]
    civil = required["channel_metrics"]["civil"]["all_target_items"]
    checks = {
        "prior_returned_targets_preserved": not result["losses"],
        "prior_general_top3_preserved": result["top3_general_loss_inputs"] == 0,
        "question_complete_not_lower": groups["question_only"]["union_all_required"]["hits"] >= 32,
        "context_complete_not_lower": groups["context_diagnostic"]["union_all_required"]["hits"] >= 34,
        "civil35_complete_preserved": required["union_all_required"]["n"] == 29
        and required["union_all_required"]["hits"] >= 28,
        "civil35_hit3_preserved": civil["n"] == 29 and civil["hit@3"] == 1,
    }
    return {"checks": checks, "passed": all(checks.values())}


def rank_changes(before, after, details):
    targets = {(d["qid"], d["mode"]): set(d["targets"]) for d in details
               if d["category"] not in ("historical_review", "no_fixed_target")}
    old = {(row["qid"], row["mode"]): row for row in before}
    changes = {channel: {str(k): [] for k in depths} for channel, depths in
               (("laws", (1, 3, 5)), ("civil_laws", (1, 3)))}
    for row in after:
        key = row["qid"], row["mode"]
        gold = targets.get(key, set())
        for channel, depths in changes.items():
            for depth, entries in depths.items():
                previous = set(old[key][channel][:int(depth)]) & gold
                current = set(row[channel][:int(depth)]) & gold
                if previous != current:
                    entries.append({"qid": row["qid"], "mode": row["mode"],
                                    "lost": sorted(previous - current),
                                    "gained": sorted(current - previous)})
    return changes


def _previous_lexical(row: dict) -> list[str]:
    return _tail_select(row, row["civil"]["bm25_expanded"], row["civil"]["dense_original"])


def _law_bm25(chunks: list[dict], expander) -> PartitionedBM25Retriever:
    laws = [c for c in chunks if c["metadata"].get("doc_type") in LAW.doc_types
            and c["metadata"].get("title") != "민법"]
    procedure = [c for c in laws if c["metadata"].get("title") in PROCEDURE_TITLES]
    core = [c for c in laws if c["metadata"].get("title") not in PROCEDURE_TITLES]
    lexical = lambda part: BM25Retriever(part, b=LAW.bm25_b, query_expander=expander)
    return PartitionedBM25Retriever(
        {"core": lexical(core), "procedure": lexical(procedure)}, PROCEDURE_TITLES,
    )


def _civil_bm25(chunks: list[dict], expander) -> BM25Retriever:
    allowed = set(CANDIDATE_CIVIL_IDS)
    civil = [c for c in chunks if c["metadata"].get("title") == "민법"
             and c["metadata"].get("article_id") in allowed]
    return BM25Retriever(civil, b=CIVIL_CORPUS.bm25_b, query_expander=expander)


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


def _reference_graph(chunks: list[dict]) -> tuple[dict[str, set[str]], list[dict]]:
    """Link only exact identities in the same verified current edition."""
    civil = {c["metadata"]["article_id"]: c for c in chunks
             if c["metadata"].get("title") == "민법"
             and c["metadata"].get("article_id") in set(CANDIDATE_CIVIL_IDS)}
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


def general_select(row: dict, policy: str) -> list[str]:
    if policy == "current":
        return row["current_laws"]
    if policy not in GENERAL:
        raise ValueError("Unknown general policy")
    tuned = fuse(row["general"]["bm25_context"], row["general"]["dense_context"])
    if policy == "context_blend":
        original = fuse(row["general"]["bm25_original"], row["general"]["dense_original"])
        tuned = merge_ranks(original[:20], tuned[:20])
    return tuned[:5]


def civil_select(row: dict, policy: str, anchors: dict[str, str], graph: dict[str, set[str]]) -> tuple[list[str], list[dict]]:
    if policy == "current":
        return row["current_civil"], []
    if policy == "lexical_control":
        return _previous_lexical(row), []
    if policy not in CIVIL_POLICIES:
        raise ValueError("Unknown civil policy")
    if policy == "context_reference":
        return _reference_select(row, anchors, graph)
    dense = row["civil"]["dense_context"] if policy == "context_both" else row["civil"]["dense_original"]
    return _tail_select(row, row["civil"]["bm25_context"], dense), []


def capture(out: Path, candidate: Path, *, allow_dirty: bool = False) -> None:
    from scripts.patch025_ranking import index_digest
    from scripts.patch027_full_ranking import check_bundle
    from scripts.patch027_tuning import check as check_concept_tuning
    from src.retrieval.dense import ChromaRetriever, SentenceTransformerEmbedding
    if out.exists() or not out.resolve().is_relative_to(ROOT / "tmp"):
        raise ValueError("Use a fresh tmp output")
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    if dirty and not allow_dirty:
        raise ValueError("Commit source first")
    check_bundle()
    check_concept_tuning()
    from scripts.patch027_context_live import execution_spec, verify_live
    spec = execution_spec()
    out.mkdir(parents=True)
    write(out / "execution-spec.json", spec)
    prior_audit = read(ROOT / "data/eval/patch026-full/capture/audit.json")
    if any(sha(candidate / rel) != digest for rel, digest in prior_audit["candidate_files"].items()):
        raise ValueError("Candidate data changed")
    chunks = [c for name in ("chunks", "cases", "guides")
              for c in load_chunks(candidate / f"chunks/{name}.jsonl")]
    old_rows = read(ROOT / "data/eval/patch027-concept-tuning/traces.json")
    queries = read(ROOT / DEPENDENCIES[0])
    law_original = _law_bm25(chunks, expand_law)
    law_context = _law_bm25(chunks, lambda q: context_terms(q, "general"))
    civil_original = _civil_bm25(chunks, expand_civil)
    civil_context = _civil_bm25(chunks, lambda q: context_terms(q, "civil"))
    graph, reference_evidence = _reference_graph(chunks)
    backend = SentenceTransformerEmbedding("nlpai-lab/KURE-v1")
    model_root = Path.home() / ".cache/huggingface/hub/models--nlpai-lab--KURE-v1"
    if any(sha(model_root / rel) != digest for rel, digest in prior_audit["model_files"].items()):
        raise ValueError("Embedding model changed")
    original_embed = backend.embed
    embedding_cache = {}

    def cached_embed(texts):
        key = tuple(texts)
        if key not in embedding_cache:
            embedding_cache[key] = original_embed(texts)
        return embedding_cache[key]

    backend.embed = cached_embed
    law_dense = ChromaRetriever(backend, candidate / "index/chroma_kurev1_1024")
    civil_dense = ChromaRetriever(backend, candidate / "index/chroma_civil_kurev1_1024")
    index_hashes = [index_digest(retriever) for retriever in (law_dense, civil_dense)]
    if index_hashes != prior_audit["candidate_index_hashes"]:
        raise ValueError("Candidate index changed")
    rows = []
    for query, old in zip(queries, old_rows):
        text = query["query"]
        where = route_law_corpus(text).where()
        lb = [cid for cid, _ in law_original.search(text, 20, where, LAW.expand_weight)]
        cb = [cid for cid, _ in civil_original.search(
            text, 26, CANDIDATE_CIVIL.where(), CANDIDATE_CIVIL.expand_weight)]
        if lb != old["general"]["bm25_original"] or cb != old["civil"]["bm25_original"]:
            raise ValueError(f"BM25 baseline drift: {query['qid']} {query['mode']}")
        row = {key: old[key] for key in ("qid", "mode", "query_sha256", "current_laws", "current_civil", "civil_seed")}
        row["core"] = old["core"]
        row["expansion"] = {"general": law_concepts(text), "civil": civil_concepts(text)}
        row["general"] = {
            "bm25_original": lb,
            "dense_original": old["general"]["dense_original"],
            "bm25_context": [cid for cid, _ in law_context.search(text, 20, where, LAW.expand_weight)],
            "dense_context": [cid for cid, _ in law_dense.search(
                dense_context_query(text, "general"), 20, where)]
            if row["expansion"]["general"] else old["general"]["dense_original"],
        }
        row["civil"] = {
            "bm25_original": cb,
            "dense_original": old["civil"]["dense_original"],
            "bm25_expanded": old["civil"]["bm25_expanded"],
            "dense_expanded": old["civil"]["dense_expanded"],
            "bm25_context": [cid for cid, _ in civil_context.search(
                text, 26, CANDIDATE_CIVIL.where(), CANDIDATE_CIVIL.expand_weight)],
            "dense_context": [cid for cid, _ in civil_dense.search(
                dense_context_query(text, "civil"), 26, CANDIDATE_CIVIL.where())]
            if row["expansion"]["civil"] else old["civil"]["dense_original"],
        }
        rows.append(row)
        if len(rows) % 25 == 0:
            print(f"{len(rows)}/235 context-gated traces", flush=True)
    if [index_digest(retriever) for retriever in (law_dense, civil_dense)] != index_hashes:
        raise ValueError("Candidate index mutated")
    anchors = read(ROOT / "data/eval/patch027-full-ranking/audit.json")["anchors"]
    # Fresh service calls, not copied cases/guides or a second score aggregation.
    # Clear the trace-collection cache before measuring the selected adapter.
    embedding_cache.clear()
    live = verify_live(chunks, law_dense, civil_dense, rows, anchors, graph, out, original_embed)
    if execution_spec() != spec:
        raise ValueError("Execution source/config changed during capture")
    if (any(sha(candidate / rel) != digest for rel, digest in prior_audit["candidate_files"].items())
            or [index_digest(r) for r in (law_dense, civil_dense)] != index_hashes):
        raise ValueError("Candidate changed during live verification")
    write(out / "traces.json", rows)
    write(out / "audit.json", {
        "patch": "PATCH-027", "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "clean": not dirty, "preview": dirty, "inputs": 235, "candidate_unchanged": True,
        "cases_guides_preserved": True, "anchors": anchors,
        "index_hashes": index_hashes, "model_files": prior_audit["model_files"],
        "general_policies": GENERAL, "civil_policies": CIVIL_POLICIES,
        "final_policies": FINAL_POLICIES, "live_verified_inputs": live["inputs"],
        "execution_spec_sha256": sha(out / "execution-spec.json"),
        "reference_evidence": reference_evidence,
        "dependencies": {path: sha(ROOT / path) for path in DEPENDENCIES},
        "script_sha256": sha(Path(__file__)), "traces_sha256": sha(out / "traces.json"),
    })
    result = report(out, require_clean=False)
    write(out / "comparison.json", result)
    write(out / "manifest.json", {p: sha(out / p) for p in BUNDLE_FILES})


def _assess(rows, anchors, graph, general_policy, civil_policy):
    prior = read(ROOT / "data/eval/patch026-full/capture/before.json")
    current = read(ROOT / "data/eval/patch026-full/capture/after.json")
    available = read(ROOT / "data/eval/patch026-full/capture/audit.json")["available_after"]
    available_before = set(read(ROOT / "data/eval/patch026-full/capture/audit.json")["available_before"])
    new_available = set(available) - available_before
    actual = []
    linked = []
    for index, row in enumerate(rows):
        civil, refs = civil_select(row, civil_policy, anchors, graph)
        actual.append({**current[index], "laws": [anchors[c] for c in general_select(row, general_policy)],
                       "civil_laws": [anchors[c] for c in civil]})
        if refs:
            linked.append({"qid": row["qid"], "mode": row["mode"], "links": refs})
    result = score(actual, available, prior)
    changes = rank_changes(prior, actual, result["details"])
    new_hits = []
    for row, detail in zip(actual, result["details"]):
        targets = set(detail["targets"])
        laws = sorted(set(row["laws"]) & targets & new_available)
        civil = sorted(set(row["civil_laws"]) & targets & new_available)
        if laws or civil:
            new_hits.append({"qid": row["qid"], "mode": row["mode"],
                             "laws": laws, "civil_laws": civil})
    context_dense = (
        (sum(bool(row["expansion"]["general"]) for row in rows)
         if general_policy in ("context_both", "context_blend") else 0)
        + (sum(bool(row["expansion"]["civil"]) for row in rows)
           if civil_policy in ("context_both", "context_reference") else 0)
    )
    summary = {"groups": result["groups"], "losses": result["losses"], "rank_changes": changes,
               "top3_general_loss_inputs": sum(bool(r["lost"]) for r in changes["laws"]["3"]),
               "new_loss_vs_full": score(actual, available, current)["losses"],
               "new_required_hits": new_hits, "reference_promotions": linked,
               "cost": {"dense_context_inputs": context_dense,
                        "embedding_call_measurement": "live-verification.json (measured separately)",
                        "reference_candidate_inputs": len(linked),
                        "maximum_reference_additions_per_input": 2 if civil_policy == "context_reference" else 0}}
    summary["adoption"] = adoption_check(summary)
    return summary


def _validated_reference_graph(audit: dict) -> dict[str, set[str]]:
    canonical = read(ROOT / "data/eval/patch027-full-ranking/audit.json")["anchors"]
    if audit["anchors"] != canonical:
        raise ValueError("Anchor identity changed")
    # All adoption-clause sources in this frozen candidate are newly collected
    # articles. Require exactly the graph derivable from that verified bundle.
    graph, expected = _reference_graph(read(ROOT / "data/eval/patch026-full/capture/new-chunks.json"))
    if audit["reference_evidence"] != expected:
        raise ValueError("Reference endpoints/edition differ from verified source")
    return graph


def report(run: Path, *, require_clean: bool = True) -> dict:
    from scripts.patch027_full_ranking import check_bundle
    from scripts.patch027_tuning import check as check_concept_tuning

    check_bundle()
    check_concept_tuning()
    audit = read(run / "audit.json")
    rows = read(run / "traces.json")
    from scripts.patch027_context_live import execution_spec, check_live
    if (sha(run / "execution-spec.json") != audit["execution_spec_sha256"]
            or read(run / "execution-spec.json") != execution_spec()):
        raise ValueError("Execution source/config mismatch")
    if require_clean and not audit["clean"]:
        raise ValueError("Preview capture is not a final shared result")
    if audit["clean"]:
        source = subprocess.check_output(
            ["git", "show", audit["commit"] + ":scripts/patch027_context_tuning.py"], cwd=ROOT,
        )
        digests = {hashlib.sha256(source).hexdigest(),
                   hashlib.sha256(source.replace(b"\n", b"\r\n")).hexdigest()}
        if audit["script_sha256"] not in digests:
            raise ValueError("Capture source mismatch")
    if sha(run / "traces.json") != audit["traces_sha256"] or audit["inputs"] != 235 or len(rows) != 235:
        raise ValueError("Trace changed")
    if (tuple(audit["general_policies"]) != GENERAL or tuple(audit["civil_policies"]) != CIVIL_POLICIES
            or tuple(audit["final_policies"]) != FINAL_POLICIES):
        raise ValueError("Policies changed")
    if set(audit["dependencies"]) != set(DEPENDENCIES) or any(
        sha(ROOT / path) != digest for path, digest in audit["dependencies"].items()
    ):
        raise ValueError("Dependency changed")
    queries = read(ROOT / DEPENDENCIES[0])
    if [(r["qid"], r["mode"], r["query_sha256"]) for r in rows] != [
        (q["qid"], q["mode"], q["query_sha256"]) for q in queries
    ]:
        raise ValueError("Input identity changed")
    old_rows = read(ROOT / "data/eval/patch027-concept-tuning/traces.json")
    identity = read(ROOT / "data/eval/patch027-full-ranking/audit.json")
    allowed = {"general": set(identity["core_ids"] + identity["extra_ids"]),
               "civil": set(identity["civil_ids"])}
    for row, query, old in zip(rows, queries, old_rows):
        expected = {"general": law_concepts(query["query"]),
                    "civil": civil_concepts(query["query"])}
        if row["expansion"] != expected:
            raise ValueError("Context rule trace changed")
        if any(row[k] != old[k] for k in ("current_laws", "current_civil", "civil_seed", "core")):
            raise ValueError("Stored baseline changed")
        for channel in ("general", "civil"):
            keys = {"bm25_original", "dense_original", "bm25_context", "dense_context"}
            if channel == "civil":
                keys |= {"bm25_expanded", "dense_expanded"}
            if set(row[channel]) != keys:
                raise ValueError("Missing member trace")
            for key, hits in row[channel].items():
                if (len(hits) > (20 if channel == "general" else 26)
                        or len(set(hits)) != len(hits) or not set(hits) <= allowed[channel]):
                    raise ValueError("Invalid channel/member trace")
                if key.endswith(("original", "expanded")) and hits != old[channel][key]:
                    raise ValueError("Stored control changed")
    prior_audit = read(ROOT / "data/eval/patch026-full/capture/audit.json")
    if (audit["candidate_unchanged"] is not True or audit["cases_guides_preserved"] is not True
            or audit["index_hashes"] != prior_audit["candidate_index_hashes"]
            or audit["model_files"] != prior_audit["model_files"]):
        raise ValueError("Candidate capture contract changed")
    anchors = audit["anchors"]
    graph = _validated_reference_graph(audit)
    check_live(run, rows, anchors, graph)
    if audit["live_verified_inputs"] != 235:
        raise ValueError("Incomplete live verification")
    general = {policy: _assess(rows, anchors, graph, policy, "current") for policy in GENERAL}
    civil = {policy: _assess(rows, anchors, graph, "current", policy) for policy in CIVIL_POLICIES}
    general_final, civil_final = FINAL_POLICIES
    joint = _assess(rows, anchors, graph, general_final, civil_final)
    return {"general": general, "civil": civil,
            "finalists": {"general": general_final, "civil": civil_final},
            "joint": {f"{general_final}+{civil_final}": joint}}


BUNDLE_FILES = ("audit.json", "traces.json", "comparison.json", "execution-spec.json",
                "live-verification.json", "evidence-catalog.json")


def check(run: Path = BUNDLE) -> dict:
    manifest = read(run / "manifest.json")
    if set(manifest) != set(BUNDLE_FILES):
        raise ValueError("Incomplete bundle")
    if any(sha(run / path) != digest for path, digest in manifest.items()):
        raise ValueError("Bundle changed")
    result = report(run)
    if not close(result, read(run / "comparison.json")):
        raise ValueError("Comparison does not replay")
    return result


def _print(result: dict) -> None:
    for channel in ("general", "civil"):
        for policy, row in result[channel].items():
            groups = row["groups"]
            print(channel, policy,
                  "complete", groups["question_only"]["union_all_required"]["hits"],
                  groups["context_diagnostic"]["union_all_required"]["hits"],
                  "loss", len(row["losses"]), "law3loss", row["top3_general_loss_inputs"],
                  "reference", len(row["reference_promotions"]), "adopt", row["adoption"]["passed"])
    name, joint = next(iter(result["joint"].items()))
    print("joint", name, "complete",
          joint["groups"]["question_only"]["union_all_required"]["hits"],
          joint["groups"]["context_diagnostic"]["union_all_required"]["hits"],
          "loss", len(joint["losses"]), "law3loss", joint["top3_general_loss_inputs"],
          "adopt", joint["adoption"]["passed"])


if __name__ == "__main__":
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      LANGSMITH_TRACING="false", ANONYMIZED_TELEMETRY="False")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.capture:
        capture(args.capture, args.candidate, allow_dirty=args.preview)
        _print(report(args.capture, require_clean=not args.preview))
    elif args.report:
        _print(report(args.report, require_clean=False))
    else:
        _print(check())
