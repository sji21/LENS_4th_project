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
        answer = {"content": _safe(str(answer.get("content", "")))[:2000], "turn": answer.get("turn")}
    else:
        answer = None
    history = dialogue["history"][-4:]
    if not history:
        # Old sessions retain user messages, including statements before abstention.
        # Assistant generations are not inferred as user facts during migration.
        history = [{"role": "user", "content": _safe(m["content"])[:2000]} for m in state.get("messages", []) if m.get("role") == "user"][-4:]
    return {
        "user": _safe(user), "topic": dialogue["topic"], "epoch": dialogue["epoch"],
        "facts": deepcopy(dialogue["facts"]), "history": deepcopy(history),
        "pending": deepcopy(dialogue["pending"]), "last_answer": answer,
        "last_status": dialogue["last_status"], "documents": documents,
        "active_document_id": document_id or dialogue["active_document_id"],
    }


def _check_meaning(field, value, evidence):
    if not _numbers(value).issubset(_numbers(evidence)) or not _quantities(value).issubset(_quantities(evidence)):
        raise DecisionError("invented_number")
    if field in {"deposit", "monthly_rent", "end_date", "start_date", "notice_date"}:
        if re.sub(r"[\s,]", "", value) not in re.sub(r"[\s,]", "", evidence):
            raise DecisionError("changed_literal_value")
    if field in BOOL_FIELDS:
        _enum(value, {"예", "아니요", "모름"}, "boolean_fact")
        # Detect clear contradictions without claiming a complete Korean parser.
        # Precise quotes allow a mixed correction sentence to cite its new clause.
        if value == "예" and re.search(r"아니|않|못|모르|모름|미종료|미반환|안\s*(?:끝|받|살|나가|알렸)", evidence):
            raise DecisionError("polarity")
        positive = {"contract_ended": r"끝났|종료됐|종료되었", "deposit_returned": r"돌려받았|반환받았", "living_in_property": r"살고\s*있|거주\s*중", "moved_out": r"이사했|퇴거했"}.get(field)
        if value == "아니요" and positive and re.search(positive, evidence) and not re.search(r"아니|않|못|안\s|줄\s*알|모르", evidence):
            raise DecisionError("polarity")
    if field == "contract_type":
        _enum(value, {"전세", "월세", "반전세", "모름"}, "contract_type")
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
        if value == "예" and re.search(negative, query):
            raise DecisionError("query_polarity")
        match = re.search(positive, query)
        if value == "아니요" and match and not re.search(r"아니|않|모르|모름", query[match.end():match.end() + 8]):
            raise DecisionError("query_polarity")


def parse_decision(raw, *, state, user):
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
    if not isinstance(updates, dict) or not set(updates).issubset(FACT_FIELDS):
        raise DecisionError("updates")
    trial = deepcopy(state)
    try:
        dialogue = apply_user_update(trial, user=user, updates=updates, topic=payload["topic"], topic_changed=payload["topic_changed"], document_id=payload["document_id"])
    except ValueError:
        raise DecisionError("provenance_or_document") from None
    for field, update in updates.items():
        _check_meaning(field, update["value"], update["evidence"])
    factual_text = user + " " + " ".join(f["value"] + " " + f["evidence"] for f in dialogue["facts"].values())
    if not _numbers(query).issubset(_numbers(factual_text)) or not _quantities(query).issubset(_quantities(factual_text)):
        raise DecisionError("invented_query_number")
    _check_query_polarity(query, dialogue["facts"])
    return Decision(**deepcopy(payload))
