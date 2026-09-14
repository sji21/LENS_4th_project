"""Untrusted planner output -> a bounded, source-attributed decision.

Representation and explicit contradictions can be checked deterministically.
Semantic interpretation still requires evaluation; JSON validity is not truth.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import re

from .dialogue_state import apply_user_update, ensure_dialogue, _safe

INTENTS = {"greeting", "question", "followup", "correction", "clarification_answer", "explain", "topic_change", "document_question"}
ACTIONS = {"social", "clarify", "rag", "refuse"}
BOOL_FIELDS = {"contract_ended", "deposit_returned", "living_in_property", "moved_out", "landlord_notified"}
FACT_FIELDS = BOOL_FIELDS | {"contract_type", "subject", "role", "property_type", "deposit", "monthly_rent", "end_date", "start_date", "notice_date"}
CLARIFY_FIELDS = FACT_FIELDS | {"details", "document"}
STYLES = {"standard", "simple", "brief"}
KEYS = {"intent", "action", "topic", "topic_changed", "updates", "clarify_field", "question", "search_query", "document_id", "style"}
ROLE_ALIASES = {"임대인": ("임대인", "집주인"), "임차인": ("임차인", "세입자"), "중개사": ("중개사", "중개인"), "대리인": ("대리인",)}
PROPERTY_ALIASES = {name: (name,) for name in ("아파트", "빌라", "단독주택", "다가구주택", "다세대주택", "연립주택", "오피스텔", "상가", "기숙사", "고시원")}
PROPERTY_ALIASES["주택"] = ("주택", "집")


class DecisionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Invalid conversation decision: {code}")


@dataclass(frozen=True)
class Decision:
    intent: str
    action: str
    topic: str | None
    topic_changed: bool
    updates: dict
    clarify_field: str | None
    question: str | None
    search_query: str
    document_id: str | None
    style: str


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DecisionError("duplicate_key")
        result[key] = value
    return result


def _numbers(text):
    return {token.replace(",", "") for token in re.findall(r"\d+(?:[,.]\d+)*", text)}


def _quantities(text):
    number = r"\d+(?:\.\d+)?"
    magnitude = r"[조억만천백십]"
    compound = number + r"(?:" + magnitude + r"+" + number + r")*"
    arabic = compound + magnitude + r"*(?:원|년|개월|월|일|평|퍼센트|%)"
    money_without_won = compound + magnitude + r"+(?:원)?"
    # Positional units distinguish amounts from ordinary words such as 이사일/만일.
    hangul = r"(?=[일이삼사오육칠팔구십백천만억조]*[십백천만억조])[일이삼사오육칠팔구십백천만억조]+원"
    return set(re.findall("|".join((arabic, money_without_won, hangul)), re.sub(r"[\s,]", "", text)))


def _enum(value, choices, code):
    if not isinstance(value, str) or value not in choices:
        raise DecisionError(code)


def build_decision_input(state, user, document_id=None):
    """Select existing user memory and public answer, never evaluation labels/OCR instructions."""
    if not isinstance(user, str) or not user.strip() or len(user) > 2000:
        raise DecisionError("user")
    documents = [{"document_id": d["document_id"], "kind": d["kind"], "label": d.get("label", "문서")} for d in state.get("documents", [])]
    if document_id is not None and document_id not in {d["document_id"] for d in documents}:
        raise DecisionError("document")
    dialogue = ensure_dialogue(deepcopy(state))
    answer = dialogue["last_answer"]
    if answer and answer.get("validation") == "existing_pipeline_passed":
        answer = {"content": _safe(str(answer.get("content", "")))[:2000], "turn": answer.get("turn"),
                  **({"request": _safe(answer["request"])} if isinstance(answer.get("request"), str) else {})}
    else:
        answer = None
    history = dialogue["history"][-4:]
    if not history and dialogue["turn"] == 0 and dialogue["epoch"] == 0:
        # Old sessions retain user messages, including statements before abstention.
        # Assistant generations are not inferred as user facts during migration.
        messages = state.get("messages", [])
        history = [
            {"role": "user", "content": _safe(m["content"])[:2000]}
            for index, m in enumerate(messages)
            if m.get("role") == "user"
            and not (index + 1 < len(messages)
                     and messages[index + 1].get("role") == "assistant"
                     and messages[index + 1].get("status") == "refused")
        ][-4:]
    return {
        "user": _safe(user), "topic": dialogue["topic"], "epoch": dialogue["epoch"],
        "facts": deepcopy(dialogue["facts"]), "history": deepcopy(history),
        "pending": deepcopy(dialogue["pending"]), "last_answer": answer,
        "last_status": dialogue["last_status"], "documents": documents,
        "active_document_id": document_id or dialogue["active_document_id"],
    }


def previous_answer_query(dialogue, intent):
    answer = dialogue.get("last_answer") or {}
    query = answer.get("request") if intent == "followup" else None
    if not isinstance(query, str) or not query.strip():
        query = answer.get("query")
    if intent in {"explain", "followup"} and answer.get("validation") == "existing_pipeline_passed" and isinstance(query, str) and len(query) <= 2000:
        return query
    return ""


def pending_request(dialogue, intent):
    """Only reuse the user request tied to the current case/document question."""
    pending = dialogue.get("pending") or {}
    request = pending.get("request")
    if (intent == "clarification_answer" and isinstance(request, str) and len(request) <= 2000
            and pending.get("epoch") == dialogue["epoch"]
            and pending.get("topic") == dialogue["topic"]
            and pending.get("document_id") == dialogue["active_document_id"]):
        return request
    return ""


def _polarity_scope(field, evidence):
    """Keep explicit polarity checks within the relevant predicate's clause.

    This recognizes a few common clause boundaries, not arbitrary Korean syntax.
    In particular, an unpaid-deposit clause must not negate a completed contract
    mentioned in the same quote. Short answers without a predicate stay intact.
    """
    if field == "landlord_notified":
        # A negative intention can be positively communicated: "갱신하지
        # 않겠다고 알렸어요". Check the notification, not its quoted content.
        reported = re.search(r"(?:다고|라고)([^.!?\n]{0,20}(?:알리|알렸|알린|통지|통보|연락)[^.!?\n]*)", evidence)
        if reported:
            return reported.group(1)
    predicates = {
        "contract_ended": r"끝|종료|만료|해지",
        "deposit_returned": r"받|반환",
        "living_in_property": r"살|거주",
        "moved_out": r"이사|퇴거|나가|나갔",
        "landlord_notified": r"알리|알렸|알린|통지|통보|연락",
    }
    # Split only past-tense 고 conjunctions; 살고/받고 있어요 stay intact.
    clauses = re.split(r"[,.;!?。\n]|는데|지만|으나|그리고|(?<=[았었했됐났렸])고(?=\s)", evidence)
    relevant = [clause for clause in clauses if re.search(predicates[field], clause)]
    return " ".join(relevant) if relevant else evidence


def _check_meaning(field, value, evidence):
    if not _numbers(value).issubset(_numbers(evidence)) or not _quantities(value).issubset(_quantities(evidence)):
        raise DecisionError("invented_number")
    if value == "모름":
        if not re.search(r"모르|모름|알\s*수\s*없|말하(?:고\s*싶지|기\s*어려)|답(?:변)?하(?:기\s*어려|고\s*싶지)|알려주기\s*싫|확답(?:하기|이)?\s*어렵|(?:찾아|확인해)\s*봐야\s*알", evidence):
            raise DecisionError("unknown_without_statement")
        return
    if field in {"deposit", "monthly_rent", "end_date", "start_date", "notice_date"}:
        unknown = value == "모름" and re.search(r"모르|모름|알\s*수\s*없|말하고\s*싶지|답하기\s*어려", evidence)
        if not unknown and re.sub(r"[\s,]", "", value) not in re.sub(r"[\s,]", "", evidence):
            raise DecisionError("changed_literal_value")
    if field in {"role", "property_type"}:
        aliases = ROLE_ALIASES if field == "role" else PROPERTY_ALIASES
        if value == "모름":
            matched = re.search(r"모르|모름|알\s*수\s*없", evidence)
        else:
            labels = aliases.get(value, ())
            matched = any(re.search(r"집(?!주인)", evidence) if alias == "집" else alias in evidence for alias in labels)
            if any(re.search(re.escape(alias) + r"(?:가|이|는|은)?\s*(?:아니|말고)", evidence) for alias in labels):
                matched = False
        if not matched:
            raise DecisionError("unsupported_or_unstated_fact")
    if field == "subject" and value not in evidence:
        raise DecisionError("unstated_subject")
    if field == "subject" and (
        value in {"계약갱신", "보증금반환", "시설수리", "계약준비", "대항력", "문서확인", "보증금", "계약서", "관련된", "그것", "그 내용", "그 요청"}
        or re.search(r"(?:처음|초보|알아듣|이해하).*(?:사람|분)|초보자", value)
    ):
        raise DecisionError("unsupported_subject")
    if field in BOOL_FIELDS:
        _enum(value, {"예", "아니요", "모름"}, "boolean_fact")
        scope = _polarity_scope(field, evidence)
        short_positive = re.fullmatch(r"\s*(?:네|예|응|맞아|맞아요|맞습니다|그렇습니다)[.!?\s]*", evidence)
        short_negative = re.fullmatch(r"\s*(?:아니|아니요|아니오|아뇨|아닙니다)[.!?\s]*", evidence)
        if (field == "landlord_notified" and not short_positive and not short_negative
                and not re.search(r"알리|알렸|알린|통지|통보|연락|전달|보냈|보내|말했|말하|요구|요청", scope)):
            raise DecisionError("unstated_notification")
        if value == "예" and short_negative or value == "아니요" and short_positive:
            raise DecisionError("polarity")
        # Detect clear contradictions without claiming a complete Korean parser.
        # Negation of another fact in the same sentence does not negate this one.
        if value == "예" and re.search(r"아니|않|못|모르|모름|미종료|미반환|안\s*(?:끝|받|살|나가|알렸)", scope):
            raise DecisionError("polarity")
        if field == "contract_ended" and value == "예" and re.search(r"끝나\s*가|(?:종료|만료|만기).{0,6}예정|아직.{0,12}남", scope):
            raise DecisionError("polarity")
        positive = {"contract_ended": r"끝났|종료됐|종료되었", "deposit_returned": r"돌려받았|반환받았", "living_in_property": r"살고\s*있|거주\s*중", "moved_out": r"이사했|퇴거했"}.get(field)
        if value == "아니요" and positive and re.search(positive, scope) and not re.search(r"아니|않|못|안\s|줄\s*알|모르", scope):
            raise DecisionError("polarity")
    if field == "contract_type":
        _enum(value, {"전세", "월세", "반전세", "모름"}, "contract_type")
        if value != "모름" and value not in evidence:
            raise DecisionError("unstated_contract_type")
        if value == "모름" and not re.search(r"모르|모름|알\s*수\s*없", evidence):
            raise DecisionError("unstated_contract_type")
        if re.search(re.escape(value) + r"(?:가|이|는|은)?\s*(?:아니|말고)", evidence):
            raise DecisionError("negated_fact")


def _check_query_polarity(query, facts):
    expressions = {
        "contract_ended": (r"계약.{0,8}(?:끝났|종료됐|종료되었)", r"계약.{0,8}(?:끝나지\s*않|종료되지\s*않|미종료)"),
        "deposit_returned": (r"보증금.{0,8}(?:돌려받았|반환받았)", r"보증금.{0,8}(?:못\s*받|받지\s*못|돌려받지\s*않)"),
        "living_in_property": (r"(?:현재|지금).{0,6}(?:살고\s*있|거주\s*중)", r"(?:현재|지금).{0,6}(?:살고\s*있지\s*않|거주하지\s*않)"),
    }
    for field, (positive, negative) in expressions.items():
        value = facts.get(field, {}).get("value")
        if value == "모름":
            for expression in (positive, negative):
                match = re.search(expression, query)
                if match and not re.match(r"(?:았|었)?(?:는지|는가|는지는|는지를)", query[match.end():]):
                    raise DecisionError("unknown_query_condition")
        if value == "예" and re.search(negative, query):
            raise DecisionError("query_polarity")
        match = re.search(positive, query)
        if value == "아니요" and match and not re.search(r"아니|않|모르|모름", query[match.end():match.end() + 8]):
            raise DecisionError("query_polarity")


def parse_decision(raw, *, state, user, updates_as_list=False, preserved_user=False):
    if not isinstance(raw, str) or len(raw) > 16000:
        raise DecisionError("json")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except DecisionError:
        raise
    except (ValueError, RecursionError):
        raise DecisionError("json") from None
    if not isinstance(payload, dict) or set(payload) != KEYS:
        raise DecisionError("keys")
    for field, choices in (("intent", INTENTS), ("action", ACTIONS), ("style", STYLES)):
        _enum(payload[field], choices, field)
    if type(payload["topic_changed"]) is not bool or payload["topic_changed"] != (payload["intent"] == "topic_change"):
        raise DecisionError("topic_transition")
    if payload["action"] == "social" and payload["intent"] not in {"greeting", "correction"}:
        raise DecisionError("social_legal_answer")
    if payload["clarify_field"] is not None:
        _enum(payload["clarify_field"], CLARIFY_FIELDS, "clarify_field")
    if payload["question"] is not None and (not isinstance(payload["question"], str) or not payload["question"].strip() or len(payload["question"]) > 300):
        raise DecisionError("question")
    if payload["action"] == "clarify" and not payload["clarify_field"]:
        raise DecisionError("clarify_field")
    query = payload["search_query"]
    if not isinstance(query, str) or len(query) > 2000 or (payload["action"] == "rag" and not query.strip()):
        raise DecisionError("query")
    updates = payload["updates"]
    if updates_as_list:
        if not isinstance(updates, list) or len(updates) > len(FACT_FIELDS):
            raise DecisionError("updates")
        normalized = {}
        for update in updates:
            if not isinstance(update, dict) or set(update) != {"field", "value", "evidence"}:
                raise DecisionError("updates")
            field = update["field"]
            if not isinstance(field, str) or field not in FACT_FIELDS or field in normalized:
                raise DecisionError("updates")
            normalized[field] = {"value": update["value"], "evidence": update["evidence"]}
        updates = payload["updates"] = normalized
    if not isinstance(updates, dict) or not set(updates).issubset(FACT_FIELDS):
        raise DecisionError("updates")
    pending = ensure_dialogue(deepcopy(state))["pending"]
    pending_field = pending.get("field") if pending else None
    if payload["intent"] == "clarification_answer" and pending_field in FACT_FIELDS and pending_field not in updates:
        raise DecisionError("pending_answer_missing")
    trial = deepcopy(state)
    try:
        dialogue = apply_user_update(trial, user=user, updates=updates, topic=payload["topic"], topic_changed=payload["topic_changed"], document_id=payload["document_id"])
    except ValueError:
        raise DecisionError("provenance_or_document") from None
    for field, update in updates.items():
        _check_meaning(field, update["value"], update["evidence"])
    factual_text = user + " " + " ".join(f["value"] + " " + f["evidence"] for f in dialogue["facts"].values())
    prior_query = previous_answer_query(dialogue, payload["intent"])
    request = pending_request(dialogue, payload["intent"])
    # This is a previously validated question, never a new user fact. Changes
    # to active facts or topic invalidate its answer cache in apply_user_update.
    if preserved_user and prior_query:
        factual_text += " " + prior_query
    if preserved_user and request:
        factual_text += " " + request
    if not _numbers(query).issubset(_numbers(factual_text)) or not _quantities(query).issubset(_quantities(factual_text)):
        raise DecisionError("invented_query_number")
    checked_query = query
    if preserved_user and payload["action"] == "rag":
        if not isinstance(user, str) or not user or not query.endswith(user):
            raise DecisionError("preserved_user_suffix")
        # A verbatim question may contain hypotheses or corrections. It is not
        # a model assertion; only generated context is checked for new polarity.
        checked_query = query[:-len(user)]
        if prior_query:
            checked_query = checked_query.replace(f"직전 답변 질문: {prior_query}\n", "", 1)
        if request:
            checked_query = checked_query.replace(f"이어서 상담할 사용자 질문: {request}\n", "", 1)
    _check_query_polarity(checked_query, dialogue["facts"])
    return Decision(**deepcopy(payload))
