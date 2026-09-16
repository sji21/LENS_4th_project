"""Keep rejected turns out of RAG until the user clarifies their context."""
from copy import deepcopy

from .dialogue_state import empty_dialogue, ensure_dialogue


RECOVERY_QUESTION = (
    "질문의 맥락을 확실히 이해하지 못했어요. 앞선 상담을 이어가는 질문인가요, "
    "새로운 사람이나 계약에 관한 질문인가요? 아래에서 선택하거나 상황과 질문을 함께 다시 알려주세요."
)


def recovery_pending(state, user, document_id):
    dialogue = ensure_dialogue(state)
    previous = deepcopy(dialogue["pending"])
    if previous and "recovery" in previous:
        previous = previous["recovery"].get("previous_pending")
    choices = []
    for label, prefix in (
        ("이전 상담 이어가기", "이전 상담과 같은 사건입니다.\n"),
        ("새로운 상담", "새로운 사람 또는 계약에 관한 상담입니다.\n"),
    ):
        message = prefix + user
        if len(message) <= 2000:
            choice = {"label": label, "message": message}
            if document_id:
                choice["document_id"] = document_id
            choices.append(choice)
    return {
        "field": "details", "question": RECOVERY_QUESTION, "attempts": 1,
        "choices": choices,
        "recovery": {"previous_pending": previous,
                     "new_case_message": next((c["message"] for c in choices if c["label"] == "새로운 상담"), None)},
    }


def resume_recovery(state, user):
    """Only a stored choice can deterministically reset the old case.

    Free-form restatements still use the planner. Its current-input provenance
    checks also apply to the full question repeated by a confirmation button.
    """
    dialogue = ensure_dialogue(state)
    pending = dialogue["pending"] or {}
    recovery = pending.get("recovery")
    if not isinstance(recovery, dict):
        return
    if not any(c.get("message") == user for c in pending.get("choices", [])):
        return
    if user == recovery.get("new_case_message"):
        fresh = empty_dialogue()
        fresh.update(turn=dialogue["turn"], epoch=dialogue["epoch"] + 1)
        state["dialogue"] = fresh
    else:
        dialogue["pending"] = deepcopy(recovery.get("previous_pending"))
