"""Discard a narrowly defined repetition of an existing user statement.

This model-output adapter does not relax the strict decision parser or replace
source quotes. A discarded update leaves the stored fact's original source and
turn untouched. The caller must retain the original output and diagnostics when
evaluating model compliance; normalized output is not an originally valid reply.
"""
from copy import deepcopy
import json

from .dialogue_contract import DecisionError, _check_meaning, parse_decision
from .dialogue_state import ensure_dialogue


def _existing_restatement(update, dialogue, user):
    field, value, evidence = update["field"], update["value"], update["evidence"]
    old = dialogue["facts"].get(field)
    if not isinstance(old, dict) or old.get("source") != "user_statement":
        return False
    if type(old.get("source_turn")) is not int or not 1 <= old["source_turn"] <= dialogue["turn"]:
        return False
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or value != old.get("value"):
        return False
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 2000 or evidence in user:
        return False
    old_quote = old.get("evidence")
    if not isinstance(old_quote, str) or not old_quote.strip() or len(old_quote) > 2000 or evidence not in old_quote:
        return False
    if old.get("certainty") != ("unknown" if value == "모름" else "reported"):
        return False
    try:
        _check_meaning(field, value, old_quote)
        _check_meaning(field, value, evidence)
    except DecisionError:
        return False
    return True


def normalize_existing_restatements(raw, *, state, user, preserved_user=False):
    """Return ``(wire_json, diagnostics)`` without mutating the session.

    Only the planner's list-shaped wire format is accepted. The original parser
    first checks JSON, duplicate keys, shape, enum and duplicate field rules.
    A provenance failure may be repaired only by dropping unchanged active facts
    on a same-topic RAG turn. Every remaining decision check must still pass.
    Unrelated invalid decisions raise ``DecisionError``; an ineligible provenance
    failure is returned unchanged for the caller's strict parser to reject.
    """
    try:
        parse_decision(raw, state=state, user=user, updates_as_list=True, preserved_user=preserved_user)
    except DecisionError as exc:
        if exc.code != "provenance_or_document":
            raise
    else:
        return raw, ()

    # The strict parser has already decoded this exact wire representation and
    # rejected duplicate keys and fields before reaching provenance validation.
    payload = json.loads(raw)
    if (
        payload["action"] != "rag"
        or payload["intent"] in {"correction", "topic_change"}
        or payload["topic_changed"]
        or not isinstance(user, str) or not user.strip() or len(user) > 2000
    ):
        return raw, ()
    dialogue = ensure_dialogue(deepcopy(state))
    if payload["topic"] is not None and payload["topic"] != dialogue["topic"]:
        return raw, ()

    # Active facts belong to the current epoch: topic transitions clear this
    # map in dialogue_state. History, changes and old answers are never searched.
    kept, diagnostics = [], []
    for update in payload["updates"]:
        if _existing_restatement(update, dialogue, user):
            diagnostics.append({
                "field": update["field"],
                "source_turn": dialogue["facts"][update["field"]]["source_turn"],
                "reason": "unchanged_existing_user_statement",
            })
        else:
            kept.append(update)
    if not diagnostics:
        return raw, ()

    payload["updates"] = kept
    normalized = json.dumps(payload, ensure_ascii=False)
    # No repaired result or diagnostics escape if any remaining update, document
    # selection, topic, query or semantic check fails. No evidence is fabricated.
    parse_decision(normalized, state=state, user=user, updates_as_list=True, preserved_user=preserved_user)
    return normalized, tuple(diagnostics)
