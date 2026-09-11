"""표시 전용 부가 기능: 용어 풀이 · 근거 하이라이트 · 후속 질문 · 쉬운 말 재서술.

네 기능 모두 새 검색이나 새 근거 없이 이미 확정된 답변 위에서만 동작해야 한다.
"""
import re
import unittest

from src.generation.evidence_highlight import build_citation_spans
from src.generation.followup import clean_citation_for_question, suggest_followups
from src.generation.glossary import GLOSSARY, find_glossary_spans
from src.generation.llm import get_llm
from src.generation.simplify import simplify_answer
from src.retrieval.service import Evidence


def evidence(citation, text, chunk_id="c1", doc_type="law", url="https://law.go.kr/x"):
    return Evidence(rank=1, chunk_id=chunk_id, doc_type=doc_type, citation=citation,
                    text=text, score=1.0, source_url=url)


class GlossaryTests(unittest.TestCase):
    def test_finds_term_with_position_and_definition(self):
        text = "대항력은 전입신고 다음 날 생깁니다."
        spans = find_glossary_spans(text)
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["term"], "대항력")
        self.assertEqual(text[spans[0]["start"]:spans[0]["end"]], "대항력")
        self.assertEqual(spans[0]["definition"], GLOSSARY["대항력"])

    def test_longer_term_wins_over_contained_term(self):
        spans = find_glossary_spans("최우선변제권이 적용됩니다.")
        self.assertEqual([s["term"] for s in spans], ["최우선변제권"])

    def test_repeated_term_is_marked_once(self):
        spans = find_glossary_spans("확정일자와 확정일자 부여 절차")
        self.assertEqual(len(spans), 1)

    def test_definitions_do_not_cite_article_numbers(self):
        """개념 설명은 조문 번호에 기대지 않는다. 법이 개정돼도 사전은 그대로다."""

        article = re.compile(r"제\s*\d+\s*조")
        for term, definition in GLOSSARY.items():
            with self.subTest(term=term):
                self.assertIsNone(article.search(definition))

    def test_empty_text_returns_nothing(self):
        self.assertEqual(find_glossary_spans(""), ())
        self.assertEqual(find_glossary_spans(None), ())


class FollowupTests(unittest.TestCase):
    def test_suggests_only_uncited_law_sources(self):
        sources = [
            {"label": "주택임대차보호법 제3조", "doc_type": "law"},
            {"label": "주택임대차보호법 제3조의2(보증금의 회수)", "doc_type": "law"},
            {"label": "대법원 2020다12345", "doc_type": "case"},
        ]
        suggestions = suggest_followups(sources, frozenset({"주택임대차보호법 제3조"}))
        self.assertEqual(suggestions, ("주택임대차보호법 제3조의2",))

    def test_deduplicates_and_respects_limit(self):
        sources = [{"label": f"주택임대차보호법 제{n}조", "doc_type": "law"} for n in (3, 4, 5, 6)]
        sources.append(dict(sources[0]))
        self.assertEqual(len(suggest_followups(sources, frozenset(), limit=3)), 3)

    def test_clean_citation_strips_trailing_title_only(self):
        self.assertEqual(clean_citation_for_question("주택임대차보호법 제3조의2(보증금의 회수)"), "주택임대차보호법 제3조의2")
        self.assertEqual(clean_citation_for_question("주택임대차보호법 제3조"), "주택임대차보호법 제3조")
        nested = "주택임대차보호법 제3조(대항력 등(특례))"
        self.assertEqual(clean_citation_for_question(nested), nested)
        self.assertEqual(clean_citation_for_question(""), "")


class EvidenceHighlightTests(unittest.TestCase):
    def test_links_citation_to_matching_evidence_with_excerpt(self):
        law = evidence("주택임대차보호법 제3조", "① 임차인은 주택의 인도와 주민등록을 마친 때에는 그 다음 날부터 제삼자에 대하여 효력이 생긴다.")
        text = "주택임대차보호법 제3조에 따라 전입신고 다음 날부터 효력이 생깁니다."
        spans = build_citation_spans(text, (law,))
        self.assertEqual(len(spans), 1)
        self.assertEqual(text[spans[0]["start"]:spans[0]["end"]], "주택임대차보호법 제3조")
        self.assertEqual(spans[0]["chunk_id"], "c1")
        self.assertIn("다음 날", spans[0]["excerpt"])

    def test_act_is_not_matched_to_its_enforcement_decree(self):
        decree = evidence("주택임대차보호법 시행령 제3조", "시행령 본문", chunk_id="decree", url="https://decree")
        act = evidence("주택임대차보호법 제3조", "본법 본문", chunk_id="act", url="https://act")
        spans = build_citation_spans("주택임대차보호법 제3조에 따라", (decree, act))
        self.assertEqual([s["chunk_id"] for s in spans], ["act"])

    def test_bare_article_links_only_when_unambiguous(self):
        one = evidence("주택임대차보호법 제3조의2", "우선변제권 본문", chunk_id="one")
        spans = build_citation_spans("제3조의2에서 따로 정합니다.", (one,))
        self.assertEqual([s["chunk_id"] for s in spans], ["one"])

        other = evidence("상가건물 임대차보호법 제3조의2", "다른 법 본문", chunk_id="two")
        self.assertEqual(build_citation_spans("제3조의2에서 따로 정합니다.", (one, other)), ())

    def test_spans_never_overlap(self):
        law = evidence("주택임대차보호법 제3조", "본문")
        spans = build_citation_spans("주택임대차보호법 제3조와 제3조 모두", (law,))
        for earlier, later in zip(spans, spans[1:]):
            self.assertLessEqual(earlier["end"], later["start"])

    def test_no_evidence_means_no_spans(self):
        self.assertEqual(build_citation_spans("주택임대차보호법 제3조", ()), ())
        self.assertEqual(build_citation_spans("", (evidence("주택임대차보호법 제3조", "본문"),)), ())


class SimplifyTests(unittest.TestCase):
    def test_blank_input_never_calls_the_model(self):
        self.assertEqual(simplify_answer("   "), "")
        self.assertEqual(simplify_answer(None), "")

    def test_returns_cleaned_model_text(self):
        llm = get_llm(fake_responses=["<think>메모</think>쉬운 말로 다시 쓴 문장입니다.", "PASS"])
        self.assertEqual(simplify_answer("어려운 원문", llm=llm), "쉬운 말로 다시 쓴 문장입니다.")

    def test_rejects_changed_numbers(self):
        with self.assertRaises(ValueError):
            simplify_answer("3개월입니다.", llm=get_llm(fake_responses=["2개월입니다."]))

    def test_rejects_reversed_meaning(self):
        with self.assertRaises(ValueError):
            simplify_answer("임차인이 부담하지 않습니다.", llm=get_llm(fake_responses=["임차인이 부담합니다.", "FAIL"]))


if __name__ == "__main__":
    unittest.main()
