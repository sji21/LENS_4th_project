from scripts import audit_dev100_v2_response_status as audit


def test_review2_marks_every_dev_item_as_having_a_required_current_answer():
    expected = audit.expected_answer_qids(audit.EXPECTED.read_text(encoding="utf-8"))

    assert expected == {f"DEV-{number:03d}" for number in range(1, 101)}


def test_status_audit_marks_whole_answer_abstention_against_required_answer():
    rows = audit.audit(
        [
            {"qid": "DEV-001", "answer": {"status": "answered"}},
            {"qid": "DEV-002", "answer": {"status": "abstained"}},
        ],
        {"DEV-001", "DEV-002"},
    )

    assert [row["status_verdict"] for row in rows] == ["status_matched", "whole_answer_over_abstained"]
