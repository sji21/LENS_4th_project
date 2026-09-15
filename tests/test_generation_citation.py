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
    def test_provider_material_variants_require_retrieved_source(self):
        guide = make_evidence(chunk_id="tax", doc_type="guide", citation="국세청 안내", text="안내")
        for verb in ("제공", "발간", "배포", "발표", "게시"):
            for ending in ("한", "하는", " 중인", "하고 있는"):
                for particle in ("에서", "에서는", "이", ""):
                    for supplied in (False, True):
                        with self.subTest(verb=verb, ending=ending, particle=particle, supplied=supplied):
                            raw = f"국세청{particle} {verb}{ending} 자료에 따르면 가능합니다."
                            mentions = [m for m in extract_citation_mentions(raw, (guide,) if supplied else ())
                                        if m.kind == "guide"]
                            self.assertEqual([m.text for m in mentions], ["국세청"])
                            self.assertEqual(mentions[0].supported, supplied)

    def test_material_receipt_is_not_attribution(self):
        for material in ("안내", "안내서", "안내문", "안내자료", "자료", "자료집", "가이드북"):
            for action in ("를 받으세요", "를 수령하세요", "를 다운로드하세요", "를 요청하세요"):
                with self.subTest(material=material, action=action):
                    raw = f"국세청에서 제공하는 {material}{action}."
                    self.assertFalse([m for m in extract_citation_mentions(raw, ()) if m.kind == "guide"])

    def test_material_attribution_survives_later_action(self):
        for material in ("안내서", "안내문", "안내자료", "자료집", "가이드북", "「미납국세 안내서」"):
            with self.subTest(material=material):
                raw = f"국세청에서 제공 중인 {material}에 따르면 지원금을 받을 수 있습니다."
                mentions = [m for m in extract_citation_mentions(raw, ()) if m.kind == "guide"]
                self.assertEqual([m.text for m in mentions], ["국세청"])
                self.assertFalse(mentions[0].supported)

    def test_agency_alias_requires_whole_name_in_evidence(self):
        for name in ("국세청장", "가짜국세청", "HUGPLUS"):
            with self.subTest(name=name):
                guide = make_evidence(chunk_id="other", doc_type="guide", citation=f"{name} 안내", text="안내")
                raw = "국세청 안내에 따르면 가능합니다. HUG 안내에 따르면 가능합니다."
                mentions = [m for m in extract_citation_mentions(raw, (guide,)) if m.kind == "guide"]
                self.assertEqual(len(mentions), 2)
                self.assertTrue(all(not m.supported for m in mentions))

    def test_agency_procedures_are_not_source_citations(self):
        law = make_evidence(chunk_id="law-14", doc_type="law",
                            citation="주택임대차보호법 제14조", text="분쟁조정위원회")
        for agency in ("조정위원회", "심의위원회", "분쟁조정위원회", "국세청", "HUG", "**조정위원**회"):
            for tail in ("에 신청해야 하며, 조정 절차 및 효력 등에 대한 **안내**를 받을 수 있습니다.",
                         "로부터 안내를 받을 수 있습니다.", "에서 안내를 받을 수 있습니다."):
                with self.subTest(agency=agency, tail=tail):
                    raw = "주택임대차보호법 제14조에 따라 조정을 신청할 수 있습니다. " + agency + tail
                    answer = Answer(question="조정", status="answered", text="", raw_text=raw, laws=(law,))
                    audit = audit_citations(answer)
                    self.assertTrue(audit.is_valid)
                    self.assertFalse([m for m in audit.mentions if m.kind == "guide"])

    def test_committee_sources_need_exact_retrieved_agency(self):
        law = make_evidence(chunk_id="law-14", doc_type="law",
                            citation="주택임대차보호법 제14조", text="분쟁조정위원회")
        guide = make_evidence(chunk_id="committee", doc_type="guide",
                              citation="분쟁조정위원회 안내", text="조정 안내")
        other = make_evidence(chunk_id="other", doc_type="guide",
                              citation="분쟁조정위원 안내", text="다른 출처")
        for phrase in ("분쟁조정위원회 안내에 따르면 가능합니다.",
                       "분쟁조정위원회 주택임대차 분쟁조정 안내에 따르면 가능합니다.",
                       "분쟁조정위원회에서 제공한 자료에 따르면 가능합니다.",
                       "분쟁조정위원회에 따르면 가능합니다.",
                       "분쟁조정위원회에따르면 가능합니다.",
                       "**분쟁조정위원**회 의 **자료**에 따르면 가능합니다."):
            for guides, expected in (((), False), ((other,), False), ((guide,), True)):
                with self.subTest(phrase=phrase, guides=guides):
                    raw = "주택임대차보호법 제14조에 따른 절차입니다. " + phrase
                    audit = audit_citations(Answer(question="조정", status="answered", text="", raw_text=raw,
                                                   laws=(law,), guides=guides))
                    mentions = [m for m in audit.mentions if m.kind == "guide"]
                    self.assertEqual(len(mentions), 1)
                    self.assertEqual("".join(mentions[0].text.split()), "분쟁조정위원회")
                    self.assertEqual(audit.is_valid, expected)

    def test_present_tense_provider_clause_is_still_a_material_citation(self):
        """"제공하는"처럼 현재형 관형사형도 "제공한"과 동일하게 자료 인용으로 본다.

        "에서 제공하는 안내에 따르면"에서 "하는"을 벗겨내지 못하면, 뒤에 남은
        "에서"가 절차 설명으로 오인되어 실제 자료 인용이 검사 대상에서 통째로
        빠지는 결과가 된다(검색 근거 없는 인용이 그대로 통과할 위험).
        """
        law = make_evidence(chunk_id="law-14", doc_type="law",
                            citation="주택임대차보호법 제14조", text="국세청")
        guide = make_evidence(chunk_id="tax-guide", doc_type="guide",
                              citation="국세청 미납국세 열람 안내", text="열람 안내")
        for verb_phrase in ("제공하는", "제공한", "발간하는", "배포하는", "발표하는", "게시하는"):
            for particle in ("에서", "이", ""):
                with self.subTest(verb_phrase=verb_phrase, particle=particle):
                    raw = f"국세청{particle} {verb_phrase} 안내에 따르면 열람할 수 있습니다."
                    answer = Answer(question="열람", status="answered", text="", raw_text=raw,
                                    laws=(law,), guides=(guide,))
                    audit = audit_citations(answer)
                    guides_found = [m for m in audit.mentions if m.kind == "guide"]
                    self.assertEqual([m.text for m in guides_found], ["국세청"])
                    self.assertTrue(guides_found[0].supported)
                    self.assertTrue(audit.is_valid)

    def test_procedure_does_not_hide_next_agency_citation(self):
        mentions = extract_citation_mentions(
            "조정위원회에 신청하고 국세청 안내에 따르면 가능합니다.", ())
        guides = [m for m in mentions if m.kind == "guide"]
        self.assertEqual([m.text for m in guides], ["국세청"])
        self.assertFalse(guides[0].supported)

    def test_passive_voice_provider_clause_is_still_a_material_citation(self):
        """"제공된/발간된" 같은 피동형도 "제공한"과 동일하게 자료 인용으로 본다.

        피동형 어미를 벗겨내지 못하면 뒤에 남은 "에서"가 절차 설명으로 오인되어
        실제 자료 인용이 검사 대상에서 통째로 빠진다(검색 근거 없는 인용이
        그대로 통과할 위험).
        """
        guide = make_evidence(chunk_id="tax-guide", doc_type="guide",
                              citation="국세청 안내자료", text="안내")
        for verb in ("제공", "발간", "배포", "발표", "게시"):
            for ending in ("된", "되는", "되고 있는", "되어 있는", "돼 있는"):
                for particle in ("에서", "에서는", "이", ""):
                    for supplied in (False, True):
                        with self.subTest(verb=verb, ending=ending, particle=particle, supplied=supplied):
                            raw = f"국세청{particle} {verb}{ending} 자료에 따르면 가능합니다."
                            mentions = [m for m in extract_citation_mentions(raw, (guide,) if supplied else ())
                                        if m.kind == "guide"]
                            self.assertEqual([m.text for m in mentions], ["국세청"])
                            self.assertEqual(mentions[0].supported, supplied)

    def test_joint_attribution_separators_and_partial_evidence(self):
        law = make_evidence(chunk_id="law", doc_type="law", citation="주택임대차보호법 제14조", text="조정")
        tax = make_evidence(chunk_id="tax", doc_type="guide", citation="국세청 안내", text="안내")
        hug = make_evidence(chunk_id="hug", doc_type="guide", citation="HUG 안내", text="안내")
        for separator in ("과 ", " 및 ", ", ", ", 그리고 ", ", 및 ", "·", " · "):
            for guides in ((), (tax,), (hug,), (tax, hug)):
                with self.subTest(separator=separator, guides=guides):
                    raw = f"주택임대차보호법 제14조에 따른 절차입니다. 국세청{separator}HUG의 안내에 따르면 가능합니다."
                    audit = audit_citations(Answer(question="q", status="answered", text="", raw_text=raw,
                                                   laws=(law,), guides=guides))
                    self.assertEqual([m.text for m in audit.mentions if m.kind == "guide"], ["국세청", "HUG"])
                    self.assertEqual(audit.is_valid, len(guides) == 2)

    def test_publication_passive_attribution_and_receipt(self):
        law = make_evidence(chunk_id="law", doc_type="law", citation="주택임대차보호법 제14조", text="조정")
        for phrase in ("발행된", "발행되어 있는", "제공되어 있는", "제공돼 있는"):
            for tail, expected in (("안내서에 따르면 가능합니다", False), ("안내서를 받으세요", True)):
                with self.subTest(phrase=phrase, tail=tail):
                    raw = f"주택임대차보호법 제14조에 따른 절차입니다. 국세청에서 {phrase} {tail}."
                    audit = audit_citations(Answer(question="q", status="answered", text="", raw_text=raw, laws=(law,)))
                    self.assertEqual(audit.is_valid, expected)

    def test_joint_procedure_and_separate_source_stay_separate(self):
        for raw in ("국세청·HUG에서 안내서를 받으세요.", "국세청, 그리고 HUG에서 안내를 받을 수 있습니다."):
            with self.subTest(raw=raw):
                self.assertFalse([m for m in extract_citation_mentions(raw, ()) if m.kind == "guide"])
        mentions = extract_citation_mentions("국세청에 신청하고, HUG 안내에 따르면 가능합니다.", ())
        self.assertEqual([m.text for m in mentions if m.kind == "guide"], ["HUG"])
        mentions = extract_citation_mentions("국세청·HUG 및 국토교통부 안내에 따르면 가능합니다.", ())
        self.assertEqual([m.text for m in mentions if m.kind == "guide"], ["국세청", "HUG", "국토교통부"])

    def test_joint_agency_attribution_requires_every_participant_supported(self):
        """"국세청과 HUG의 안내에 따르면"처럼 접속사로 묶인 공동 인용은, 언급된
        기관 전원이 각자 검색 근거를 가져야 한다. 마지막 기관 뒤의 서술부만 보고
        tail을 자르면 앞선 기관은 접속사(과/와)만 남아 절차 설명으로 오인되어
        인용 검사에서 조용히 누락되고, 그 결과 근거 없는 기관 인용도 감사를
        통과할 수 있다.
        """
        hug = make_evidence(chunk_id="hug-guide", doc_type="guide",
                            citation="HUG 안내자료", text="안내")
        nts = make_evidence(chunk_id="nts-guide", doc_type="guide",
                            citation="국세청 안내자료", text="안내")
        raw = "국세청과 HUG의 안내에 따르면 가능합니다."

        with self.subTest("only_hug_supplied"):
            mentions = {m.text: m.supported for m in extract_citation_mentions(raw, (hug,)) if m.kind == "guide"}
            self.assertEqual(mentions, {"국세청": False, "HUG": True})

        with self.subTest("both_supplied"):
            mentions = {m.text: m.supported for m in extract_citation_mentions(raw, (hug, nts)) if m.kind == "guide"}
            self.assertEqual(mentions, {"국세청": True, "HUG": True})

        with self.subTest("only_hug_supplied_audit_is_invalid"):
            answer = Answer(question="문의", status="answered", text="", raw_text=raw, guides=(hug,))
            audit = audit_citations(answer)
            self.assertFalse(audit.is_valid)

        with self.subTest("both_supplied_audit_is_valid"):
            answer = Answer(question="문의", status="answered", text="", raw_text=raw, guides=(hug, nts))
            audit = audit_citations(answer)
            self.assertTrue(audit.is_valid)

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

    def test_rejects_unretrieved_same_law_cross_reference(self):
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

        self.assertFalse(validate_citations(answer))

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

    def test_rejects_unretrieved_explicit_cross_referenced_law(self):
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

        self.assertFalse(validate_citations(answer))

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
