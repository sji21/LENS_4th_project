import unittest

from src.document_check.session_retrieval import SessionDocumentEvidence
from src.generation.citation import (
    audit_citations,
    extract_citation_mentions,
    validate_citations,
)
from src.generation.models import Answer
from src.retrieval.service import Evidence


def make_evidence(
    *,
    chunk_id: str,
    doc_type: str,
    citation: str,
    text: str,
) -> Evidence:
    return Evidence(
        rank=1,
        chunk_id=chunk_id,
        doc_type=doc_type,
        citation=citation,
        text=text,
        score=1.0,
        source_url="https://example.com",
    )


class CitationTests(unittest.TestCase):
    @staticmethod
    def document_evidence(text: str) -> SessionDocumentEvidence:
        return SessionDocumentEvidence(
            chunk_id="session:registry:page:1",
            filename="registry.pdf",
            page_number=1,
            extraction_method="tesseract",
            checksum="checksum",
            text=text,
            document_id="registry-a",
            document_kind="등기사항증명서",
        )

    def test_supports_retrieved_law(self):
        ev = make_evidence(
            chunk_id="law-3",
            doc_type="law",
            citation="주택임대차보호법 제3조(대항력 등)",
            text=(
                "[주택임대차보호법 제3조(대항력 등)]\n"
                "임대차는 그 등기가 없는 경우에도..."
            ),
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제3조제1항에 따르면 대항력이 발생합니다.",
            laws=(ev,),
        )

        audit = audit_citations(answer)

        self.assertTrue(audit.is_valid)
        self.assertEqual(audit.mentions[0].evidence_chunk_ids, ("law-3",))

    def test_does_not_confuse_article_prefix(self):
        ev = make_evidence(
            chunk_id="law-3-3",
            doc_type="law",
            citation="주택임대차보호법 제3조의3(임차권등기명령)",
            text="[주택임대차보호법 제3조의3(임차권등기명령)]\n임대차가 끝난 후...",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제3조에 따르면 대항력이 발생합니다.",
            laws=(ev,),
        )

        self.assertFalse(validate_citations(answer))

    def test_accepts_same_law_bare_cross_reference(self):
        ev = make_evidence(
            chunk_id="law-3-3",
            doc_type="law",
            citation="주택임대차보호법 제3조의3(임차권등기명령)",
            text=(
                "[주택임대차보호법 제3조의3(임차권등기명령)]\n"
                "임차인은 제3조에 따른 대항력을 상실하지 아니한다."
            ),
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제3조와 관련된 대항력이 유지됩니다.",
            laws=(ev,),
        )

        self.assertTrue(validate_citations(answer))

    def test_does_not_mix_different_law_and_article(self):
        ev = make_evidence(
            chunk_id="law-main",
            doc_type="law",
            citation="주택임대차보호법 제3조",
            text=(
                "[주택임대차보호법 제3조]\n"
                "이 조문은 민법 제123조를 참조할 수 있다."
            ),
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제123조에 따르면 그렇습니다.",
            laws=(ev,),
        )

        self.assertFalse(validate_citations(answer))

    def test_supports_explicit_cross_referenced_law(self):
        ev = make_evidence(
            chunk_id="law-main",
            doc_type="law",
            citation="주택임대차보호법 제3조",
            text=(
                "[주택임대차보호법 제3조]\n"
                "이 조문은 민법 제575조를 준용한다."
            ),
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="민법 제575조를 함께 준용합니다.",
            laws=(ev,),
        )

        self.assertTrue(validate_citations(answer))

    def test_supports_law_name_with_space(self):
        ev = make_evidence(
            chunk_id="commercial-5",
            doc_type="law",
            citation="상가건물 임대차보호법 제5조",
            text="[상가건물 임대차보호법 제5조]\n대항력에 관한 내용",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="상가건물 임대차보호법 제5조에 따르면 그렇습니다.",
            laws=(ev,),
        )

        self.assertTrue(validate_citations(answer))

    def test_supports_decree(self):
        ev = make_evidence(
            chunk_id="decree-10",
            doc_type="decree",
            citation="주택임대차보호법 시행령 제10조",
            text="[주택임대차보호법 시행령 제10조]\n서울특별시: 5천500만원",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="주택임대차보호법 시행령 제10조에 따르면 그렇습니다.",
            laws=(ev,),
        )

        self.assertTrue(validate_citations(answer))

    def test_rejects_unretrieved_law(self):
        ev = make_evidence(
            chunk_id="law-3",
            doc_type="law",
            citation="주택임대차보호법 제3조",
            text="[주택임대차보호법 제3조]\n본문",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="민법 제123조에 따르면 그렇습니다.",
            laws=(ev,),
        )

        self.assertFalse(validate_citations(answer))

    def test_supports_retrieved_case(self):
        ev = make_evidence(
            chunk_id="case-1",
            doc_type="case",
            citation="대법원 2011다49523 배당이의",
            text="대법원 2011다49523 판결 내용",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="대법원 2011다49523 판결에서는 이와 같이 보았습니다.",
            cases=(ev,),
        )

        self.assertTrue(validate_citations(answer))

    def test_case_with_name_and_e_dareum_is_not_misclassified_as_guide(self):
        ev = make_evidence(
            chunk_id="case-1",
            doc_type="case",
            citation="대법원 2011다49523 배당이의",
            text="대법원 2011다49523 판결 내용",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="대법원 2011다49523 배당이의 판결에 따르면 그렇습니다.",
            cases=(ev,),
        )

        audit = audit_citations(answer)

        self.assertTrue(audit.is_valid)
        self.assertEqual([(mention.kind, mention.text) for mention in audit.mentions], [
            ("case", "대법원 2011다49523"),
        ])

    def test_court_without_case_number_is_not_a_guide_citation(self):
        ev = make_evidence(
            chunk_id="case-1",
            doc_type="case",
            citation="대법원 2011다49523 배당이의",
            text="대법원 2011다49523 판결 내용",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="대법원 판례에 따르면 그렇습니다.",
            cases=(ev,),
        )

        audit = audit_citations(answer)

        self.assertTrue(audit.missing_required)
        self.assertEqual(audit.unsupported, ())

    def test_supports_case_number_without_court_in_answer(self):
        ev = make_evidence(
            chunk_id="case-1",
            doc_type="case",
            citation="대법원 2011다49523 배당이의",
            text="판결 내용",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="2011다49523 판결에서는 이와 같이 보았습니다.",
            cases=(ev,),
        )

        self.assertTrue(validate_citations(answer))

    def test_rejects_wrong_court_with_same_case_number(self):
        ev = make_evidence(
            chunk_id="case-1",
            doc_type="case",
            citation="대법원 2011다49523 배당이의",
            text="판결 내용",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="서울중앙지방법원 2011다49523 판결에서는 그렇습니다.",
            cases=(ev,),
        )

        self.assertFalse(validate_citations(answer))

    def test_rejects_unretrieved_case(self):
        ev = make_evidence(
            chunk_id="case-1",
            doc_type="case",
            citation="대법원 2011다49523 배당이의",
            text="판결 내용",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="대법원 2020다12345 판결에서는 그렇게 판단했습니다.",
            cases=(ev,),
        )

        self.assertFalse(validate_citations(answer))

    def test_supports_hug_alias(self):
        ev = make_evidence(
            chunk_id="guide-hug",
            doc_type="guide",
            citation="HUG 전세보증금반환보증 상품안내",
            text="전세보증금반환보증 상품 개요",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="주택도시보증공사 안내에 따르면 해당 보증을 이용할 수 있습니다.",
            guides=(ev,),
        )

        self.assertTrue(validate_citations(answer))

    def test_supports_national_tax_service_guide(self):
        ev = make_evidence(
            chunk_id="guide-tax",
            doc_type="guide",
            citation="국세청 미납국세 등 열람신청 안내",
            text="임대인의 미납국세 열람 안내",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="국세청 안내에 따르면 열람을 신청할 수 있습니다.",
            guides=(ev,),
        )

        self.assertTrue(validate_citations(answer))

    def test_supports_generic_official_guide_agency(self):
        ev = make_evidence(
            chunk_id="guide-molit",
            doc_type="guide",
            citation="국토교통부 주택임대차 신고 안내",
            text="신고 절차 안내",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="국토교통부 안내에 따르면 신고 절차를 확인할 수 있습니다.",
            guides=(ev,),
        )

        self.assertTrue(validate_citations(answer))

    def test_rejects_unretrieved_guide(self):
        ev = make_evidence(
            chunk_id="guide-hug",
            doc_type="guide",
            citation="HUG 전세보증금반환보증 상품안내",
            text="상품 안내",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="국토교통부 안내에 따르면 가능합니다.",
            guides=(ev,),
        )

        self.assertFalse(validate_citations(answer))

    def test_requires_named_source_for_answered_result(self):
        ev = make_evidence(
            chunk_id="law-3",
            doc_type="law",
            citation="주택임대차보호법 제3조",
            text="[주택임대차보호법 제3조]\n본문",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="대항력은 다음 날부터 발생합니다.",
            laws=(ev,),
        )

        audit = audit_citations(answer)

        self.assertTrue(audit.missing_required)
        self.assertFalse(audit.is_valid)

    def test_uses_raw_text_not_final_text(self):
        ev = make_evidence(
            chunk_id="law-3",
            doc_type="law",
            citation="주택임대차보호법 제3조",
            text="[주택임대차보호법 제3조]\n본문",
        )
        answer = Answer(
            question="질문",
            status="answered",
            raw_text="대항력은 다음 날부터 발생합니다.",
            text="대항력은 다음 날부터 발생합니다.\n\n주택임대차보호법 제3조",
            laws=(ev,),
        )

        audit = audit_citations(answer)

        self.assertEqual(audit.mentions, ())
        self.assertTrue(audit.missing_required)

    def test_deduplicates_repeated_citation(self):
        ev = make_evidence(
            chunk_id="law-3",
            doc_type="law",
            citation="주택임대차보호법 제3조",
            text="[주택임대차보호법 제3조]\n본문",
        )

        mentions = extract_citation_mentions(
            (
                "주택임대차보호법 제3조에 따르면 그렇습니다. "
                "다시 주택임대차보호법 제3조를 확인할 수 있습니다."
            ),
            (ev,),
        )

        self.assertEqual(len(mentions), 1)

    def test_non_answered_result_does_not_require_citation(self):
        answer = Answer(
            question="질문",
            status="abstained",
            text="답변을 생성하지 못했습니다.",
            raw_text="",
        )

        self.assertTrue(validate_citations(answer))

    def test_accepts_court_case_text_copied_from_uploaded_registry(self):
        evidence = self.document_evidence(
            "서울중앙지방법원 2024카단12345 가압류 결정에 따른 가압류 등기"
        )
        answer = Answer(
            question="첨부한 등본 검토해줘",
            status="answered",
            text="",
            raw_text="registry.pdf 1쪽에 서울중앙지방법원 2024카단12345 가압류 결정이 표시되어 있습니다.",
            document_evidences=(evidence,),
            requires_official_citation=False,
        )

        audit = audit_citations(answer)

        self.assertTrue(audit.is_valid)
        self.assertEqual(
            audit.mentions[0].evidence_chunk_ids,
            ("session:registry:page:1",),
        )

    def test_rejects_case_citation_absent_from_uploaded_registry(self):
        evidence = self.document_evidence("갑구에 가압류 등기가 표시되어 있습니다.")
        answer = Answer(
            question="첨부한 등본 검토해줘",
            status="answered",
            text="",
            raw_text="대법원 2021다12345 판결에 따르면 주의가 필요합니다.",
            document_evidences=(evidence,),
            requires_official_citation=False,
        )

        self.assertFalse(validate_citations(answer))


if __name__ == "__main__":
    unittest.main()
