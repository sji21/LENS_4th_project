import unittest

from src.generation.models import Answer
from src.generation.validation import (
    SemanticJudgement,
    _extract_values,
    audit_answer,
    ground_answer_conditions,
    requires_semantic_validation,
    validate_answer,
)
from src.retrieval.service import Evidence


def evidence(
    chunk_id: str,
    citation: str,
    text: str,
    doc_type: str = "law",
) -> Evidence:
    return Evidence(
        rank=1,
        chunk_id=chunk_id,
        doc_type=doc_type,
        citation=citation,
        text=text,
        score=1.0,
        source_url="",
    )


def issue_kinds(answer: Answer, semantic_judge=None) -> set[str]:
    return {
        issue.kind
        for issue in audit_answer(answer, semantic_judge=semantic_judge).issues
    }


class ValidationTests(unittest.TestCase):
    def test_long_registry_identifier_is_not_treated_as_money(self):
        text = "문서관리번호 23660070500023666102000001000202"

        self.assertEqual((), _extract_values(text))

    def test_accepts_grounded_period(self):
        ev = evidence(
            "law-6-2",
            "주택임대차보호법 제6조의2",
            (
                "[주택임대차보호법 제6조의2]\n"
                "임대인이 통지를 받은 날부터 3개월이 지나면 효력이 발생한다."
            ),
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text=(
                "주택임대차보호법 제6조의2에 따르면 "
                "3개월이 지나면 효력이 발생합니다."
            ),
            laws=(ev,),
        )

        self.assertTrue(validate_answer(answer))

    def test_rejects_unsupported_period(self):
        ev = evidence(
            "law-6-2",
            "주택임대차보호법 제6조의2",
            "[주택임대차보호법 제6조의2]\n통지를 받은 날부터 3개월이 지나면...",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text=(
                "주택임대차보호법 제6조의2에 따르면 "
                "30일이 지나면 효력이 발생합니다."
            ),
            laws=(ev,),
        )

        self.assertIn("value", issue_kinds(answer))

    def test_rejects_same_day_when_evidence_says_next_day(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "주민등록을 마친 때에는 그 다음 날부터 제삼자에 대하여 효력이 생긴다.",
        )
        answer = Answer(
            question="대항력은 언제 생기나요?",
            status="answered",
            text="",
            raw_text=(
                "주택임대차보호법 제3조에 따라 주민등록을 마친 날부터 "
                "제삼자에 대해 효력이 생깁니다."
            ),
            laws=(ev,),
        )

        self.assertIn("condition", issue_kinds(answer))

    def test_rejects_registration_requirement_when_evidence_says_without_it(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "임대차는 그 등기(登記)가 없는 경우에도 주택의 인도와 주민등록을 마치면 효력이 생긴다.",
        )
        answer = Answer(
            question="등기를 안 한 계약도 보호되나요?",
            status="answered",
            text="",
            raw_text=(
                "주택임대차보호법 제3조에 따르면 대항력이나 우선변제권은 "
                "주택임대차등기를 통해 확보해야 합니다."
            ),
            laws=(ev,),
        )

        self.assertIn("condition", issue_kinds(answer))

    def test_accepts_correct_next_day_and_no_registration_requirement(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            (
                "임대차는 그 등기가 없는 경우에도 주택의 인도와 주민등록을 "
                "마친 때에는 그 다음 날부터 효력이 생긴다."
            ),
        )
        answer = Answer(
            question="등기를 안 한 계약도 보호되나요?",
            status="answered",
            text="",
            raw_text=(
                "주택임대차보호법 제3조에 따르면 등기 없이도 주택의 인도와 "
                "주민등록을 마친 그 다음 날부터 효력이 생깁니다."
            ),
            laws=(ev,),
        )

        self.assertNotIn("condition", issue_kinds(answer))

    def test_grounds_only_registration_effect_timing(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "주민등록을 마친 때에는 그 다음 날부터 제삼자에 대하여 효력이 생긴다.",
        )
        raw = (
            "주민등록을 마친 날부터 대항력이 생깁니다. "
            "임대차 신고는 계약을 마친 날부터 처리합니다."
        )

        grounded = ground_answer_conditions(raw, (ev,))

        self.assertIn(
            "주민등록을 마친 그 다음 날부터 대항력이 생깁니다", grounded
        )
        self.assertIn("계약을 마친 날부터 처리합니다", grounded)

    def test_accepts_equivalent_percent_expression(self):
        ev = evidence(
            "decree-9",
            "주택임대차보호법 시행령 제9조",
            (
                "[주택임대차보호법 시행령 제9조]\n"
                "대통령령으로 정하는 비율은 연 1할을 말한다."
            ),
            "decree",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text=(
                "주택임대차보호법 시행령 제9조에 따르면 "
                "연 10%입니다."
            ),
            laws=(ev,),
        )

        self.assertTrue(validate_answer(answer))

    def test_accepts_equivalent_money_notation(self):
        ev = evidence(
            "decree-11",
            "주택임대차보호법 시행령 제11조",
            "[주택임대차보호법 시행령 제11조]\n서울특별시: 1억6천500만원",
            "decree",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text=(
                "주택임대차보호법 시행령 제11조에 따르면 "
                "서울의 기준 금액은 165,000,000원입니다."
            ),
            laws=(ev,),
        )

        self.assertTrue(validate_answer(answer))

    def test_accepts_date_present_in_evidence_citation(self):
        ev = evidence(
            "case-1",
            "대법원 2011다49523 배당이의 (2013-01-17 선고)",
            "대법원 2011다49523 판결 내용",
            "case",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="대법원 2011다49523은 2013-01-17 선고된 판결입니다.",
            cases=(ev,),
        )

        self.assertTrue(validate_answer(answer))

    def test_detects_priority_payment_role_mixup(self):
        payout = evidence(
            "decree-10",
            "주택임대차보호법 시행령 제10조",
            (
                "[주택임대차보호법 시행령 제10조(보증금 중 일정액의 범위 등)]\n"
                "법 제8조에 따라 우선변제를 받을 보증금 중 일정액의 범위는 다음과 같다.\n"
                "서울특별시: 5천500만원"
            ),
            "decree",
        )
        eligibility = evidence(
            "decree-11",
            "주택임대차보호법 시행령 제11조",
            (
                "[주택임대차보호법 시행령 제11조(우선변제를 받을 임차인의 범위)]\n"
                "우선변제를 받을 임차인은 보증금이 다음 금액 이하인 임차인으로 한다.\n"
                "서울특별시: 1억6천500만원"
            ),
            "decree",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text=(
                "주택임대차보호법 시행령 제11조에 따르면 "
                "서울에서는 1억6천500만원까지 최우선변제를 받을 수 있습니다."
            ),
            laws=(payout, eligibility),
        )

        self.assertIn("amount_role", issue_kinds(answer))

    def test_detects_priority_payment_role_mixup_in_list_header(self):
        payout = evidence(
            "decree-10",
            "주택임대차보호법 시행령 제10조",
            (
                "[주택임대차보호법 시행령 제10조(보증금 중 일정액의 범위 등)]\n"
                "법 제8조에 따라 우선변제를 받을 보증금 중 일정액의 범위는 다음과 같다.\n"
                "서울특별시: 5천500만원"
            ),
            "decree",
        )
        eligibility = evidence(
            "decree-11",
            "주택임대차보호법 시행령 제11조",
            (
                "[주택임대차보호법 시행령 제11조(우선변제를 받을 임차인의 범위)]\n"
                "우선변제를 받을 임차인은 보증금이 다음 금액 이하인 임차인으로 한다.\n"
                "서울특별시: 1억6천500만원"
            ),
            "decree",
        )
        answer = Answer(
            question="소액임차인 최우선변제 금액은 지역별로 얼마인가요?",
            status="answered",
            text="",
            raw_text=(
                "소액임차인 최우선변제 금액은 지역에 따라 다음과 같습니다.\n"
                "1. 서울특별시: 1억6천500만원\n"
                "주택임대차보호법 시행령 제11조 참조."
            ),
            laws=(payout, eligibility),
        )

        self.assertIn("amount_role", issue_kinds(answer))

    def test_accepts_correct_priority_payment_roles(self):
        payout = evidence(
            "decree-10",
            "주택임대차보호법 시행령 제10조",
            (
                "[주택임대차보호법 시행령 제10조]\n"
                "우선변제를 받을 보증금 중 일정액의 범위.\n"
                "서울특별시: 5천500만원"
            ),
            "decree",
        )
        eligibility = evidence(
            "decree-11",
            "주택임대차보호법 시행령 제11조",
            (
                "[주택임대차보호법 시행령 제11조]\n"
                "우선변제를 받을 임차인은 보증금이 다음 금액 이하인 임차인으로 한다.\n"
                "서울특별시: 1억6천500만원"
            ),
            "decree",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text=(
                "주택임대차보호법 시행령 제11조에 따르면 "
                "서울은 보증금 1억6천500만원 이하인 임차인이 대상입니다. "
                "주택임대차보호법 시행령 제10조에 따르면 "
                "최우선변제액은 5천500만원 이하입니다."
            ),
            laws=(payout, eligibility),
        )

        self.assertNotIn("amount_role", issue_kinds(answer))
        self.assertTrue(validate_answer(answer))

    def test_detects_fabricated_direct_quote(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            (
                "[주택임대차보호법 제3조]\n"
                "주민등록을 마친 때에는 그 다음 날부터 제삼자에 대하여 효력이 생긴다."
            ),
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text=(
                '주택임대차보호법 제3조는 '
                '"주민등록을 마친 당일부터 효력이 생긴다"라고 규정합니다.'
            ),
            laws=(ev,),
        )

        self.assertIn("quote", issue_kinds(answer))

    def test_accepts_exact_direct_quote(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "[주택임대차보호법 제3조]\n그 다음 날부터 제삼자에 대하여 효력이 생긴다.",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text=(
                '주택임대차보호법 제3조는 '
                '"그 다음 날부터 제삼자에 대하여 효력이 생긴다"라고 규정합니다.'
            ),
            laws=(ev,),
        )

        self.assertNotIn("quote", issue_kinds(answer))

    def test_ordinary_quoted_term_is_not_treated_as_statutory_quote(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "[주택임대차보호법 제3조]\n대항력에 관한 내용",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text=(
                '여기서 "계약 전 확인사항"이라는 표현은 일반적인 설명입니다. '
                "주택임대차보호법 제3조를 확인할 수 있습니다."
            ),
            laws=(ev,),
        )

        self.assertNotIn("quote", issue_kinds(answer))

    def test_detects_nonexistent_paragraph(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            (
                "[주택임대차보호법 제3조]\n"
                "① 첫째 항\n② 둘째 항\n③ 셋째 항\n④ 넷째 항\n⑤ 다섯째 항\n⑥ 여섯째 항"
            ),
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제3조제7항에 따르면 그렇습니다.",
            laws=(ev,),
        )

        self.assertIn("paragraph", issue_kinds(answer))

    def test_accepts_existing_paragraph(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "[주택임대차보호법 제3조]\n① 임차인이 주택의 인도와 주민등록을 마친 때에는...",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제3조제1항에 따르면 그렇습니다.",
            laws=(ev,),
        )

        self.assertTrue(validate_answer(answer))

    def test_surfaces_unsupported_citation(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "[주택임대차보호법 제3조]\n본문",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="민법 제999조에 따르면 그렇습니다.",
            laws=(ev,),
        )

        self.assertIn("citation", issue_kinds(answer))

    def test_blocks_known_contract_safety_verdict_failure(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "[주택임대차보호법 제3조]\n대항력 관련 내용",
        )
        answer = Answer(
            question="이 집 계약해도 안전할까요?",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제3조를 보면 이 계약은 안전합니다.",
            laws=(ev,),
        )

        self.assertIn("safety_verdict", issue_kinds(answer))

    def test_semantic_judge_runs_after_deterministic_checks_pass(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "[주택임대차보호법 제3조]\n대항력에 관한 내용",
        )
        answer = Answer(
            question="대항력은 언제 생기나요?",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제3조에 따르면 대항력 요건을 확인할 수 있습니다.",
            laws=(ev,),
        )
        calls = []

        def judge(question, raw_text, evidences):
            calls.append((question, raw_text, evidences))
            return SemanticJudgement(
                supported=False,
                detail="질문의 시점에 직접 답하지 않았습니다.",
            )

        report = audit_answer(answer, semantic_judge=judge)

        self.assertIn("semantic", {issue.kind for issue in report.issues})
        self.assertEqual(len(calls), 1)

    def test_semantic_judge_can_accept_answer(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "[주택임대차보호법 제3조]\n대항력에 관한 내용",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제3조에 따르면 그렇습니다.",
            laws=(ev,),
        )

        self.assertTrue(
            validate_answer(
                answer,
                semantic_judge=lambda *_: True,
            )
        )

    def test_semantic_judge_is_skipped_when_deterministic_check_fails(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "[주택임대차보호법 제3조]\n본문",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="민법 제999조에 따르면 그렇습니다.",
            laws=(ev,),
        )

        def must_not_run(*_):
            raise AssertionError("semantic judge should not run")

        report = audit_answer(answer, semantic_judge=must_not_run)

        self.assertIn("citation", {issue.kind for issue in report.issues})
        self.assertNotIn("semantic", {issue.kind for issue in report.issues})

    def test_semantic_judge_failure_fails_closed_when_enabled(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "[주택임대차보호법 제3조]\n본문",
        )
        answer = Answer(
            question="질문",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제3조에 따르면 그렇습니다.",
            laws=(ev,),
        )

        def broken_judge(*_):
            raise RuntimeError("judge unavailable")

        self.assertFalse(validate_answer(answer, semantic_judge=broken_judge))
        self.assertIn("semantic", issue_kinds(answer, semantic_judge=broken_judge))

    def test_non_answered_result_is_valid(self):
        answer = Answer(
            question="질문",
            status="abstained",
            text="답변을 생성하지 못했습니다.",
            raw_text="",
        )

        self.assertTrue(validate_answer(answer))


class ConditionalSemanticValidationTests(unittest.TestCase):
    def test_simple_single_law_explanation_uses_deterministic_validation(self):
        ev = evidence(
            "law-3",
            "주택임대차보호법 제3조",
            "대항력은 제3자에게 임차권을 주장할 수 있는 효력이다.",
        )
        answer = Answer(
            question="대항력이 무엇인가요?",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제3조에 따르면 대항력은 임차권을 주장할 수 있는 효력입니다.",
            laws=(ev,),
        )

        self.assertFalse(requires_semantic_validation(answer))

    def test_period_value_requires_semantic_validation(self):
        ev = evidence("law-6", "주택임대차보호법 제6조", "기간은 2년이다.")
        answer = Answer(
            question="기간은 얼마인가요?",
            status="answered",
            text="",
            raw_text="주택임대차보호법 제6조에 따르면 기간은 2년입니다.",
            laws=(ev,),
        )

        self.assertTrue(requires_semantic_validation(answer))

    def test_case_or_guide_requires_semantic_validation(self):
        case = evidence("case-1", "대법원 2020다12345", "판례", "case")
        guide = evidence("guide-1", "국세청 안내", "안내", "guide")

        self.assertTrue(
            requires_semantic_validation(
                Answer("q", "answered", "", raw_text="대법원 2020다12345 판결입니다.", cases=(case,))
            )
        )
        self.assertTrue(
            requires_semantic_validation(
                Answer("q", "answered", "", raw_text="국세청 안내에 따릅니다.", guides=(guide,))
            )
        )

    def test_condition_or_exception_requires_semantic_validation(self):
        ev = evidence("law-3", "주택임대차보호법 제3조", "본문")
        answer = Answer(
            "q",
            "answered",
            "",
            raw_text="주택임대차보호법 제3조에 따르면 다만 예외가 있습니다.",
            laws=(ev,),
        )

        self.assertTrue(requires_semantic_validation(answer))

    def test_legal_action_or_effect_question_requires_semantic_validation(self):
        ev = evidence("law-3-3", "주택임대차보호법 제3조의3", "본문")
        for question in (
            "임차권등기명령은 언제 신청할 수 있나요?",
            "비용은 누가 부담하나요?",
            "특약의 효력이 있나요?",
        ):
            answer = Answer(
                question,
                "answered",
                "",
                raw_text="주택임대차보호법 제3조의3에 따른 설명입니다.",
                laws=(ev,),
            )
            with self.subTest(question=question):
                self.assertTrue(requires_semantic_validation(answer))


if __name__ == "__main__":
    unittest.main()
