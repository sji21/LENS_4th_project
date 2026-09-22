from scripts import build_dev100_v2_scoring_worksheet as worksheet
from scripts import validate_dev100_v2_formal_score_review as review


def test_generated_worksheet_is_not_formal_score_ready_before_review():
    import json

    requirements = json.loads(worksheet.REQUIREMENTS.read_text(encoding="utf-8"))
    rows = worksheet.build_rows(requirements)

    errors = review.validate_rows(rows, review.expected_claims(requirements))

    assert errors
    assert any("검토 승인" in error for error in errors)
    assert any("active 또는 inactive" in error for error in errors)


def test_fully_approved_rows_pass_when_sources_and_modes_are_valid():
    import json

    requirements = json.loads(worksheet.REQUIREMENTS.read_text(encoding="utf-8"))
    rows = worksheet.build_rows(requirements)
    for row in rows:
        row["direct_source_id"] = row["candidate_source_ids"].split(";", 1)[0]
        row["review_status"] = "approved"
        row["question_only_activation"] = "active"
        row["context_diagnostic_activation"] = "inactive"

    assert review.validate_rows(rows, review.expected_claims(requirements)) == []
