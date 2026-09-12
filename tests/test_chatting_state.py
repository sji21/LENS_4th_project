"""Conversation state contracts, independent of model wording and transport."""

from copy import deepcopy
import json

import pytest

from chat.dialogue_state import (
    apply_user_update,
    empty_dialogue,
    ensure_dialogue,
    invalidate_document,
    record_answer,
)


def session():
    return {"messages": [], "documents": [], "completed_requests": []}


def add_deposit_situation(state):
    return apply_user_update(
        state,
        user="월세 계약이 끝났는데 보증금을 못 받았어요.",
        updates={
            "contract_type": {"value": "월세", "evidence": "월세"},
            "contract_ended": {"value": "예", "evidence": "계약이 끝났는데"},
            "deposit_returned": {"value": "아니요", "evidence": "보증금을 못 받았어요"},
        },
        topic="보증금반환",
    )


def test_legacy_session_gets_independent_defaults_without_losing_web_state():
    state = session()
    state["messages"].append({"role": "assistant", "content": "기존 공개 답변"})
    state["completed_requests"].append("already-finished")
    original = deepcopy(state)

    dialogue = ensure_dialogue(state)

    assert dialogue["version"] == 1
    assert dialogue["turn"] == 0
    assert dialogue["facts"] == {}
    assert dialogue["history"] == []
    assert dialogue["pending"] is None
    assert dialogue["last_answer"] is None
    assert state["messages"] == original["messages"]
    assert state["completed_requests"] == original["completed_requests"]
    other = empty_dialogue()
    dialogue["facts"]["temporary"] = {"value": "테스트"}
    assert "temporary" not in other["facts"]


@pytest.mark.parametrize(
    "stored",
    [None, [], "broken", {"version": 999}, {"version": 1, "facts": []}],
)
def test_malformed_or_unknown_version_dialogue_resets_only_dialogue(stored):
    state = session()
    state["dialogue"] = stored
    state["messages"] = [{"role": "user", "content": "보존할 웹 이력"}]

    dialogue = ensure_dialogue(state)

    assert dialogue == empty_dialogue()
    assert state["messages"] == [{"role": "user", "content": "보존할 웹 이력"}]


def test_user_facts_record_quoted_provenance_without_claiming_legal_certainty():
    state = session()

    dialogue = add_deposit_situation(state)

    assert dialogue["turn"] == 1
    fact = dialogue["facts"]["contract_ended"]
    assert fact["value"] == "예"
    assert fact["evidence"] == "계약이 끝났는데"
    assert fact["source_turn"] == 1
    assert fact["source"] == "user_statement"
    assert fact["certainty"] == "reported"
    assert dialogue["topic"] == "보증금반환"


def test_json_reload_preserves_turn_provenance_and_allows_correction():
    state = session()
    add_deposit_situation(state)
    restored = json.loads(json.dumps(state, ensure_ascii=False))

    dialogue = apply_user_update(
        restored,
        user="종료됐다고 잘못 말했어요. 계약은 아직 안 끝났어요.",
        updates={"contract_ended": {"value": "아니요", "evidence": "아직 안 끝났어요"}},
    )

    assert dialogue["turn"] == 2
    assert dialogue["facts"]["contract_ended"]["value"] == "아니요"
    assert dialogue["facts"]["contract_ended"]["source_turn"] == 2
    assert dialogue["facts"]["contract_type"]["value"] == "월세"
    assert dialogue["facts"]["deposit_returned"]["value"] == "아니요"
    assert dialogue["changes"][-1]["field"] == "contract_ended"
    assert dialogue["changes"][-1]["previous"]["value"] == "예"
    assert dialogue["changes"][-1]["replacement"]["source_turn"] == 2
    history = json.dumps(dialogue["history"], ensure_ascii=False)
    assert "계약이 끝났는데" in history
    assert "아직 안 끝났어요" in history
    assert state["dialogue"]["facts"]["contract_ended"]["value"] == "예"


def test_unknown_response_is_not_coerced_to_a_positive_or_negative_fact():
    state = session()
    add_deposit_situation(state)

    dialogue = apply_user_update(
        state,
        user="정확한 종료 여부는 모르겠어요.",
        updates={"contract_ended": {"value": "모름", "evidence": "종료 여부는 모르겠어요"}},
    )

    assert dialogue["facts"]["contract_ended"]["value"] == "모름"
    assert dialogue["facts"]["contract_ended"]["certainty"] == "unknown"
    assert dialogue["facts"]["deposit_returned"]["value"] == "아니요"


def test_other_sessions_and_caller_update_objects_do_not_share_mutable_state():
    first, second = session(), session()
    updates = {"contract_type": {"value": "월세", "evidence": "월세"}}
    apply_user_update(first, user="월세에 살아요.", updates=updates)
    ensure_dialogue(second)

    updates["contract_type"]["value"] = "전세"
    first["dialogue"]["facts"]["contract_type"]["evidence"] = "수정된 첫 상태"

    assert first["dialogue"]["facts"]["contract_type"]["value"] == "월세"
    assert second["dialogue"]["facts"] == {}
    assert second["dialogue"]["history"] == []


def test_topic_change_clears_previous_subject_and_starts_current_turn_history():
    state = session()
    state["documents"] = [{"document_id": "doc-a", "kind": "contract", "label": "계약서"}]
    add_deposit_situation(state)
    apply_user_update(state, user="이 문서를 참고하세요.", document_id="doc-a")
    state["dialogue"]["pending"] = {"field": "living_in_property", "question": "그 집에 거주 중인가요?"}
    record_answer(state, {"status": "answered", "content": "이전 상담 공개 답변", "sources": []})
    old_epoch = state["dialogue"]["epoch"]

    dialogue = apply_user_update(
        state,
        user="이제 제 전세 집의 보일러 수리 문제를 물을게요.",
        topic="시설수리",
        topic_changed=True,
        updates={"contract_type": {"value": "전세", "evidence": "전세"}},
    )

    assert dialogue["epoch"] == old_epoch + 1
    assert dialogue["topic"] == "시설수리"
    assert set(dialogue["facts"]) == {"contract_type"}
    assert dialogue["facts"]["contract_type"]["value"] == "전세"
    assert dialogue["pending"] is None
    assert dialogue["changes"] == []
    assert dialogue["last_answer"] is None
    assert dialogue["active_document_id"] is None
    assert len(dialogue["history"]) == 1
    assert "보일러 수리" in json.dumps(dialogue["history"], ensure_ascii=False)
    assert "보증금을 못 받았어요" not in json.dumps(dialogue["history"], ensure_ascii=False)


def test_recent_user_history_is_bounded_without_erasing_active_facts():
    state = session()
    add_deposit_situation(state)
    for number in range(12):
        apply_user_update(state, user=f"후속 질문 {number}를 할게요.")

    dialogue = ensure_dialogue(state)

    assert dialogue["turn"] == 13
    assert len(dialogue["history"]) == 8
    history = json.dumps(dialogue["history"], ensure_ascii=False)
    assert "후속 질문 0를" not in history
    assert "후속 질문 11를" in history
    assert dialogue["facts"]["contract_ended"]["value"] == "예"


def test_owned_document_can_be_selected_and_foreign_document_is_atomic_failure():
    state = session()
    state["documents"] = [{"document_id": "owned-document", "kind": "contract"}]
    apply_user_update(state, user="제 계약서예요.", document_id="owned-document")
    before = deepcopy(state)

    with pytest.raises(ValueError):
        apply_user_update(
            state,
            user="다른 주제로 월세 계약을 물을게요.",
            document_id="another-session-document",
            topic="계약갱신",
            topic_changed=True,
            updates={"contract_type": {"value": "월세", "evidence": "월세"}},
        )

    assert state == before
    assert state["dialogue"]["active_document_id"] == "owned-document"


@pytest.mark.parametrize(
    "bad_update",
    [
        {"value": "예", "evidence": "계약이 끝났어요"},
        {"value": "예", "evidence": ""},
        {"value": "예"},
        {"value": True, "evidence": "월세"},
        {"value": ["예"], "evidence": "월세"},
        {"value": "예", "evidence": 1},
    ],
)
def test_invalid_quote_or_value_rolls_back_all_updates_and_topic_change(bad_update):
    state = session()
    add_deposit_situation(state)
    before = deepcopy(state)

    with pytest.raises(ValueError):
        apply_user_update(
            state,
            user="이제 월세에 대해 물을게요.",
            topic="계약갱신",
            topic_changed=True,
            updates={
                "contract_type": {"value": "월세", "evidence": "월세"},
                "contract_ended": bad_update,
            },
        )

    assert state == before


def test_invalid_first_update_does_not_even_insert_default_dialogue():
    state = session()
    before = deepcopy(state)

    with pytest.raises(ValueError):
        apply_user_update(
            state,
            user="월세예요.",
            updates={"contract_ended": {"value": "예", "evidence": "존재하지 않는 인용"}},
        )

    assert state == before


def test_answer_memory_keeps_public_text_and_sources_without_private_context():
    state = session()
    add_deposit_situation(state)
    source = {"label": "공개 근거", "url": "https://example.com/evidence"}
    message = {
        "status": "answered",
        "content": "최종 검증 경로를 통과한 공개 답변",
        "sources": [source],
        "raw_text": "RAW_GENERATION_MUST_NOT_BECOME_MEMORY",
        "context_content": "PRIVATE_CONTEXT_MUST_NOT_BECOME_MEMORY",
    }

    record_answer(state, message)

    answer = state["dialogue"]["last_answer"]
    assert answer["content"] == message["content"]
    assert answer["sources"] == [source]
    assert answer["validation"] == "existing_pipeline_passed"
    assert "RAW_GENERATION" not in json.dumps(state)
    assert "PRIVATE_CONTEXT" not in json.dumps(state)
    assert state["dialogue"]["last_status"] == "answered"
    source["label"] = "외부에서 수정한 값"
    assert answer["sources"][0]["label"] == "공개 근거"


@pytest.mark.parametrize("status", ["abstained", "refused"])
def test_nonanswers_drop_previous_answer_but_keep_user_statements(status):
    state = session()
    add_deposit_situation(state)
    record_answer(state, {"status": "answered", "content": "이전 공개 답변", "sources": []})
    before_facts = deepcopy(state["dialogue"]["facts"])
    before_history = deepcopy(state["dialogue"]["history"])

    record_answer(
        state,
        {"status": status, "content": "응답 보류 안내", "context_content": "검증되지 않은 법률 설명", "raw_text": "비공개 생성문"},
    )

    assert state["dialogue"]["last_answer"] is None
    assert state["dialogue"]["last_status"] == status
    assert state["dialogue"]["facts"] == before_facts
    assert state["dialogue"]["history"] == before_history
    assert "검증되지 않은 법률 설명" not in json.dumps(state, ensure_ascii=False)
    assert "비공개 생성문" not in json.dumps(state, ensure_ascii=False)


def test_document_deletion_invalidates_derived_state_and_answer_memory():
    state = session()
    state["documents"] = [{"document_id": "doc-a", "kind": "contract"}]
    add_deposit_situation(state)
    apply_user_update(state, user="이 계약서를 참고해 주세요.", document_id="doc-a")
    record_answer(state, {"status": "answered", "content": "삭제할 문서에 대한 설명", "sources": []})

    invalidate_document(state, "doc-a")
    restored = json.loads(json.dumps(state, ensure_ascii=False))

    assert ensure_dialogue(restored) == empty_dialogue()
    assert "삭제할 문서에 대한 설명" not in json.dumps(restored["dialogue"], ensure_ascii=False)
    assert "보증금을 못 받았어요" not in json.dumps(restored["dialogue"], ensure_ascii=False)


def test_changed_user_statement_invalidates_pre_correction_answer():
    state = session()
    apply_user_update(state, user="전세 계약이에요.", updates={"contract_type": {"value": "전세", "evidence": "전세"}})
    record_answer(state, {"status": "answered", "content": "이전 설명", "sources": []})
    apply_user_update(state, user="전세가 아니라 월세예요.", updates={"contract_type": {"value": "월세", "evidence": "월세"}})
    assert state["dialogue"]["last_answer"] is None
    assert state["dialogue"]["facts"]["contract_type"]["value"] == "월세"
