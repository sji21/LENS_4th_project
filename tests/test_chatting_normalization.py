"""Existing-state repetition is a visible adapter operation, never new evidence."""
from copy import deepcopy
import json

import pytest

from chat.dialogue_contract import DecisionError, parse_decision
from chat.dialogue_normalization import normalize_existing_restatements
from chat.dialogue_state import apply_user_update


def session():
    state = {"messages": [], "documents": []}
    apply_user_update(
        state, user="월세 계약으로 살고 있어요.", topic="보증금반환",
        updates={"contract_type": {"value": "월세", "evidence": "월세 계약"}},
    )
    return state


def update(field="contract_type", value="월세", evidence="월세"):
    return {"field": field, "value": value, "evidence": evidence}


def wire(**changes):
    payload = {
        "intent": "followup", "action": "rag", "topic": "보증금반환",
        "topic_changed": False, "updates": [update()], "clarify_field": None,
        "question": None, "search_query": "월세 보증금 반환 절차",
        "document_id": None, "style": "standard",
    }
    payload.update(changes)
    return json.dumps(payload, ensure_ascii=False)


def normalize(raw, *, state=None, user="그 조건으로 절차를 알려주세요."):
    return normalize_existing_restatements(raw, state=session() if state is None else state, user=user)


def test_stale_repetition_is_removed_and_original_fact_source_is_retained():
    state = session()
    original = deepcopy(state)
    raw = wire()
    with pytest.raises(DecisionError, match="provenance_or_document"):
        parse_decision(raw, state=state, user="절차를 알려주세요.", updates_as_list=True)

    normalized, diagnostics = normalize(raw, state=state, user="절차를 알려주세요.")

    assert state == original
    assert json.loads(normalized)["updates"] == []
    assert diagnostics == ({"field": "contract_type", "source_turn": 1, "reason": "unchanged_existing_user_statement"},)
    decision = parse_decision(normalized, state=state, user="절차를 알려주세요.", updates_as_list=True)
    apply_user_update(state, user="절차를 알려주세요.", updates=decision.updates, topic=decision.topic)
    assert state["dialogue"]["facts"] == original["dialogue"]["facts"]
    assert state["dialogue"]["turn"] == 2
    assert state["dialogue"]["changes"] == original["dialogue"]["changes"]
    assert set(diagnostics[0]) == {"field", "source_turn", "reason"}


def test_valid_current_user_quote_keeps_original_wire_exactly():
    raw = wire()
    assert normalize(raw, user="월세 계약에 대해 알려주세요.") == (raw, ())


@pytest.mark.parametrize("topic", [None, "보증금반환"])
def test_explicit_current_topic_or_implicit_keep_can_normalize(topic):
    assert normalize(wire(topic=topic))[1]


@pytest.mark.parametrize("changes", [
    {"intent": "correction"},
    {"intent": "topic_change", "topic_changed": True, "topic": "수리"},
    {"topic": "수리"},
    {"intent": "greeting", "action": "social", "search_query": ""},
    {"action": "clarify", "clarify_field": "contract_ended"},
    {"action": "refuse"},
])
def test_transitions_and_non_rag_actions_are_not_repaired(changes):
    raw = wire(**changes)
    assert normalize(raw) == (raw, ())
    with pytest.raises(DecisionError):
        parse_decision(raw, state=session(), user="절차를 알려주세요.", updates_as_list=True)


@pytest.mark.parametrize("changed", [
    update(value="전세"),
    update(evidence="원래 월세로 계약했었어요"),
    update(evidence=""),
    update(evidence=[]),
    update(value=[]),
    update(field="role", value="임차인", evidence="월세"),
])
def test_new_values_unmatched_quotes_and_invalid_updates_are_not_repaired(changed):
    raw = wire(updates=[changed])
    assert normalize(raw) == (raw, ())


@pytest.mark.parametrize("raw", [
    "not json", "[]", "null",
    '{"intent":"followup","intent":"question"}',
    wire(updates=[update(), update()]),
    wire(updates={"contract_type": {"value": "월세", "evidence": "월세"}}),
    wire(updates=[dict(update(), extra="value")]),
    wire(updates=[update(field="execute")]),
    wire(action="execute"),
    wire(style="unbounded"),
    wire(intent="unknown"),
    wire(topic_changed=True),
    wire(question=[]),
    wire(search_query=""),
    wire(extra="hidden"),
    wire().replace('"field": "contract_type"', '"field": "contract_type", "field": "role"'),
])
def test_invalid_original_wire_is_rejected_before_dropping_anything(raw):
    state = session()
    before = deepcopy(state)
    with pytest.raises(DecisionError):
        normalize(raw, state=state)
    assert state == before


def test_mixed_valid_new_statement_is_kept_without_refreshing_old_fact():
    state = session()
    old = deepcopy(state["dialogue"]["facts"]["contract_type"])
    user = "계약은 끝났어요. 절차를 알려주세요."
    raw = wire(updates=[update(), update("contract_ended", "예", "계약은 끝났어요")])
    normalized, diagnostics = normalize(raw, state=state, user=user)
    decision = parse_decision(normalized, state=state, user=user, updates_as_list=True)
    assert decision.updates == {"contract_ended": {"value": "예", "evidence": "계약은 끝났어요"}}
    assert len(diagnostics) == 1
    apply_user_update(state, user=user, updates=decision.updates, topic=decision.topic)
    assert state["dialogue"]["facts"]["contract_type"] == old
    assert state["dialogue"]["facts"]["contract_ended"]["source_turn"] == 2


@pytest.mark.parametrize("changes,user,code", [
    ({"updates": [update(), update("contract_ended", "예", "계약은 안 끝났어요")]}, "계약은 안 끝났어요", "polarity"),
    ({"updates": [update(), update("role", "임차인", "수리 문의")]}, "수리 문의", "unsupported_or_unstated_fact"),
    ({"updates": [update(), update("contract_ended", "예", "인용을 위조했습니다")]}, "절차를 알려주세요", "provenance_or_document"),
    ({"document_id": "other-users-document"}, "절차를 알려주세요", "provenance_or_document"),
    ({"search_query": "보증금 300억원 반환"}, "절차를 알려주세요", "invented_query_number"),
])
def test_other_invalid_data_rejects_whole_candidate_atomically(changes, user, code):
    state = session()
    before = deepcopy(state)
    with pytest.raises(DecisionError, match=code):
        normalize(wire(**changes), state=state, user=user)
    assert state == before


@pytest.mark.parametrize("field,value", [
    ("source", "document"), ("source_turn", 0), ("source_turn", -1),
    ("source_turn", 2), ("source_turn", True), ("certainty", "verified"),
    ("evidence", "전세 계약"), ("evidence", ""),
])
def test_invalid_stored_fact_cannot_authorize_normalization(field, value):
    state = session()
    state["dialogue"]["facts"]["contract_type"][field] = value
    before = deepcopy(state)
    raw = wire()
    assert normalize(raw, state=state) == (raw, ())
    assert state == before


def test_old_fact_with_rejected_meaning_cannot_authorize_normalization():
    state = session()
    state["dialogue"]["facts"]["contract_type"].update(value="전월세", evidence="전월세 계약")
    raw = wire(updates=[update(value="전월세", evidence="전월세")])
    assert normalize(raw, state=state) == (raw, ())


def test_previous_epoch_history_and_change_records_are_not_used():
    state = session()
    old = deepcopy(state["dialogue"]["facts"]["contract_type"])
    apply_user_update(state, user="이제 수리 문제를 문의할게요.", topic="수리", topic_changed=True)
    state["dialogue"]["changes"] = [{"field": "contract_type", "previous": old}]
    state["messages"] = [{"role": "user", "content": "월세 계약"}]
    raw = wire(topic="수리")
    assert normalize(raw, state=state) == (raw, ())


def test_current_epoch_fact_remains_eligible_after_topic_reset():
    state = session()
    apply_user_update(state, user="새 월세 계약입니다.", topic="다른계약", topic_changed=True,
                      updates={"contract_type": {"value": "월세", "evidence": "새 월세 계약"}})
    normalized, diagnostics = normalize(wire(topic="다른계약"), state=state)
    assert json.loads(normalized)["updates"] == []
    assert diagnostics[0]["source_turn"] == 2
    assert state["dialogue"]["epoch"] == 1


def test_stale_and_current_quotes_for_same_field_are_rejected_as_duplicates():
    raw = wire(updates=[update(), update(evidence="월세 계약")])
    with pytest.raises(DecisionError, match="updates"):
        normalize(raw, user="현재도 월세 계약입니다.")


def test_unknown_value_keeps_original_uncertainty_and_quote():
    state = session()
    apply_user_update(state, user="계약 종료일은 모름", updates={"contract_ended": {"value": "모름", "evidence": "모름"}})
    before = deepcopy(state)
    raw = wire(updates=[update("contract_ended", "모름", "모름")])
    assert normalize(raw, state=state)[1][0]["source_turn"] == 2
    assert state == before


def test_invalid_user_is_left_for_strict_parser_and_not_repaired():
    raw = wire()
    assert normalize(raw, user=None) == (raw, ())
