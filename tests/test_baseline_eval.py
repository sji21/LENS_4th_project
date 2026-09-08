"""Regression metrics and invalid-input handling without models or network."""

import json
from types import SimpleNamespace

import pytest

from src.evaluation.baseline import evaluate, load_dataset, main, prepare_questions


def chunk(cid, aid, kind="law"):
    return {"chunk_id": cid, "text": aid, "metadata": {
        "doc_type": kind, "article_id": aid, "case_id": aid}}


def test_fixed_service_call_and_distinct_article_metrics():
    chunks = [chunk("c1", "a"), chunk("c2", "a"), chunk("c3", "b"), chunk("c4", "c")]
    calls = []

    class Service:
        def search(self, query, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(laws=[SimpleNamespace(chunk_id=c["chunk_id"]) for c in chunks],
                                   guides=[object()])

    result = evaluate(Service(), [{"qid": "q", "question": "query", "gold": ["b", "missing"]}], chunks, "law")
    assert calls == [{"k_law": 5, "k_case": 5, "k_guide": 2}]
    assert result["metrics"]["hit@1"] == 0
    assert result["metrics"]["hit@2"] == 1
    assert result["metrics"]["recall@3"] == .5
    assert result["metrics"]["mrr"] == .5
    assert result["questions"][0]["retrieved_ids"] == ["a", "b", "c"]


def test_case_uses_case_ids_and_does_not_score_guides():
    chunks = [chunk("c", "case-1", "case")]
    class Service:
        def search(self, *args, **kwargs):
            return SimpleNamespace(cases=[SimpleNamespace(chunk_id="c")], guides=[])
    result = evaluate(Service(), [{"qid": "q", "question": "query", "gold": ["case-1"]}], chunks, "case")
    assert result["metrics"]["hit@2"] == 1
    assert "guide_accuracy" not in result


def test_missing_gold_is_not_silently_removed():
    with pytest.raises(ValueError, match="Gold absent"):
        prepare_questions([{"qid": "q", "gold_articles": ["a", "missing"]}], [chunk("c", "a")], "law")


def test_explicit_guide_gold_exclusion_is_reported():
    rows = [{"qid": "mixed", "gold_articles": ["a", "guide"]},
            {"qid": "guide-only", "gold_articles": ["guide"]}]
    selected, excluded = prepare_questions(rows, [chunk("a", "a"), chunk("g", "guide", "guide")], "law")
    assert [r["gold"] for r in selected] == [["a"]]
    assert len(excluded) == 2


@pytest.mark.parametrize("rows", [[], [{"qid": "q", "question": "x", "gold_case_ids": []}],
    [{"qid": "q", "question": "x", "gold_case_ids": "case"}],
    [{"qid": "q", "question": "x", "gold_case_ids": ["case"]}] * 2])
def test_rejects_invalid_datasets(tmp_path, rows):
    path = tmp_path / "eval.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(path, "case")


def test_existing_output_is_preserved(tmp_path):
    report = tmp_path / "report.json"
    report.write_text("frozen", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--kind", "law", "--eval-set", "missing.jsonl", "--out", str(report)])
    assert report.read_text() == "frozen"


def test_missing_input_does_not_publish_report(tmp_path):
    report = tmp_path / "report.json"
    with pytest.raises(SystemExit):
        main(["--kind", "case", "--eval-set", str(tmp_path / "missing.jsonl"), "--out", str(report)])
    assert not report.exists()
