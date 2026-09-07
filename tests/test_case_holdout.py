from src.evaluation.case_holdout import check_gold_case_ids, evaluate_case_holdout, load_case_holdout
from src.retrieval.retriever import BM25Retriever


def _chunks():
    return [
        {"chunk_id": "case:100#0", "text": "보증금 반환과 동시이행", "metadata": {"case_id": "100"}},
        {"chunk_id": "case:200#0", "text": "전입신고와 대항력", "metadata": {"case_id": "200"}},
    ]


def test_case_holdout_scores_stable_case_id():
    chunks = _chunks()
    questions = [{"qid": "case-holdout-001", "question": "보증금 반환 동시이행", "gold_case_ids": ["100"]}]

    report = evaluate_case_holdout(questions, chunks, BM25Retriever(chunks))

    assert report["metrics"] == {"hit_at_1": 1.0, "hit_at_3": 1.0, "hit_at_5": 1.0, "mrr": 1.0}
    assert report["results"][0]["rank"] == 1


def test_case_holdout_rejects_missing_gold_case_id(tmp_path):
    path = tmp_path / "holdout.jsonl"
    path.write_text('{"qid":"x","question":"질문","gold_case_ids":["999"]}\n', encoding="utf-8")

    questions = load_case_holdout(path)

    assert check_gold_case_ids(questions, _chunks()) == ["x: 999"]
