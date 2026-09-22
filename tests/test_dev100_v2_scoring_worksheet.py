from scripts import build_dev100_v2_scoring_worksheet as worksheet


def test_worksheet_preserves_all_claims_without_marking_them_reviewed():
    import json

    requirements = json.loads(worksheet.REQUIREMENTS.read_text(encoding="utf-8"))
    rows = worksheet.build_rows(requirements)

    assert len(rows) == 349
    assert {row["qid"] for row in rows} == {row["qid"] for row in requirements}
    assert {row["review_status"] for row in rows} <= {"unreviewed", "candidate_ready"}
    assert all(not row["direct_source_id"] for row in rows)
    assert sum(bool(row["proposed_direct_source_id"]) for row in rows) == 156
