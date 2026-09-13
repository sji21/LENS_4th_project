"""Session-scoped user statements; these are not established legal facts.

Only this conversation's JSON state is used. No global memory, model call or
second persistence store is introduced alongside Django's existing lease.
"""
from copy import deepcopy
import re

from src.document_check.privacy import mask_sensitive_text
from src.security.secret_filter import redact_secrets

VERSION = 1
HISTORY_LIMIT = 8
FACT_LIMIT = 32
CHANGE_LIMIT = 16


def _safe(text):
    return mask_sensitive_text(redact_secrets(text).text)


def empty_dialogue():
    return {
        "version": VERSION, "turn": 0, "epoch": 0, "topic": None,
        "facts": {}, "history": [], "changes": [], "pending": None,
        "active_document_id": None, "last_answer": None, "last_status": None,
        "clarification_counts": {},
    }


def _read(state):
    stored = state.get("dialogue")
    fresh = empty_dialogue()
    if not isinstance(stored, dict) or type(stored.get("version")) is not int or stored.get("version") != VERSION:
        return fresh
    if any(type(stored.get(k, fresh[k])) is not type(fresh[k]) for k in ("turn", "epoch", "facts", "history", "changes")):
        return fresh
    fresh.update({k: deepcopy(stored[k]) for k in fresh if k in stored})
    if fresh["turn"] < 0 or fresh["epoch"] < 0:
        return empty_dialogue()
    if any(fresh[k] is not None and not isinstance(fresh[k], str) for k in ("topic", "active_document_id", "last_status")):
        return empty_dialogue()
    if any(fresh[k] is not None and not isinstance(fresh[k], dict) for k in ("pending", "last_answer")):
        return empty_dialogue()
    if len(fresh["facts"]) > FACT_LIMIT or any(
        not isinstance(fact, dict) or not isinstance(fact.get("value"), str)
        or not isinstance(fact.get("evidence"), str) or fact.get("source") != "user_statement"
        or type(fact.get("source_turn")) is not int
        for fact in fresh["facts"].values()
    ):
        return empty_dialogue()
    if any(not isinstance(item, dict) or item.get("role") != "user" or not isinstance(item.get("content"), str) for item in fresh["history"]):
        return empty_dialogue()
    ids = {doc.get("document_id") for doc in state.get("documents", [])}
    if fresh["active_document_id"] and fresh["active_document_id"] not in ids:
        return empty_dialogue()
    counts = fresh["clarification_counts"]
    fresh["clarification_counts"] = {
        key: min(value, 2) for key, value in counts.items()
        if isinstance(key, str) and re.fullmatch(r"[a-z_]{1,40}", key)
        and type(value) is int and value >= 0
    } if isinstance(counts, dict) and len(counts) <= 32 else {}
    fresh["history"] = fresh["history"][-HISTORY_LIMIT:]
    fresh["changes"] = fresh["changes"][-CHANGE_LIMIT:]
    return fresh


def ensure_dialogue(state):
    """Initialize old sessions without inferring facts from assistant messages."""
    state["dialogue"] = _read(state)
    return state["dialogue"]


def apply_user_update(state, *, user, updates=None, topic=None, topic_changed=False, document_id=None):
    """Validate all provenance first, then replace the session snapshot once.

    The planner interprets language; this layer validates representation and
    literal source attribution. A matching quote alone is not semantic proof.
    """
    if not isinstance(user, str) or not user.strip() or len(user) > 2000:
        raise ValueError("Invalid user statement")
    if type(topic_changed) is not bool:
        raise ValueError("Invalid topic transition")
    if topic is not None and (not isinstance(topic, str) or not topic.strip() or len(topic) > 100):
        raise ValueError("Invalid topic")
    if document_id is not None and document_id != "":
        if not isinstance(document_id, str) or document_id not in {d.get("document_id") for d in state.get("documents", [])}:
            raise ValueError("Document does not belong to this conversation")
    updates = {} if updates is None else updates
    if not isinstance(updates, dict) or len(updates) > FACT_LIMIT:
        raise ValueError("Invalid statement updates")
    draft = _read(state)
    turn, epoch = draft["turn"] + 1, draft["epoch"]
    if topic_changed:
        draft = empty_dialogue()
        draft["epoch"] = epoch + 1
    draft["turn"] = turn
    if topic is not None:
        if topic != draft["topic"]:
            draft["last_answer"] = None
        draft["topic"] = _safe(topic)
    if document_id is not None:
        if draft["active_document_id"] != (document_id or None):
            draft["last_answer"] = None
        draft["active_document_id"] = document_id or None
    for field, update in updates.items():
        if not isinstance(field, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", field):
            raise ValueError("Invalid statement field")
        if not isinstance(update, dict) or set(update) != {"value", "evidence"}:
            raise ValueError("Statement must include value and source quote")
        value, evidence = update["value"], update["evidence"]
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("Invalid statement value")
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 2000 or evidence not in user:
            raise ValueError("Statement quote is not in the current user input")
        fact = {
            "value": _safe(value), "evidence": _safe(evidence), "source_turn": turn,
            "source": "user_statement", "certainty": "unknown" if value == "모름" else "reported",
        }
        old = draft["facts"].get(field)
        if not old or old.get("value") != fact["value"]:
            draft["last_answer"] = None
        if old and old.get("value") != fact["value"]:
            draft["changes"].append({"field": field, "previous": deepcopy(old), "replacement": deepcopy(fact)})
        draft["facts"][field] = fact
    if len(draft["facts"]) > FACT_LIMIT:
        raise ValueError("Too many active statements")
    draft["changes"] = draft["changes"][-CHANGE_LIMIT:]
    draft["history"] = (draft["history"] + [{"role": "user", "turn": turn, "content": _safe(user)}])[-HISTORY_LIMIT:]
    state["dialogue"] = draft
    return draft


def record_answer(state, message, *, query=None):
    """Cache only a public answer that passed the existing answer pipeline."""
    draft = _read(state)
    draft["last_status"] = message.get("status")
    draft["last_answer"] = None
    if message.get("status") == "answered" and message.get("content"):
        draft["last_answer"] = {
            "content": _safe(message["content"]), "sources": deepcopy(message.get("sources", [])),
            "turn": draft["turn"], "validation": "existing_pipeline_passed",
        }
        if isinstance(query, str) and query.strip() and len(query) <= 2000:
            draft["last_answer"]["query"] = _safe(query)
    state["dialogue"] = draft
    return draft


def invalidate_document(state, document_id):
    # The web adapter already discards all chat history on document deletion.
    # Clear derived state on the same boundary, including indirect references.
    state["dialogue"] = empty_dialogue()
