"""Regression metrics and invalid-input handling without models or network."""

import json
from types import SimpleNamespace

import pytest

from src.evaluation.baseline import evaluate, load_dataset, main, prepare_questions


def test_settings_include_actual_civil_and_custom_law_configuration():
    from dataclasses import replace
    from src.evaluation.baseline import settings
    from src.retrieval.service import LAW, RetrievalService

    chunks = [chunk("law", "a"), chunk("civil", "민법-제632조")]
    chunks[1]["metadata"]["title"] = "민법"
    service = RetrievalService(chunks, law=replace(LAW, rrf_k=9))
    captured = settings(service)
    assert captured["search_k"] == {"k_law": 5, "k_case": 5, "k_guide": 2, "k_civil": 3}
    config = captured["corpora"]
    assert config["law"]["rrf_k"] == config["law"]["retriever"]["rrf_k"] == 9
    assert "민법-제632조" in config["civil"]["include_ids"]
    assert config["civil"]["retriever"]["rrf_k"] == 5


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
    assert calls == [{"k_law": 5, "k_case": 5, "k_guide": 2, "k_civil": 3}]
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


@pytest.mark.parametrize("article", [623, 626, 627, 629, 632, 634, 640])
def test_separate_civil_gold_is_scored(article):
    aid = f"민법-제{article}조"
    class Service:
        def search(self, query, **kwargs):
            assert kwargs["k_civil"] == 3
            return SimpleNamespace(laws=[], civil_laws=[SimpleNamespace(chunk_id="c")], guides=[])
    result = evaluate(Service(), [{"qid": "q", "question": "q", "gold": [aid]}],
                      [chunk("c", aid)], "law", law_scope="civil")
    assert result["metrics"]["hit@3"] == 1
    assert result["metrics"]["mrr"] == 1
    assert "hit@5" not in result["metrics"]


def test_combined_coverage_preserves_separate_budgets_and_no_merged_rank():
    chunks = [chunk(f"l{i}", f"law-{i}") for i in range(6)]
    chunks += [chunk(f"c{i}", f"민법-제{i}조") for i in range(4)]
    class Service:
        def search(self, query, **kwargs):
            return SimpleNamespace(laws=[SimpleNamespace(chunk_id=f"l{i}") for i in range(6)],
                civil_laws=[SimpleNamespace(chunk_id=f"c{i}") for i in range(4)], guides=[])
    result = evaluate(Service(), [{"qid": "q", "question": "q",
        "gold": ["law-4", "민법-제2조", "민법-제3조"]}], chunks, "law", law_scope="combined")
    assert result["metrics"] == {"hit@law5+civil3": 1, "all@law5+civil3": 0,
                                 "recall@law5+civil3": 2 / 3}
    row = result["questions"][0]
    assert len(row["retrieved_ids"]) == 8
    assert "gold_ranks" not in row
    civil = evaluate(Service(), [{"qid": "q", "question": "q", "gold": ["민법-제3조"]}],
                     chunks, "law", law_scope="civil")
    assert civil["metrics"]["mrr"] == 0
    assert civil["metrics"]["hit@3"] == 0


@pytest.mark.parametrize("scope,gold", [("general", "민법-제626조"), ("civil", "주택임대차보호법-제3조")])
def test_wrong_channel_gold_fails_before_search(scope, gold):
    with pytest.raises(ValueError, match="Gold outside law_scope"):
        evaluate(None, [{"qid": "q", "question": "q", "gold": [gold]}], [], "law", law_scope=scope)


def test_case_rejects_civil_scope():
    with pytest.raises(ValueError, match="only applies"):
        evaluate(None, [], [], "case", law_scope="civil")


def test_published_regression_scores_all_general_and_civil_rows():
    from pathlib import Path
    from scripts.evaluate_civil_regression import evaluate_published_regression
    rows = load_dataset(Path("data/eval/minbeop_review_holdout_20260901.jsonl"), "law")
    selected = [{**r, "gold": r["gold_articles"]} for r in rows if r["gold_articles"]]
    chunks = [chunk(a, a) for a in sorted({a for r in selected for a in r["gold"]})]
    by_query = {r["question"]: r["gold"] for r in selected}
    class Service:
        def search(self, query, **kwargs):
            gold = by_query[query]
            return SimpleNamespace(
                laws=[SimpleNamespace(chunk_id=a) for a in gold if not a.startswith("민법-")],
                civil_laws=[SimpleNamespace(chunk_id=a) for a in gold if a.startswith("민법-")], guides=[])
    result = evaluate_published_regression(Service(), selected, chunks)
    assert result["n"] == 15
    assert result["metrics"]["all@law5+civil3"] == 1
    assert sum(all(a.startswith("민법-") for a in r["gold"]) for r in selected) == 7
