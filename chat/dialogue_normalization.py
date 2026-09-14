"""Normalize unsupported proposals without inventing or reattributing facts.

This model-output adapter does not relax the strict decision parser or replace
source quotes. A discarded update leaves the stored fact's original source and
turn untouched. The caller must retain the original output and diagnostics when
evaluating model compliance; normalized output is not an originally valid reply.
"""
from copy import deepcopy
import json

from .dialogue_contract import FACT_FIELDS, DecisionError, _check_meaning, _unique_object, parse_decision
from .dialogue_state import apply_user_update, ensure_dialogue


def normalize_optional_statements(raw, *, state, user):
    """Drop unsupported descriptive fields, never repair a core condition.

    This is only a proposal adapter. The complete routing/document/query contract
    still runs afterwards. Identical repetitions and unstated defaults for unset
    fields may be removed; conflicting values and invalid amounts, dates or
    yes/no conditions remain errors.
    """
    payload = json.loads(raw, object_pairs_hook=_unique_object)
    updates = payload.get("updates")
    if not isinstance(updates, list) or len(updates) > len(FACT_FIELDS):
        return raw, ()
    seen = {}
    for update in updates:
        if not isinstance(update, dict) or set(update) != {"field", "value", "evidence"} or not isinstance(update["field"], str):
            return raw, ()
        field = update["field"]
        if field in seen and seen[field] != update:
            return raw, ()
        seen[field] = update
    dialogue = ensure_dialogue(deepcopy(state))
    kept, diagnostics = [], []
    for update in updates:
        if not isinstance(update, dict) or set(update) != {"field", "value", "evidence"}:
            return raw, ()
        if update in kept:
            diagnostics.append({"field": update["field"], "reason": "identical_statement_removed"})
            continue
        field = update["field"]
        if (field == "landlord_notified" and payload.get("action") == "rag"
                and payload.get("intent") not in {"correction", "topic_change"}
                and not payload.get("topic_changed")
                and payload.get("topic") in (None, dialogue["topic"])
                and field != (dialogue["pending"] or {}).get("field")):
            # A channel or a hypothetical lack of reply does not report that
            # notification happened. Discard the unsupported proposal only when
            # the entire current input lacks a notification statement as well.
            try:
                _check_meaning(field, update["value"], user)
            except DecisionError as error:
                if error.code == "unstated_notification":
                    diagnostics.append({"field": field, "reason": "unsupported_notification_removed"})
                    continue
        if isinstance(field, str) and field in {"subject", "role", "property_type"}:
            try:
                apply_user_update(deepcopy(state), user=user,
                                  updates={field: {k: update[k] for k in ("value", "evidence")}})
                _check_meaning(field, update["value"], update["evidence"])
            except (DecisionError, ValueError):
                # Dropping a bad person/building proposal must not conceal a
                # potentially missed case transition. Ask before using old facts.
                if dialogue["facts"] and payload.get("topic") not in (None, dialogue["topic"]) and not payload.get("topic_changed"):
                    return raw, ()
                diagnostics.append({"field": field, "reason": "unsupported_optional_statement_removed"})
                continue
        if (isinstance(field, str) and field in FACT_FIELDS and update["value"] == "모름"
                and field not in dialogue["facts"]
                and field != (dialogue["pending"] or {}).get("field")):
            try:
                apply_user_update(deepcopy(state), user=user,
                                  updates={field: {k: update[k] for k in ("value", "evidence")}})
                _check_meaning(field, update["value"], update["evidence"])
            except DecisionError as error:
                if error.code == "unknown_without_statement":
                    diagnostics.append({"field": field, "reason": "unstated_unknown_removed"})
                    continue
            except ValueError:
                pass
        if (field == "contract_type" and field not in dialogue["facts"]
                and field != (dialogue["pending"] or {}).get("field")
                and isinstance(update["value"], str) and update["value"] not in user):
            try:
                apply_user_update(deepcopy(state), user=user,
                                  updates={field: {k: update[k] for k in ("value", "evidence")}})
                _check_meaning(field, update["value"], update["evidence"])
            except DecisionError as error:
                if error.code == "unstated_contract_type":
                    diagnostics.append({"field": field, "reason": "unstated_contract_type_removed"})
                    continue
            except ValueError:
                pass
        kept.append(update)
    payload["updates"] = kept
    return json.dumps(payload, ensure_ascii=False), tuple(diagnostics)


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
    if not isinstance(old_quote, str) or not old_quote.strip() or len(old_quote) > 2000:
        return False
    # The model may choose another span from the same attributed user turn.
    # Dropping this redundant proposal must never replace the saved evidence.
    original_turn_quote = any(
        item.get("turn") == old["source_turn"]
        and old_quote in item.get("content", "")
        and evidence in item.get("content", "")
        for item in dialogue["history"]
    )
    if evidence not in old_quote and not original_turn_quote:
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
    # map in dialogue_state. History only verifies quotes from that fact's own
    # source turn; it is never used to recover inactive facts.
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
