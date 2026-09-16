"""Resolve only owned document references before selecting document evidence."""
from copy import deepcopy
from dataclasses import replace
import re

from src.document_check.session_retrieval import question_references_uploaded_document, referenced_document_kind
from .dialogue_contract import Decision
from .dialogue_state import ensure_dialogue


def select_document(state, user, explicit_id=None, *, continue_document=False):
    documents = state.get("documents", [])
    ids = {doc["document_id"] for doc in documents}
    if explicit_id is not None:
        if explicit_id not in ids:
            raise ValueError("Document does not belong to this conversation")
        return explicit_id, False
    if not documents:
        return None, False
    kinds = tuple(dict.fromkeys(doc["kind"] for doc in documents))
    short_registry = "registry" in kinds and bool(re.search(r"(?:올린|첨부한|아까|이|그)\s*등기(?:를|의|에|서|\s|$)", user))
    explicit_reference = bool(re.search(r"올린|첨부|업로드|아까|방금|선택한|(?:이|그)\s*(?:문서|계약서|등기)", user))
    reference = short_registry or (question_references_uploaded_document(user, kinds) and (explicit_reference or continue_document))
    # Numbered document choices refer to upload order, never an article number.
    match = re.search(r"(?:(첫|두|세|네)\s*번째|([1-5])\s*번)\s*(?:문서|계약서|등기)", user)
    if match:
        index = int(match[2]) if match[2] else {"첫": 1, "두": 2, "세": 3, "네": 4}[match[1]]
        return (documents[index - 1]["document_id"], False) if index <= len(documents) else (None, True)
    named = [doc for doc in documents if doc.get("filename") and doc["filename"] in user]
    if len(named) == 1:
        return named[0]["document_id"], False
    if len(named) > 1:
        return None, True
    if not reference and not continue_document:
        return None, False
    kind = "registry" if short_registry else referenced_document_kind(user, kinds)
    candidates = [doc for doc in documents if not kind or doc["kind"] == kind]
    active = ensure_dialogue(deepcopy(state))["active_document_id"]
    if active in {doc["document_id"] for doc in candidates}:
        return active, False
    if len(candidates) == 1:
        return candidates[0]["document_id"], False
    return None, True


def document_question(state):
    topic = ensure_dialogue(deepcopy(state))["topic"] or "문서확인"
    return Decision("document_question", "clarify", topic, False, {}, "document", None, "", None, "standard")


def retain_followup_topic(state, decision):
    previous = ensure_dialogue(deepcopy(state))["topic"]
    if previous and not decision.topic_changed and decision.intent in {"followup", "explain", "correction", "clarification_answer"}:
        return replace(decision, topic=previous)
    return decision
