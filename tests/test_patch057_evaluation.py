import json

from scripts import patch057_comprehensive_eval as evaluation


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8")


def _case(case_id, case_number, key, *, court="대법원", date="2020-01-01"):
    return {"chunk_id": f"case:{case_id}#0", "text": case_number,
            "metadata": {"doc_type": "case", "case_id": case_id,
                         "case_number": case_number, "canonical_case_key": key,
                         "court_name": court, "decision_date": date,
                         "source_url": f"https://example.test/{case_id}"}}


def test_legacy_case_mapping_is_exact_and_keeps_missing(tmp_path):
    dev = tmp_path / "dev.jsonl"
    external = tmp_path / "external.jsonl"
    old_chunks = tmp_path / "old.jsonl"
    _write_jsonl(dev, [{"qid": "d", "question": "q", "gold_case_ids": ["CASE-A", "CASE-X"]}])
    _write_jsonl(external, [{"qid": "e", "question": "q2", "gold_case_ids": ["CASE-A"]}])
    _write_jsonl(old_chunks, [
        _case("CASE-A", "2020다1", "old-a"),
        _case("CASE-X", "2020다9", "old-x"),
    ])
    mapping, summary = evaluation.map_legacy_cases(
        dev, external, old_chunks, [_case("101", "2020다1", "new-a")])

    assert summary == {"requested": 2, "mapped": 1, "lower_bound_coverage": 0.5,
                       "unknown": ["CASE-X"], "missing": ["CASE-X"],
                       "metadata_mismatch": [], "ambiguous": []}
    assert mapping["CASE-A"]["canonical_case_key"] == "new-a"
    targets = evaluation.mapped_case_targets(["CASE-A", "CASE-X"], mapping)
    assert targets == [
        {"label": "CASE-A", "identity": "new-a", "available": True},
        {"label": "CASE-X", "identity": "unavailable:legacy-case:CASE-X", "available": False},
    ]


def test_official_case_ids_map_directly_and_do_not_guess():
    targets = evaluation.official_case_targets(
        ["123", "999"], [_case("123", "2020다1", "key-123")])
    assert targets[0] == {"label": "123", "identity": "key-123", "available": True}
    assert targets[1] == {"label": "999", "identity": "unavailable:official-case:999",
                          "available": False}


def test_holdout_source_identity_uses_article_and_canonical_case_key():
    case = _case("167313", "2010다42990", "case-key")
    assert evaluation.source_identity(
        "A3", {"title": "주택임대차보호법 제3조", "url": ""},
        {"주택임대차보호법-제3조"}, [case]) == (
            "laws", "주택임대차보호법-제3조", True)
    assert evaluation.source_identity(
        "P2010", {"title": "대법원 2012. 7. 12. 선고 2010다42990",
                  "url": "https://law.go.kr/x?precSeq=167313"}, set(), [case]) == (
            "cases", "case-key", True)
    assert evaluation.source_identity(
        "Iform4", {"title": "공공주택 특별법 시행규칙 별지 제4호서식", "url": ""},
        set(), [case]) == (
            "forms", "공공주택특별법시행규칙-별지제4호서식", False)


def test_holdout_case_mapping_requires_exact_court_and_consolidated_number():
    wrong_court = _case("1", "90다카11377", "wrong", court="서울고등법원")
    consolidated = _case("2", "2017다224630, 224647", "joined")
    assert evaluation.source_identity(
        "P90", {"title": "대법원 1990. 8. 14. 선고 90다카11377", "url": ""},
        set(), [wrong_court]) == ("cases", "unavailable:ho-source:P90", False)
    assert evaluation.source_identity(
        "P2017", {"title": "대법원 2017. 10. 12. 선고 2017다224630, 224647",
                  "url": ""}, set(), [consolidated]) == ("cases", "joined", True)
    longer = _case("3", "90다카113770", "prefix-is-not-exact")
    assert evaluation.source_identity(
        "P90", {"title": "대법원 1990. 8. 14. 선고 90다카11377", "url": ""},
        set(), [longer]) == ("cases", "unavailable:ho-source:P90", False)


def test_law_identity_matches_spacing_without_changing_article_number():
    targets = evaluation.split_law_targets(
        ["국세징수법시행령-제97조"], {"국세징수법 시행령-제97조"})
    assert targets["laws"] == [{"label": "국세징수법시행령-제97조",
                                  "identity": "국세징수법시행령-제97조",
                                  "available": True}]


def test_public_law_denominators_keep_all_twenty_civil_inputs():
    jobs = evaluation.public_law_jobs({
        "주택임대차보호법-제3조", "주택임대차보호법-제3조의2",
        "주택임대차보호법-제3조의3", "주택임대차보호법-제3조의6",
        "주택임대차보호법-제4조", "주택임대차보호법-제6조",
        "주택임대차보호법-제6조의2", "주택임대차보호법-제6조의3",
        "주택임대차보호법-제7조", "주택임대차보호법-제7조의2",
        "주택임대차보호법-제8조", "주택임대차보호법-제10조",
        "주택임대차보호법-제10조의2", "주택임대차보호법-제12조",
        "주택임대차보호법-제14조", "주택임대차보호법-제21조",
        "주택임대차보호법 시행령-제9조", "주택임대차보호법 시행령-제10조",
        "주택임대차보호법 시행령-제11조", "민법-제623조", "민법-제626조",
        "민법-제627조", "민법-제629조", "민법-제634조",
    })
    counts = evaluation.job_counts(jobs)["groups"]
    assert counts == {
        "legacy_dev": {"inputs": 24, "scored": 24},
        "legacy_holdout": {"inputs": 18, "scored": 18},
        "civil_public": {"inputs": 20, "scored": 15},
    }


def test_score_keeps_unavailable_gold_in_recall_and_full_denominator():
    job = {"qid": "HO-X", "mode": "question_only", "group": "ho30", "score": True,
           "targets": {
               "laws": [{"label": "law", "identity": "law-1", "available": True}],
               "civil_laws": [],
               "cases": [{"label": "case", "identity": "unavailable:case", "available": False}],
               "forms": []}}
    result = {"laws": [{"article_id": "law-1"}], "civil_laws": [], "cases": [], "guides": []}
    report = evaluation.score_rows([{"job": job, "runs": {"raw3": {"result": result}}}])

    law = report["channel_metrics"]["ho30:raw3:laws:@3"]
    case = report["channel_metrics"]["ho30:raw3:cases:@5"]
    complete = report["complete_metrics"]["ho30:raw3:all-required"]
    assert law["all_rate"] == 1
    assert case["mean_recall"] == 0
    assert case["required_occurrences"] == 1
    assert case["available_occurrences"] == 0
    assert complete["n"] == 1
    assert complete["complete_rate"] == 0
    assert complete["micro_recall"] == 0.5
    assert complete["law_complete_rate"] == 1


def test_score_uses_only_real_channel_cutoffs_and_stratifies_holdout():
    job = {"qid": "HO-X", "mode": "question_only", "group": "ho30", "track": "mixed_source",
           "suite": "holdout30", "score": True,
           "targets": {"laws": [{"label": "l", "identity": "l", "available": True}],
                       "civil_laws": [{"label": "c", "identity": "c", "available": True}],
                       "cases": [{"label": "p", "identity": "p", "available": True}],
                       "forms": []}}
    result = {"laws": [{"article_id": "l"}], "civil_laws": [{"article_id": "c"}],
              "cases": [{"canonical_case_key": "p"}], "guides": []}
    report = evaluation.score_rows([{"job": job, "runs": {
        "raw3": {"result": result}, "staged_operating": {"result": result}}}])
    keys = set(report["channel_metrics"])
    assert "ho30:raw3:laws:@5" not in keys
    assert "ho30:raw3:civil_laws:@5" not in keys
    assert "ho30:staged_operating:cases:@3" not in keys
    assert "ho30:staged_operating:cases:@2" in keys
    assert report["complete_metrics"][
        "ho30:mixed_source:raw3:all-required"]["complete_rate"] == 1


def test_case_only_complete_metrics_do_not_claim_law_success():
    job = {"qid": "case", "mode": "question_only", "group": "case_legacy20",
           "score": True, "targets": {"laws": [], "civil_laws": [],
                                       "cases": [{"label": "p", "identity": "p", "available": True}],
                                       "forms": []}}
    result = {"laws": [], "civil_laws": [], "cases": [{"canonical_case_key": "p"}], "guides": []}
    value = evaluation.score_rows([{"job": job, "runs": {"raw5": {"result": result}}}])[
        "complete_metrics"]["case_legacy20:raw5:all-required"]
    assert value["law_n"] == 0
    assert value["law_complete_rate"] is None


def test_score_rejects_empty_capture_manifest(tmp_path):
    capture = tmp_path / "capture"
    capture.mkdir()
    (capture / "manifest.json").write_text("{}\n", encoding="utf-8")
    try:
        evaluation.score(capture, tmp_path / "score.json")
    except ValueError as error:
        assert "file set" in str(error)
    else:
        raise AssertionError("empty manifest was accepted")


def test_expected_total_includes_legacy20_and_unscored_civil_five():
    assert evaluation.EXPECTED["jobs"] == 368
    assert sum(evaluation.EXPECTED["public_scored"].values()) == 98
    assert sum(evaluation.EXPECTED["public_inputs"].values()) == 103
