import unittest

from src.evaluation.metrics import (
    QuestionResult,
    RunResult,
    compare,
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
    standard_error,
)
from src.evaluation.run_eval import check_gold_exists, run
from src.evaluation.compare_hybrid import evaluate as evaluate_hybrid
from src.evaluation.compare_law_top3 import context_rule_activations, law_questions
from src.retrieval.retriever import BM25Retriever


def _chunk(chunk_id: str, article_id: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "metadata": {"article_id": article_id},
    }


def _result(qid: str, hit: bool) -> QuestionResult:
    return QuestionResult(
        qid=qid,
        question="",
        gold_articles=["gold"],
        retrieved_articles=["gold"] if hit else ["other"],
        hit=hit,
        rr=1.0 if hit else 0.0,
        recall=1.0 if hit else 0.0,
    )


class _FixedRetriever:
    def __init__(self, chunk_ids: list[str]) -> None:
        self.chunk_ids = chunk_ids

    def search(self, query, k, where=None, expand_weight=0.0):
        return [(chunk_id, 1.0) for chunk_id in self.chunk_ids[:k]]


class MetricTests(unittest.TestCase):
    def test_ranking_metrics(self) -> None:
        retrieved = ["other", "gold-a", "gold-b"]
        gold = ["gold-a", "gold-b"]

        self.assertTrue(hit_at_k(retrieved, gold, 2))
        self.assertFalse(hit_at_k(retrieved, gold, 1))
        self.assertEqual(0.5, recall_at_k(retrieved, gold, 2))
        self.assertEqual(0.5, reciprocal_rank(retrieved, gold))

    def test_empty_metric_inputs_are_safe(self) -> None:
        self.assertEqual(0.0, recall_at_k([], [], 5))
        self.assertEqual(0.0, reciprocal_rank([], []))
        self.assertEqual(0.0, standard_error(0.0, 0))
        self.assertEqual(0.0, RunResult(run_id="empty", k=5).mrr)

    def test_compare_reports_fixed_and_broken_questions(self) -> None:
        before = RunResult(run_id="before", k=5, results=[_result("q1", False), _result("q2", True)])
        after = RunResult(run_id="after", k=5, results=[_result("q1", True), _result("q2", False)])

        diff = compare(before, after)

        self.assertEqual(["q1"], diff["fixed"])
        self.assertEqual(["q2"], diff["broken"])
        self.assertEqual(0, diff["net"])


class EvaluationHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = [
            _chunk("chunk-a", "article-a", "임차권등기명령"),
            _chunk("chunk-b", "article-b", "월차임 전환"),
        ]

    def test_check_gold_exists_reports_only_missing_articles(self) -> None:
        questions = [
            {"qid": "q1", "gold_articles": ["article-a"]},
            {"qid": "q2", "gold_articles": ["article-missing"]},
            {"qid": "q3", "gold_articles": []},
        ]

        self.assertEqual(["q2: article-missing"], check_gold_exists(questions, self.chunks))

    def test_run_scores_answerable_questions_only(self) -> None:
        questions = [
            {"qid": "q1", "question": "임차권등기명령", "gold_articles": ["article-a"]},
            {"qid": "q2", "question": "판단해 줘", "gold_articles": []},
        ]

        result = run(
            questions,
            self.chunks,
            BM25Retriever(self.chunks),
            run_id="test",
            k=1,
        )

        self.assertEqual(1, result.n)
        self.assertEqual(1.0, result.hit_rate)
        self.assertEqual([], result.failures)

    def test_hybrid_report_distinguishes_hit_at_3_from_recall_at_3(self) -> None:
        chunks = [
            _chunk("chunk-gold-a", "gold-a", "첫 번째 정답"),
            _chunk("chunk-other-a", "other-a", "기타"),
            _chunk("chunk-other-b", "other-b", "기타"),
            _chunk("chunk-gold-b", "gold-b", "두 번째 정답"),
        ]
        questions = [{
            "qid": "q-multi",
            "question": "복수 근거 질문",
            "gold_articles": ["gold-a", "gold-b"],
        }]

        result = evaluate_hybrid(
            _FixedRetriever(["chunk-gold-a", "chunk-other-a", "chunk-other-b", "chunk-gold-b"]),
            chunks,
            questions,
        )

        self.assertEqual(1.0, result["hit@3"])
        self.assertEqual(0.5, result["recall@3"])
        self.assertEqual(1.0, result["recall@5"])
        self.assertEqual(["q-multi"], result["partial@3"])

    def test_law_top3_denominator_excludes_non_law_gold(self) -> None:
        chunks = [
            {
                **_chunk("chunk-law", "law-a", "법령"),
                "metadata": {"article_id": "law-a", "doc_type": "law"},
            },
            {
                **_chunk("chunk-guide", "guide-a", "안내"),
                "metadata": {"article_id": "guide-a", "doc_type": "guide"},
            },
        ]
        questions = [
            {"qid": "mixed", "gold_articles": ["law-a", "guide-a"]},
            {"qid": "guide-only", "gold_articles": ["guide-a"]},
        ]

        filtered = law_questions(questions, chunks)

        self.assertEqual(["mixed"], [question["qid"] for question in filtered])
        self.assertEqual(["law-a"], filtered[0]["gold_articles"])

    def test_context_rule_activation_diagnostic_compares_with_shared_expansion(self) -> None:
        questions = [
            {"qid": "effect", "question": "확정일자가 없으면 효력이 어떻게 되나요?"},
            {"qid": "procedure", "question": "확정일자는 어디서 받나요?"},
        ]

        self.assertEqual(["effect"], context_rule_activations(questions))


if __name__ == "__main__":
    unittest.main()
