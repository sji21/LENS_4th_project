import json
import tempfile
import unittest
from pathlib import Path

from src.retrieval.retriever import (
    BM25Retriever,
    TfidfRetriever,
    chunk_to_article,
    load_chunks,
    tokenize,
)
from src.retrieval.terms import expand, expand_law


def _chunk(chunk_id: str, article_id: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "metadata": {"article_id": article_id},
    }


class TokenizeTests(unittest.TestCase):
    def test_preserves_article_reference_and_adds_bigrams(self) -> None:
        tokens = tokenize("제3조의2 확정일자")

        self.assertIn("§제3조의2", tokens)
        self.assertIn("확정", tokens)
        self.assertIn("정일", tokens)


class BM25RetrieverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = [
            _chunk("chunk-b", "article-b", "월차임 전환 산정률"),
            _chunk("chunk-a", "article-a", "임차권등기명령 신청"),
        ]

    def test_returns_most_relevant_chunk_first(self) -> None:
        retriever = BM25Retriever(self.chunks, b=0.25)

        results = retriever.search("임차권등기명령", k=2)

        self.assertEqual("chunk-a", results[0][0])
        self.assertGreater(results[0][1], 0)

    def test_respects_k_and_is_deterministic_for_ties(self) -> None:
        chunks = [
            _chunk("chunk-b", "article-b", "같은 표현"),
            _chunk("chunk-a", "article-a", "같은 표현"),
        ]
        retriever = BM25Retriever(chunks)

        self.assertEqual(["chunk-a"], [x[0] for x in retriever.search("같은 표현", k=1)])

    def test_empty_corpus_returns_no_results(self) -> None:
        self.assertEqual([], BM25Retriever([]).search("질문", k=5))

    def test_expands_confirmation_date_to_its_legal_effect(self) -> None:
        chunks = [
            _chunk("procedure", "procedure", "확정일자 부여기관 신청 절차"),
            _chunk("fees", "fees", "확정일자 부여 수수료"),
            _chunk(
                "priority",
                "priority",
                "대항요건을 갖춘 임차인은 보증금을 우선하여 변제받을 우선변제권이 있다",
            ),
        ]
        retriever = BM25Retriever(chunks, query_expander=expand_law)

        plain = [chunk_id for chunk_id, _ in retriever.search("확정일자를 안 받으면", 3)]
        expanded = [
            chunk_id
            for chunk_id, _ in retriever.search("확정일자를 안 받으면", 3, expand_weight=1.0)
        ]

        self.assertNotIn("priority", plain)
        self.assertEqual("priority", expanded[0])

    def test_confirmation_date_expansion_uses_concepts_not_article_number(self) -> None:
        terms = expand_law("확정일자를 안 받으면 어떻게 되나요?")

        self.assertEqual(["우선변제권", "우선하여 변제"], terms)
        self.assertNotIn("제3조의2", terms)

    def test_confirmation_date_procedure_question_is_not_effect_expanded(self) -> None:
        self.assertEqual([], expand_law("확정일자는 어디서 받나요?"))

    def test_confirmation_date_effect_variants_are_expanded(self) -> None:
        for question in (
            "확정일자 없이도 보증금을 먼저 받을 수 있나요?",
            "확정일자는 왜 필요한가요?",
            "확정일자의 효력은 무엇인가요?",
        ):
            with self.subTest(question=question):
                expanded = expand_law(question)
                self.assertIn("우선변제권", expanded)
                self.assertIn("우선하여 변제", expanded)

    def test_confirmation_date_procedure_variants_are_not_effect_expanded(self) -> None:
        for question in (
            "확정일자는 어디서 받나요?",
            "확정일자 신청 방법과 수수료가 궁금해요",
            "신분증 없이 확정일자를 받을 수 있나요?",
            "임대차계약서 없이 확정일자를 신청할 수 있나요?",
            "전입신고 없이 확정일자만 받으려면 어디로 가야 하나요?",
            "확정일자 없이 전입신고만 하려면 어디로 가야 하나요?",
            "신분증 효력이 없으면 확정일자를 신청할 수 있나요?",
        ):
            with self.subTest(question=question):
                expanded = expand_law(question)
                self.assertNotIn("우선변제권", expanded)
                self.assertNotIn("우선하여 변제", expanded)

    def test_law_context_terms_are_not_in_the_shared_expansion(self) -> None:
        self.assertEqual([], expand("확정일자를 안 받으면 어떻게 되나요?"))

    def test_chunk_to_article_maps_metadata(self) -> None:
        self.assertEqual(
            {"chunk-b": "article-b", "chunk-a": "article-a"},
            chunk_to_article(self.chunks),
        )

    def test_load_chunks_reads_non_empty_jsonl_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "chunks.jsonl"
            path.write_text(
                "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in self.chunks)
                + "\n\n",
                encoding="utf-8",
            )

            self.assertEqual(self.chunks, load_chunks(path))


class TfidfRetrieverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = [
            _chunk("chunk-b", "article-b", "월차임 전환 산정률"),
            _chunk("chunk-a", "article-a", "임차권등기명령 신청"),
        ]

    def test_returns_most_relevant_chunk_first(self) -> None:
        results = TfidfRetriever(self.chunks).search("월차임 전환", k=2)

        self.assertEqual("chunk-b", results[0][0])
        self.assertGreater(results[0][1], 0)

    def test_returns_no_results_for_unknown_or_empty_corpus(self) -> None:
        self.assertEqual([], TfidfRetriever(self.chunks).search("완전히없는영단어xyz", k=5))
        self.assertEqual([], TfidfRetriever([]).search("질문", k=5))

    def test_ties_are_sorted_by_chunk_id(self) -> None:
        chunks = [
            _chunk("chunk-b", "article-b", "같은 표현"),
            _chunk("chunk-a", "article-a", "같은 표현"),
        ]

        results = TfidfRetriever(chunks).search("같은 표현", k=2)

        self.assertEqual(["chunk-a", "chunk-b"], [item[0] for item in results])


if __name__ == "__main__":
    unittest.main()
