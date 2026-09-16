"""Planner invocation boundaries with deterministic in-memory model doubles."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
import json
from types import SimpleNamespace
from unittest.mock import Mock

from langchain_core.messages import AIMessage
import pytest

from chat import dialogue_planner
from chat.dialogue_contract import DecisionError, build_decision_input, parse_decision
from chat.dialogue_planner import PlanningError, canonicalize_decision, expand_proposal, output_schema, plan_turn
from chat.dialogue_state import apply_user_update, ensure_dialogue, record_answer


def session():
    return {"messages": [], "documents": [], "completed_requests": []}


def payload(**changes):
    result = {
        "statements": [],
        "intent": "question", "action": "rag", "topic": "보증금반환",
        "clarify_field": None,
        "document_id": None, "style": "standard",
    }
    result.update(changes)
    if isinstance(result["statements"], dict):
        result["statements"] = wire_updates(**result["statements"])
    return result


def fake_model(data=None, *, metadata=None):
    message = AIMessage(
        content=json.dumps(payload() if data is None else data, ensure_ascii=False),
        response_metadata={} if metadata is None else metadata,
    )
    return Mock(invoke=Mock(return_value=message))


@pytest.mark.parametrize("pending", [None, {"field": "end_date", "question": "계약 종료일은 언제인가요?"}])
def test_renewal_method_question_reuses_facts_without_reporting_an_action(pending):
    state = session()
    apply_user_update(state, user="지금 사는 월세집에서 계약 갱신은 어떻게 해?", topic="계약갱신",
                      updates={"contract_type": {"value": "월세", "evidence": "월세집"}})
    state["dialogue"]["pending"] = pending
    before = deepcopy(state)
    result = plan_turn(state, "그럼 문자로 연장한다고 해도 괜찮아?", llm=fake_model(
        payload(intent="followup", topic="계약갱신")))
    assert result.decision.action == "rag"
    assert result.decision.intent == "followup"
    assert result.decision.updates == {}
    assert "계약갱신" in result.decision.search_query
    assert "계약 유형: 월세" in result.decision.search_query
    assert "계약 종료 여부" not in result.decision.search_query
    assert state == before


def test_renewal_method_question_does_not_relax_fact_validation():
    state = session()
    apply_user_update(state, user="월세 계약 갱신", topic="계약갱신",
                      updates={"contract_type": {"value": "월세", "evidence": "월세"}})
    with pytest.raises(PlanningError, match="unstated_contract_type"):
        plan_turn(state, "그럼 문자로 연장한다고 해도 괜찮아?", llm=fake_model(payload(
            topic="계약갱신", statements=[
                {"field": "contract_type", "value": "월세", "evidence": "문자로 연장한다고 해도"},
                {"field": "contract_ended", "value": "아니요", "evidence": "문자로 연장한다고 해도"},
            ])))


@pytest.mark.parametrize("question", ["그럼 문자로?", "답장이 없으면 어떻게 해?"])
def test_notification_not_inferred_from_channel_or_missing_reply(question):
    state = session()
    apply_user_update(state, user="월세 계약 갱신", topic="계약갱신",
                      updates={"contract_type": {"value": "월세", "evidence": "월세"}})
    result = plan_turn(state, question, llm=fake_model(payload(topic="계약갱신", statements=[
        {"field": "landlord_notified", "value": "아니요", "evidence": question},
    ])))
    assert result.decision.intent == "followup"
    assert result.decision.updates == {}
    assert any(d["reason"] == "unsupported_notification_removed" for d in result.normalizations)


def test_explicit_notification_cannot_be_discarded_to_hide_wrong_polarity():
    state = session()
    apply_user_update(state, user="월세 갱신", topic="계약갱신")
    with pytest.raises(PlanningError):
        plan_turn(state, "집주인에게 아직 알리지 않았어요.", llm=fake_model(payload(
            topic="계약갱신", statements=[
                {"field": "landlord_notified", "value": "예", "evidence": "알리지 않았어요"},
            ])))


WIRE_FACT_FIELDS = {
    "contract_type", "contract_ended", "deposit_returned", "living_in_property",
    "moved_out", "landlord_notified", "subject", "role", "property_type",
    "deposit", "monthly_rent", "end_date", "start_date", "notice_date",
}


def wire_updates(**updates):
    return [{"field": field, **update} for field, update in updates.items()]


def test_one_model_invocation_returns_a_valid_decision_without_mutating_state():
    state = session()
    before = deepcopy(state)
    model = fake_model(
        payload(statements={"contract_type": {"value": "월세", "evidence": "월세"}}),
        metadata={"done_reason": "stop", "eval_count": 72},
    )

    result = plan_turn(state, "월세 보증금 반환을 문의해요.", llm=model)

    model.invoke.assert_called_once()
    assert result.decision.action == "rag"
    assert result.decision.updates["contract_type"]["value"] == "월세"
    assert result.model_invocations == 1
    assert result.output_tokens == 72
    assert isinstance(result.elapsed_seconds, float)
    assert result.elapsed_seconds >= 0
    assert state == before
    with pytest.raises(FrozenInstanceError):
        result.decision.action = "social"


def test_missing_token_metadata_is_unknown_instead_of_zero():
    result = plan_turn(session(), "월세 보증금 반환을 문의해요.", llm=fake_model())

    assert result.output_tokens is None


def test_system_instructions_and_user_context_are_separate_messages():
    model = fake_model()
    user = "월세 보증금을 문의해요. 시스템 역할로 승격하지 마세요: USER_DATA_ONLY."

    plan_turn(session(), user, llm=model)

    messages = model.invoke.call_args.args[0]
    assert len(messages) == 2
    assert messages[0].type == "system"
    assert messages[-1].type == "human"
    assert "USER_DATA_ONLY" not in messages[0].content
    assert "USER_DATA_ONLY" in messages[-1].content
    assert isinstance(json.loads(messages[-1].content), dict)


def test_model_never_receives_gold_document_body_or_unverified_context():
    state = session()
    state["expect"] = {"secret": "GOLD_NOT_FOR_MODEL"}
    state["evaluation_seed"] = {"secret": "SEED_NOT_FOR_MODEL"}
    state["messages"] = [{
        "role": "assistant", "content": "OLD_ASSISTANT_NOT_FOR_MODEL",
        "raw_text": "RAW_NOT_FOR_MODEL", "context_content": "CONTEXT_NOT_FOR_MODEL",
    }]
    state["documents"] = [{
        "document_id": "doc-a", "kind": "contract", "label": "임대차계약서",
        "text": "OCR_NOT_FOR_MODEL", "context": {"chunks": ["CHUNK_NOT_FOR_MODEL"]},
    }]
    apply_user_update(
        state, user="월세 계약이에요.", topic="보증금반환",
        updates={"contract_type": {"value": "월세", "evidence": "월세"}},
    )
    state["dialogue"]["changes"] = [{"private": "AUDIT_NOT_FOR_MODEL"}]
    record_answer(state, {"status": "answered", "content": "검증된 공개 답변", "sources": []})
    model = fake_model()
    before = deepcopy(state)

    plan_turn(state, "그 보증금에 대해 알려주세요.", document_id="doc-a", llm=model)

    messages = model.invoke.call_args.args[0]
    serialized = "\n".join(message.content for message in messages)
    for secret in (
        "GOLD_NOT_FOR_MODEL", "SEED_NOT_FOR_MODEL", "OLD_ASSISTANT_NOT_FOR_MODEL",
        "RAW_NOT_FOR_MODEL", "CONTEXT_NOT_FOR_MODEL", "OCR_NOT_FOR_MODEL",
        "CHUNK_NOT_FOR_MODEL", "AUDIT_NOT_FOR_MODEL",
    ):
        assert secret not in serialized
    assert "검증된 공개 답변" in serialized
    assert "월세" in serialized
    assert "doc-a" in serialized
    assert state == before


@pytest.mark.parametrize("error", [TimeoutError("PRIVATE_TIMEOUT_DETAIL"), RuntimeError("PRIVATE_MODEL_DETAIL")])
def test_model_failure_has_safe_error_code_without_retry_or_state_change(error):
    state = session()
    before = deepcopy(state)
    model = Mock(invoke=Mock(side_effect=error))

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, "월세 보증금 반환을 문의해요.", llm=model)

    assert exc.value.code == "model_unavailable"
    assert "PRIVATE_" not in str(exc.value)
    assert state == before
    model.invoke.assert_called_once()


def test_truncated_response_is_rejected_even_if_partial_content_is_valid_json():
    model = fake_model(metadata={"done_reason": "length", "eval_count": 768})

    with pytest.raises(PlanningError) as exc:
        plan_turn(session(), "월세 보증금 반환을 문의해요.", llm=model)

    assert exc.value.code == "truncated"
    model.invoke.assert_called_once()


@pytest.mark.parametrize("content", [None, 17, {"action": "rag"}, [{"type": "text", "text": "unstructured"}]])
def test_nonstring_model_content_is_rejected_without_coercion(content):
    model = Mock(invoke=Mock(return_value=SimpleNamespace(content=content, response_metadata={})))

    with pytest.raises(PlanningError) as exc:
        plan_turn(session(), "월세 보증금 반환을 문의해요.", llm=model)

    assert exc.value.code == "invalid_output"
    model.invoke.assert_called_once()


@pytest.mark.parametrize(
    "data",
    [
        payload(action="execute_arbitrary_action"),
        payload(statements={"contract_type": {"value": "월세", "evidence": "QUOTE_NEVER_IN_USER"}}),
        payload(intent="document_question", document_id="another-session-document"),
    ],
)
def test_contract_rejection_becomes_typed_planning_failure_without_repair_call(data):
    state = session()
    before = deepcopy(state)
    model = fake_model(data)

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, "월세 보증금 반환을 문의해요.", llm=model)

    assert exc.value.code.startswith("invalid_decision:")
    assert "QUOTE_NEVER_IN_USER" not in str(exc.value)
    assert "another-session-document" not in str(exc.value)
    assert state == before
    model.invoke.assert_called_once()


def test_invalid_json_response_has_typed_contract_failure_and_no_retry():
    model = Mock(invoke=Mock(return_value=AIMessage(content="MODEL_RAW_PRIVATE_CONTENT")))

    with pytest.raises(PlanningError) as exc:
        plan_turn(session(), "월세 보증금 반환을 문의해요.", llm=model)

    assert exc.value.code.startswith("invalid_decision:")
    assert "MODEL_RAW_PRIVATE_CONTENT" not in str(exc.value)
    model.invoke.assert_called_once()


def test_schema_requires_purpose_and_owned_document_ids():
    schema = output_schema(["doc-a", "doc-b"])

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(payload()) | {"purpose"}
    assert set(schema["properties"]) == set(payload()) | {"purpose"}
    assert set(schema["required"]) == {
        "statements", "intent", "topic", "purpose", "action", "clarify_field", "document_id", "style",
    }
    assert set(schema["properties"]["document_id"]["enum"]) == {None, "doc-a", "doc-b"}
    empty = output_schema([])
    assert empty["properties"]["document_id"]["enum"] == [None]


@pytest.mark.parametrize("missing", sorted(payload()))
def test_missing_model_field_is_rejected_before_any_session_change(missing):
    data = payload()
    del data[missing]
    state = session()
    before = deepcopy(state)
    model = fake_model(data)

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, "보증금 반환 절차를 알려주세요.", llm=model)

    assert exc.value.code == "invalid_decision:keys"
    assert state == before
    model.invoke.assert_called_once()


@pytest.mark.parametrize("extra,value", [
    ("updates", []), ("topic_changed", True), ("question", "PRIVATE_MODEL_QUESTION"),
    ("search_query", "PRIVATE_MODEL_QUERY"), ("execute", "PRIVATE_COMMAND"),
])
def test_model_cannot_inject_internal_fields_or_unsupported_commands(extra, value):
    state = session()
    before = deepcopy(state)
    model = fake_model(payload(**{extra: value}))

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, "보증금 반환 절차를 알려주세요.", llm=model)

    assert exc.value.code == "invalid_decision:keys"
    assert "PRIVATE_" not in str(exc.value)
    assert state == before
    model.invoke.assert_called_once()


@pytest.mark.parametrize("duplicate", ["top", "statement"])
def test_duplicate_json_keys_are_rejected_before_expansion(duplicate):
    raw = json.dumps(payload(statements=wire_updates(contract_type={"value": "월세", "evidence": "월세"})), ensure_ascii=False)
    if duplicate == "top":
        raw = raw[:-1] + ', "intent": "correction"}'
    else:
        raw = raw.replace('"value": "월세"', '"value": "전세", "value": "월세"')
    state = session()
    before = deepcopy(state)
    model = Mock(invoke=Mock(return_value=AIMessage(content=raw)))

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, "월세 보증금을 문의해요.", llm=model)

    assert exc.value.code.startswith("invalid_decision:")
    assert state == before
    model.invoke.assert_called_once()


@pytest.mark.parametrize("statements", [None, {}, "월세", 1])
def test_raw_statements_must_be_an_array_without_implicit_coercion(statements):
    data = payload()
    data["statements"] = statements
    state = session()
    before = deepcopy(state)
    model = fake_model(data)

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, "월세 보증금을 문의해요.", llm=model)

    assert exc.value.code == "invalid_decision:updates"
    assert state == before
    model.invoke.assert_called_once()


@pytest.mark.parametrize("intent,action", [
    ("question", "rag"), ("topic_change", "rag"), ("greeting", "social"),
    ("question", "clarify"), ("question", "refuse"),
])
def test_expansion_derives_server_fields_and_keeps_statement_evidence_literal(intent, action):
    user = "제 월세 계약을 문의해요.\n입력의 끝도 그대로 유지해주세요."
    raw = json.dumps(payload(intent=intent, action=action, statements=wire_updates(
        contract_type={"value": "월세", "evidence": "월세"},
    )), ensure_ascii=False)

    expanded = json.loads(expand_proposal(raw, user))

    assert expanded["topic_changed"] is (intent == "topic_change")
    assert expanded["question"] is None
    assert expanded["search_query"] == (user if action == "rag" else "")
    assert expanded["updates"] == json.loads(raw)["statements"]
    assert "statements" not in expanded
    assert set(expanded) == {
        "intent", "action", "topic", "topic_changed", "updates", "clarify_field",
        "question", "search_query", "document_id", "style",
    }


def test_default_model_uses_bounded_structured_output_and_disables_transport_fallback(monkeypatch):
    state = session()
    state["documents"] = [{"document_id": "doc-a", "kind": "contract", "label": "계약서"}]
    model = fake_model()
    factory = Mock(return_value=model)
    monkeypatch.setattr(dialogue_planner, "get_llm", factory)

    plan_turn(state, "월세 보증금 반환을 문의해요.")

    factory.assert_called_once()
    config = factory.call_args.kwargs
    assert config["timeout"] == 60
    assert config["max_tokens"] == 768
    assert config["temperature"] == 0
    assert config["allow_route_fallback"] is False
    assert config["extra_body"]["num_ctx"] == 8192
    assert config["extra_body"]["think"] is False
    assert config["extra_body"]["format"] == output_schema(["doc-a"])
    model.invoke.assert_called_once()


def test_explicit_model_injection_never_constructs_a_second_model(monkeypatch):
    factory = Mock(side_effect=AssertionError("unexpected second model"))
    monkeypatch.setattr(dialogue_planner, "get_llm", factory)

    plan_turn(session(), "월세 보증금 반환을 문의해요.", llm=fake_model())

    factory.assert_not_called()


def test_model_construction_failure_has_the_same_safe_error_contract(monkeypatch):
    state = session()
    before = deepcopy(state)
    factory = Mock(side_effect=RuntimeError("PRIVATE_INITIALIZATION_DETAIL"))
    monkeypatch.setattr(dialogue_planner, "get_llm", factory)

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, "월세 보증금 반환을 문의해요.")

    assert exc.value.code == "model_unavailable"
    assert "PRIVATE_INITIALIZATION_DETAIL" not in str(exc.value)
    assert state == before
    factory.assert_called_once()


def test_foreign_selected_document_is_rejected_before_model_invocation():
    state = session()
    state["documents"] = [{"document_id": "doc-owned", "kind": "contract", "label": "계약서"}]
    before = deepcopy(state)
    model = fake_model()

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, "이 계약서의 보증금을 알려주세요.", document_id="doc-foreign", llm=model)

    assert exc.value.code.startswith("invalid_input:")
    assert "doc-foreign" not in str(exc.value)
    assert state == before
    model.invoke.assert_not_called()


def test_array_wire_output_normalizes_to_attributed_runtime_fact_mapping():
    state = session()
    before = deepcopy(state)
    updates = wire_updates(contract_type={"value": "월세", "evidence": "월세"})
    model = fake_model(payload(statements=updates))

    result = plan_turn(state, "월세 보증금 반환을 문의해요.", llm=model)

    assert result.decision.updates == {"contract_type": {"value": "월세", "evidence": "월세"}}
    assert all(value is not None for value in result.decision.updates.values())
    assert state == before
    model.invoke.assert_called_once()


def test_empty_wire_array_means_no_new_user_facts():
    model = fake_model(payload(statements=wire_updates()))

    result = plan_turn(session(), "보증금 반환의 일반적인 절차를 알려주세요.", llm=model)

    assert result.decision.updates == {}
    assert result.model_invocations == 1


def test_runtime_contract_stays_dict_only_when_array_support_is_not_requested():
    user = "월세 보증금을 문의해요."
    data = payload(statements=wire_updates(contract_type={"value": "월세", "evidence": "월세"}))
    expanded = expand_proposal(json.dumps(data, ensure_ascii=False), user)

    with pytest.raises(DecisionError) as exc:
        parse_decision(expanded, state=session(), user=user)

    assert exc.value.code == "updates"


@pytest.mark.parametrize(
    "updates,user",
    [
        (wire_updates(contract_type={"value": "월세", "evidence": "NEVER_QUOTED_TEXT"}), "월세 보증금을 문의해요."),
        (wire_updates(deposit={"value": "90000000원", "evidence": "보증금은 30000000원"}), "보증금은 30000000원입니다."),
        (wire_updates(contract_ended={"value": "예", "evidence": "계약이 아직 안 끝났어요"}), "계약이 아직 안 끝났어요."),
    ],
)
def test_array_wire_normalization_preserves_value_and_provenance_checks(updates, user):
    state = session()
    before = deepcopy(state)
    model = fake_model(payload(statements=updates))

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, user, llm=model)

    assert exc.value.code.startswith("invalid_decision:")
    assert "NEVER_QUOTED_TEXT" not in str(exc.value)
    assert state == before
    model.invoke.assert_called_once()


def test_unknown_wire_fact_is_rejected_before_runtime_normalization():
    updates = wire_updates(unsupported_legal_conclusion={"value": "확정", "evidence": "보증금"})
    model = fake_model(payload(statements=updates))

    with pytest.raises(PlanningError) as exc:
        plan_turn(session(), "보증금 반환을 문의해요.", llm=model)

    assert exc.value.code.startswith("invalid_decision:")
    model.invoke.assert_called_once()


def test_array_wire_schema_bounds_record_count_and_rejects_extensions():
    schema = output_schema([])
    updates = schema["properties"]["statements"]

    assert schema["additionalProperties"] is False
    assert updates["type"] == "array"
    assert updates["maxItems"] == len(WIRE_FACT_FIELDS)
    variants = updates["items"]["oneOf"]
    records = {variant["properties"]["field"]["const"]: variant for variant in variants}
    assert len(records) == len(variants)
    assert set(records) == WIRE_FACT_FIELDS
    for record in records.values():
        assert record["type"] == "object"
        assert record["additionalProperties"] is False
        assert set(record["required"]) == {"field", "value", "evidence"}
        assert set(record["properties"]) == {"field", "value", "evidence"}
    for field in {"contract_ended", "deposit_returned", "living_in_property", "moved_out", "landlord_notified"}:
        assert set(records[field]["properties"]["value"]["enum"]) == {"예", "아니요", "모름"}
    assert set(records["contract_type"]["properties"]["value"]["enum"]) == {"전세", "월세", "반전세", "모름"}
    assert "임차인" in records["role"]["properties"]["value"]["enum"]
    assert "주택" in records["property_type"]["properties"]["value"]["enum"]


@pytest.mark.parametrize("second_value", ["월세", "전세"])
def test_duplicate_wire_fields_are_deduplicated_only_when_the_entire_record_agrees(second_value):
    state = session()
    before = deepcopy(state)
    updates = [
        {"field": "contract_type", "value": "월세", "evidence": "월세"},
        {"field": "contract_type", "value": second_value, "evidence": "월세"},
    ]
    model = fake_model(payload(statements=updates))

    if second_value == "월세":
        result = plan_turn(state, "월세 보증금 반환을 문의해요.", llm=model)
        assert result.decision.updates == {"contract_type": {"value": "월세", "evidence": "월세"}}
        assert result.normalizations[0]["reason"] == "identical_statement_removed"
    else:
        with pytest.raises(PlanningError) as exc:
            plan_turn(state, "월세 보증금 반환을 문의해요.", llm=model)
        assert exc.value.code.startswith("invalid_decision:")
    assert state == before
    model.invoke.assert_called_once()


@pytest.mark.parametrize(
    "record",
    [
        None,
        "contract_type=월세",
        ["contract_type", "월세", "월세"],
        {"value": "월세", "evidence": "월세"},
        {"field": "contract_type", "value": "월세"},
        {"field": "contract_type", "value": "월세", "evidence": "월세", "source": "official_law"},
        {"field": "contract_type", "value": None, "evidence": "월세"},
        {"field": ["contract_type"], "value": "월세", "evidence": "월세"},
    ],
)
def test_malformed_wire_records_cannot_create_partial_runtime_facts(record):
    state = session()
    before = deepcopy(state)
    model = fake_model(payload(statements=[record]))

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, "월세 보증금 반환을 문의해요.", llm=model)

    assert exc.value.code.startswith("invalid_decision:")
    assert state == before
    model.invoke.assert_called_once()


def ongoing_case(topic="보증금반환"):
    state = session()
    apply_user_update(
        state, user="월세 계약이에요.", topic=topic,
        updates={"contract_type": {"value": "월세", "evidence": "월세"}},
    )
    return state


def validated_proposal(state, user="그 상황에서 준비할 내용을 알려주세요.", **changes):
    internal = {key: changes.pop(key) for key in ("topic_changed", "question", "search_query") if key in changes}
    expanded = json.loads(expand_proposal(json.dumps(payload(**changes), ensure_ascii=False), user))
    expanded.update(internal)
    decision = parse_decision(
        json.dumps(expanded, ensure_ascii=False),
        state=state, user=user, updates_as_list=True,
    )
    return decision, build_decision_input(state, user)


def test_same_topic_question_with_real_history_is_canonical_followup():
    state = ongoing_case()
    decision, context = validated_proposal(state)
    before_decision, before_context = asdict(decision), deepcopy(context)

    canonical, changes = canonicalize_decision(decision, context)

    assert canonical.intent == "followup"
    assert isinstance(changes, tuple)
    assert changes and all(isinstance(change, dict) for change in changes)
    assert changes[0]["from"] == "question"
    assert changes[0]["to"] == "followup"
    assert changes[0]["reason"]
    assert asdict(decision) == before_decision
    assert context == before_context
    after = asdict(canonical)
    assert {key: value for key, value in after.items() if key != "intent"} == {
        key: value for key, value in before_decision.items() if key != "intent"
    }


@pytest.mark.parametrize("case_kind", ["no_topic", "no_history", "different_topic", "proposal_has_no_topic"])
def test_question_without_matching_topic_and_history_keeps_its_label(case_kind):
    state = ongoing_case()
    proposed_topic = "보증금반환"
    if case_kind == "no_topic":
        state["dialogue"]["topic"] = None
    elif case_kind == "no_history":
        state["dialogue"]["history"] = []
    elif case_kind == "different_topic":
        proposed_topic = "시설수리"
    else:
        proposed_topic = None
    decision, context = validated_proposal(state, topic=proposed_topic)

    canonical, changes = canonicalize_decision(decision, context)

    assert canonical == decision
    assert changes == ()


def test_model_selected_owned_document_takes_precedence_over_general_followup_label():
    state = ongoing_case()
    state["documents"] = [{"document_id": "owned", "kind": "contract", "label": "계약서"}]
    decision, context = validated_proposal(state, document_id="owned")

    canonical, changes = canonicalize_decision(decision, context)

    assert canonical.intent == "document_question"
    assert canonical.document_id == "owned"
    assert canonical.action == decision.action
    assert canonical.search_query == decision.search_query
    assert changes


@pytest.mark.parametrize("active", [None, "owned"])
def test_document_inventory_or_ui_selection_alone_does_not_relabel_a_question(active):
    state = session()
    state["documents"] = [{"document_id": "owned", "kind": "contract", "label": "계약서"}]
    ensure_dialogue(state)["active_document_id"] = active
    decision, context = validated_proposal(state, topic=None, document_id=None)

    canonical, changes = canonicalize_decision(decision, context)

    assert canonical == decision
    assert changes == ()


def test_document_canonicalization_defensively_requires_current_ownership():
    state = session()
    state["documents"] = [{"document_id": "owned", "kind": "contract", "label": "계약서"}]
    decision, context = validated_proposal(state, topic=None, document_id="owned")
    context["documents"] = []

    canonical, changes = canonicalize_decision(decision, context)

    assert canonical.intent == "question"
    assert changes == ()


def test_missing_document_id_is_never_an_owned_selection():
    decision, context = validated_proposal(session(), topic=None, document_id=None)
    context["documents"] = [{"document_id": None, "kind": "contract", "label": "손상된 문서 항목"}]

    canonical, changes = canonicalize_decision(decision, context)

    assert canonical.intent == "question"
    assert changes == ()


@pytest.mark.parametrize("same_topic,expected", [(True, "followup"), (False, "question")])
def test_clarification_label_without_pending_question_uses_existing_topic_context(same_topic, expected):
    state = ongoing_case()
    decision, context = validated_proposal(
        state, intent="clarification_answer", topic="보증금반환" if same_topic else "시설수리",
    )

    canonical, changes = canonicalize_decision(decision, context)

    assert canonical.intent == expected
    assert canonical.action == decision.action
    assert canonical.updates == decision.updates
    assert canonical.search_query == decision.search_query
    assert changes


def test_answer_to_a_real_pending_question_keeps_clarification_label():
    state = ongoing_case()
    state["dialogue"]["pending"] = {"field": "contract_ended", "question": "계약은 끝났나요?"}
    decision, context = validated_proposal(
        state, user="네.", intent="clarification_answer",
        statements=wire_updates(contract_ended={"value": "예", "evidence": "네"}),
    )

    canonical, changes = canonicalize_decision(decision, context)

    assert canonical == decision
    assert changes == ()


@pytest.mark.parametrize("intent", ["correction", "topic_change", "explain", "greeting", "followup", "document_question"])
def test_explicit_supported_intents_are_not_rewritten_from_surrounding_context(intent):
    state = ongoing_case()
    if intent == "document_question":
        state["documents"] = [{"document_id": "owned", "kind": "contract"}]
    decision, context = validated_proposal(
        state, intent=intent, topic_changed=intent == "topic_change",
        action="social" if intent == "greeting" else "rag",
        search_query="" if intent == "greeting" else "월세 보증금 반환 절차",
    )

    canonical, changes = canonicalize_decision(decision, context)

    assert canonical == decision
    assert changes == ()


@pytest.mark.parametrize("action", ["rag", "social", "refuse"])
def test_only_inactive_clarification_fields_are_cleared(action):
    state = session()
    decision, context = validated_proposal(
        state, intent="correction" if action == "social" else "question", action=action,
        clarify_field="contract_ended", question="계약은 끝났나요?",
        search_query="월세 보증금 반환 절차" if action == "rag" else "",
    )
    original = asdict(decision)

    canonical, changes = canonicalize_decision(decision, context)

    assert canonical.clarify_field == ("contract_ended" if action == "rag" else None)
    assert canonical.question is None
    if action != "rag":
        assert changes
    for field, value in original.items():
        if field not in {"clarify_field", "question"}:
            assert getattr(canonical, field) == value


def test_real_clarification_fields_survive_label_consistency_check():
    state = session()
    decision, context = validated_proposal(
        state, action="clarify", clarify_field="contract_ended",
        question="계약은 끝났나요?", search_query="",
    )

    canonical, changes = canonicalize_decision(decision, context)

    assert canonical == decision
    assert changes == ()


def test_planning_reports_normalizations_and_preserves_model_raw_label_and_facts():
    state = ongoing_case()
    before = deepcopy(state)
    model = fake_model(payload(statements=wire_updates(contract_type={"value": "월세", "evidence": "월세"})))
    raw = model.invoke.return_value.content

    result = plan_turn(state, "월세인 제 상황에서 다음 순서를 알려주세요.", llm=model)

    assert result.decision.intent == "followup"
    assert result.decision.updates == {"contract_type": {"value": "월세", "evidence": "월세"}}
    assert result.normalizations
    assert isinstance(result.normalizations, tuple)
    assert model.invoke.return_value.content == raw
    assert json.loads(raw)["intent"] == "question"
    assert state == before
    model.invoke.assert_called_once()


def test_canonicalization_never_repairs_a_strict_contract_violation():
    state = ongoing_case()
    before = deepcopy(state)
    model = fake_model(payload(intent="question", topic_changed=True))

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, "월세 보증금 반환을 문의해요.", llm=model)

    assert exc.value.code.startswith("invalid_decision:")
    assert state == before
    model.invoke.assert_called_once()


def test_pending_short_answer_is_grounded_in_existing_facts_without_new_attribution():
    state = ongoing_case()
    state["dialogue"]["pending"] = {"field": "contract_ended", "question": "계약은 끝났나요?"}
    before = deepcopy(state)
    model = fake_model(payload(intent="clarification_answer", statements=wire_updates(
        contract_ended={"value": "예", "evidence": "네"},
    )))

    result = plan_turn(state, "네.", llm=model)

    assert result.decision.intent == "clarification_answer"
    assert result.decision.updates == {"contract_ended": {"value": "예", "evidence": "네"}}
    assert "계약 유형: 월세" in result.decision.search_query
    assert "계약 종료 여부: 예" in result.decision.search_query
    assert result.decision.search_query.endswith("네.")
    assert "source_turn" not in result.decision.search_query
    assert state == before
    model.invoke.assert_called_once()


def test_pending_fact_omission_still_fails_before_query_or_label_can_hide_it():
    state = ongoing_case()
    state["dialogue"]["pending"] = {"field": "contract_ended", "question": "계약은 끝났나요?"}
    before = deepcopy(state)
    model = fake_model(payload(intent="clarification_answer"))

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, "네.", llm=model)

    assert exc.value.code == "invalid_decision:pending_answer_missing"
    assert state == before
    model.invoke.assert_called_once()


def test_query_uses_corrected_facts_and_keeps_the_entire_current_input():
    state = ongoing_case()
    user = "잘못 말했어요. 반전세 계약입니다.\n임차인이 준비할 문서도 함께 알려주세요."
    before = deepcopy(state)
    model = fake_model(payload(intent="correction", statements=wire_updates(
        contract_type={"value": "반전세", "evidence": "반전세"},
    )))

    result = plan_turn(state, user, llm=model)

    assert "계약 유형: 반전세" in result.decision.search_query
    assert "월세" not in result.decision.search_query
    assert result.decision.search_query.endswith(user)
    assert result.decision.updates == {"contract_type": {"value": "반전세", "evidence": "반전세"}}
    assert state == before
    model.invoke.assert_called_once()


def test_topic_change_query_does_not_reuse_previous_case_or_document():
    state = ongoing_case()
    state["documents"] = [{"document_id": "old", "kind": "registry", "label": "이전 등기부"}]
    apply_user_update(state, user="누나의 보증금은 8천만원이에요.", document_id="old", updates={
        "subject": {"value": "누나", "evidence": "누나"},
        "deposit": {"value": "8천만원", "evidence": "8천만원"},
    })
    before = deepcopy(state)
    user = "이번에는 친구의 전세 계약 준비를 물어볼게요."
    model = fake_model(payload(intent="topic_change", topic="계약준비", statements=wire_updates(
        subject={"value": "친구", "evidence": "친구"},
        contract_type={"value": "전세", "evidence": "전세"},
    )))

    result = plan_turn(state, user, llm=model)

    assert result.decision.topic_changed is True
    assert result.decision.intent == "topic_change"
    assert "계약 유형: 전세" in result.decision.search_query
    assert "상담 대상: 친구" in result.decision.search_query
    assert result.decision.search_query.endswith(user)
    for stale in ("누나", "8천만원", "월세", "보증금반환", "등기부"):
        assert stale not in result.decision.search_query
    assert state == before
    model.invoke.assert_called_once()


def test_grounded_query_failure_is_safe_and_does_not_commit_valid_updates():
    state = ongoing_case()
    before = deepcopy(state)
    user = "반전세 " + "설명 " * 660
    assert len(user) <= 2000
    model = fake_model(payload(intent="correction", statements=wire_updates(
        contract_type={"value": "반전세", "evidence": "반전세"},
    )))

    with pytest.raises(PlanningError) as exc:
        plan_turn(state, user, llm=model)

    assert exc.value.code == "invalid_decision:query_context_limit"
    assert user not in str(exc.value)
    assert state == before
    model.invoke.assert_called_once()


@pytest.mark.parametrize("action,intent,field", [
    ("social", "greeting", None), ("clarify", "question", "details"), ("refuse", "question", None),
])
def test_nonrag_routes_do_not_generate_search_or_model_authored_question(action, intent, field):
    state = session()
    before = deepcopy(state)
    model = fake_model(payload(intent=intent, action=action, clarify_field=field))

    result = plan_turn(state, "상황을 더 설명할게요.", llm=model)

    assert result.decision.action == action
    assert result.decision.clarify_field == field
    assert result.decision.question is None
    assert result.decision.search_query == ""
    assert state == before
    model.invoke.assert_called_once()


@pytest.mark.parametrize("value,evidence", [
    ("모름", "종료 여부는 모름"),
    ("아니요", "계약이 아직 안 끝났어요"),
])
def test_hypothetical_current_question_is_preserved_without_overwriting_existing_fact(value, evidence):
    state = ongoing_case()
    apply_user_update(state, user=evidence, updates={
        "contract_ended": {"value": value, "evidence": evidence},
    })
    before = deepcopy(state)
    user = "계약이 끝났다면 필요한 서류가 달라지나요?"
    model = fake_model(payload(intent="followup"))

    result = plan_turn(state, user, llm=model)

    assert result.decision.action == "rag"
    assert result.decision.intent == "followup"
    assert result.decision.updates == {}
    assert f"계약 종료 여부: {value}" in result.decision.search_query
    assert result.decision.search_query.endswith(user)
    assert state == before
    model.invoke.assert_called_once()


@pytest.mark.parametrize("value,evidence,error_code", [
    ("모름", "종료 여부는 모름", "unknown_query_condition"),
    ("아니요", "계약이 아직 안 끝났어요", "query_polarity"),
])
def test_default_contract_does_not_exempt_arbitrary_queries_from_polarity_checks(value, evidence, error_code):
    state = ongoing_case()
    apply_user_update(state, user=evidence, updates={
        "contract_ended": {"value": value, "evidence": evidence},
    })
    before = deepcopy(state)
    user = "필요한 서류를 알려주세요."
    expanded = json.loads(expand_proposal(json.dumps(payload(intent="followup")), user))
    expanded["search_query"] = "계약이 끝났다면 필요한 서류가 달라지나요?"

    with pytest.raises(DecisionError) as exc:
        parse_decision(json.dumps(expanded, ensure_ascii=False), state=state, user=user, updates_as_list=True)

    assert exc.value.code == error_code
    assert state == before


@pytest.mark.parametrize("quote,accepted", [("정정할게요", False), ("월세", True)])
def test_contract_type_quote_must_support_its_value_even_when_the_user_mentions_the_value_elsewhere(quote, accepted):
    state = session()
    before = deepcopy(state)
    user = "정정할게요. 전세가 아니라 월세 계약이에요."
    model = fake_model(payload(intent="correction", statements=wire_updates(
        contract_type={"value": "월세", "evidence": quote},
    )))

    if accepted:
        result = plan_turn(state, user, llm=model)
        assert result.decision.updates == {"contract_type": {"value": "월세", "evidence": "월세"}}
        assert "계약 유형: 월세" in result.decision.search_query
    else:
        with pytest.raises(PlanningError) as exc:
            plan_turn(state, user, llm=model)
        assert exc.value.code.startswith("invalid_decision:")
    assert state == before
    model.invoke.assert_called_once()
