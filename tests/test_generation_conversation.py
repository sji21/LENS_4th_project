from src.generation.conversation import (
    needs_conversation_context,
    resolve_question,
)


def answered_history():
    return [
        {
            "role": "user",
            "content": "임차권등기명령은 언제 신청할 수 있어?",
            "context_content": "임차권등기명령은 언제 신청할 수 있어?",
            "status": None,
        },
        {
            "role": "assistant",
            "content": "화면용 답변",
            "context_content": "임대차가 끝난 후 신청할 수 있습니다.",
            "status": "answered",
        },
    ]


def test_standalone_question_skips_rewriter():
    called = False

    def fake_rewriter(history, question):
        nonlocal called
        called = True
        return question

    result = resolve_question(
        "전입신고와 확정일자는 왜 필요한가요?",
        answered_history(),
        rewriter=fake_rewriter,
    )

    assert result.standalone == "전입신고와 확정일자는 왜 필요한가요?"
    assert result.used_history is False
    assert called is False


def test_followup_is_rewritten_from_answered_history():
    seen = {}

    def fake_rewriter(history, question):
        seen["history"] = history
        seen["question"] = question
        return "임차권등기명령을 신청한 후 바로 이사해도 되나요?"

    result = resolve_question(
        "그럼 신청하면 바로 이사 가도 돼?",
        answered_history(),
        rewriter=fake_rewriter,
    )

    assert result.standalone == "임차권등기명령을 신청한 후 바로 이사해도 되나요?"
    assert result.used_history is True
    assert "임차권등기명령은 언제 신청할 수 있어?" in seen["history"]
    assert "임대차가 끝난 후 신청할 수 있습니다." in seen["history"]


def test_followup_without_answered_history_uses_original():
    result = resolve_question(
        "그럼 언제 해야 해?",
        [],
        rewriter=lambda history, question: "호출되면 안 됩니다",
    )

    assert result.standalone == "그럼 언제 해야 해?"
    assert result.used_history is False


def test_abstained_answer_is_not_used_as_context():
    history = [
        {
            "role": "user",
            "content": "질문",
            "context_content": "질문",
            "status": None,
        },
        {
            "role": "assistant",
            "content": "답변을 바로 제공하기 어렵습니다.",
            "context_content": "",
            "status": "abstained",
        },
    ]

    result = resolve_question(
        "그럼 언제 해야 해?",
        history,
        rewriter=lambda history, question: "호출되면 안 됩니다",
    )

    assert result.standalone == "그럼 언제 해야 해?"
    assert result.used_history is False


def test_rewriter_failure_falls_back_to_original():
    def broken_rewriter(history, question):
        raise RuntimeError("boom")

    result = resolve_question(
        "그럼 언제 해야 해?",
        answered_history(),
        rewriter=broken_rewriter,
    )

    assert result.standalone == "그럼 언제 해야 해?"
    assert result.used_history is False


def test_known_followup_prefixes():
    assert needs_conversation_context("그럼 언제 신청해야 해?")
    assert needs_conversation_context("그 특약은 효력이 있어?")
    assert needs_conversation_context("아까 말한 건 언제까지 해야 해?")
    assert not needs_conversation_context("임차권등기명령은 언제 신청할 수 있나요?")


def test_rewrite_that_changes_application_to_completion_is_rejected():
    result = resolve_question(
        "그럼 신청하면 바로 이사 가도 돼?",
        answered_history(),
        rewriter=lambda _history, _question: (
            "임차권등기명령이 완료된 후 바로 이사해도 되나요?"
        ),
    )

    assert result.standalone == "그럼 신청하면 바로 이사 가도 돼?"
    assert result.used_history is False


def test_rewrite_cannot_invent_number_not_in_conversation():
    result = resolve_question(
        "그럼 언제 이사해도 돼?",
        answered_history(),
        rewriter=lambda _history, _question: (
            "임차권등기명령 신청 3일 후 이사해도 되나요?"
        ),
    )

    assert result.standalone == "그럼 언제 이사해도 돼?"
    assert result.used_history is False
