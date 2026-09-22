from scripts import run_dev100_v2_generation as runner
from src.generation.models import Answer


def test_successful_repair_diagnostics_are_opt_in():
    answer = Answer(
        question="질문", status="answered", text="답변",
        diagnostic_initial_draft="초안", diagnostic_repair_draft="보정",
        initial_validation_codes=("semantic",), repair_attempts=1,
    )
    assert "initial_draft" not in runner._answer_record(answer)
    record = runner._answer_record(answer, include_rejected_draft=True)
    assert record["initial_draft"] == "초안"
    assert record["repair_draft"] == "보정"
    assert "rejected_draft" not in record


def test_runner_loads_each_dev100_v2_question_once():
    rows = runner._load_questions(runner.DATASET, "question_only")

    assert len(rows) == 100
    assert len({row["qid"] for row in rows}) == 100
    assert all(row["question"].strip() for row in rows)


def test_runner_selects_requested_questions_in_requested_order():
    rows = runner._load_questions(runner.DATASET, "question_only")

    selected = runner._select_questions(rows, "DEV-019, DEV-007")

    assert [row["qid"] for row in selected] == ["DEV-019", "DEV-007"]


def test_runner_rejects_unknown_or_duplicate_selected_question_ids():
    rows = runner._load_questions(runner.DATASET, "question_only")

    try:
        runner._select_questions(rows, "DEV-007,DEV-007")
    except ValueError as error:
        assert "중복" in str(error)
    else:
        raise AssertionError("중복 ID를 거절해야 합니다.")

    try:
        runner._select_questions(rows, "DEV-999")
    except ValueError as error:
        assert "없는" in str(error)
    else:
        raise AssertionError("없는 ID를 거절해야 합니다.")


def test_runner_summary_separates_validation_repair_and_scope_reasons():
    rows = [
        {
            "answer": {
                "status": "answered", "validation_codes": [], "repair_attempts": 1,
                "refusal_reason": "",
            },
            "execution_error": "",
        },
        {
            "answer": {
                "status": "abstained", "validation_codes": ["paragraph"], "repair_attempts": 0,
                "refusal_reason": "",
            },
            "execution_error": "",
        },
        {
            "answer": {
                "status": "refused", "validation_codes": [], "repair_attempts": 0,
                "refusal_reason": "semantic_out_of_scope",
            },
            "execution_error": "",
        },
    ]

    assert runner._summary(rows) == {
        "total": 3,
        "answered": 1,
        "abstained": 1,
        "refused": 1,
        "generation_errors": 0,
        "repair_attempted": 1,
        "validation_code_counts": {"paragraph": 1},
        "refusal_reason_counts": {"semantic_out_of_scope": 1},
    }


def test_runner_records_rejected_draft_only_when_explicitly_requested():
    answer = Answer(
        question="질문",
        status="abstained",
        text="검증 보류",
        raw_text="검증 전 후보 문장",
        validation_codes=("unsupported_claim",),
    )

    assert "rejected_draft" not in runner._answer_record(answer)
    record = runner._answer_record(answer, include_rejected_draft=True)
    assert record["rejected_draft"] == answer.raw_text
    assert record["initial_draft"] == ""
    assert record["repair_draft"] == ""
