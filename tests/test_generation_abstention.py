import json
from pathlib import Path
import unittest

from src.generation.abstention import (
    SCOPE_JUDGE_SYSTEM,
    build_scope_judge_prompt,
    classify_scope,
    is_out_of_scope,
)


class AbstentionTests(unittest.TestCase):
    def test_refuses_direct_contract_safety_verdict(self):
        decision = classify_scope("이 집 계약해도 안전할까요?")

        self.assertTrue(decision.out_of_scope)
        self.assertEqual(decision.reason, "contract_safety_verdict")
        self.assertEqual(decision.source, "deterministic")

    def test_refuses_market_price_lookup(self):
        decision = classify_scope("이 아파트 현재 시세가 얼마인가요?")

        self.assertTrue(decision.out_of_scope)
        self.assertEqual(decision.reason, "market_price_lookup")

    def test_refuses_jeonse_price_lookup(self):
        decision = classify_scope("이 아파트 전세가 얼마인가요?")

        self.assertTrue(decision.out_of_scope)
        self.assertEqual(decision.reason, "market_price_lookup")

    def test_allows_deposit_return_timing_after_jeonse_ends(self):
        decision = classify_scope("전세가 끝난 후 보증금은 얼마 만에 돌려받나요?")

        self.assertFalse(decision.out_of_scope)
        self.assertEqual(decision.reason, "in_scope")

    def test_temporal_word_plus_jeonse_subject_is_not_market_price_lookup(self):
        questions = (
            "지금 전세가 끝나가는데 보증금을 못 받고 있어요",
            "요즘 전세가 잘 안 나가서 걱정이에요",
            "지금 전세가 계약갱신 거절당했어요 어떻게 하나요",
            "현재 전세가 만료를 앞두고 있는데 임차권등기명령 신청 방법 알려주세요",
        )

        for question in questions:
            with self.subTest(question=question):
                decision = classify_scope(question)
                self.assertFalse(decision.out_of_scope)
                self.assertNotEqual(decision.reason, "market_price_lookup")

    def test_temporal_jeonse_price_question_still_refuses(self):
        decision = classify_scope("지금 이 아파트 전세가 어느 정도인가요?")

        self.assertTrue(decision.out_of_scope)
        self.assertEqual(decision.reason, "market_price_lookup")


    def test_market_term_in_legal_condition_is_not_price_lookup(self):
        decision = classify_scope(
            "현재 전세가격이 보증금보다 낮으면 우선변제는 어떻게 되나요?"
        )

        self.assertFalse(decision.out_of_scope)
        self.assertEqual(decision.reason, "in_scope")

    def test_market_description_question_still_refuses(self):
        decision = classify_scope("현재 이 아파트 시세가 어떻게 되나요?")

        self.assertTrue(decision.out_of_scope)
        self.assertEqual(decision.reason, "market_price_lookup")

    def test_broad_non_rental_terms_require_semantic_review(self):
        questions = (
            "주식 세금은 어떻게 내나요?",
            "근로계약서 계약기간은 어떻게 정하나요?",
            "경매로 산 자동차 소유권은 언제 생기나요?",
        )

        for question in questions:
            calls = []

            def judge(value):
                calls.append(value)
                return True

            with self.subTest(question=question):
                initial = classify_scope(question)
                self.assertTrue(initial.needs_semantic_review)

                decision = classify_scope(question, semantic_judge=judge)
                self.assertTrue(decision.out_of_scope)
                self.assertEqual(decision.reason, "semantic_out_of_scope")
                self.assertEqual(calls, [question])

    def test_allows_informational_risk_question(self):
        self.assertFalse(
            is_out_of_scope("전세계약 전에 어떤 위험 요소를 확인해야 하나요?")
        )

    def test_allows_legal_question_containing_safe_word(self):
        self.assertFalse(
            is_out_of_scope("안전한 계약을 위해 확정일자는 언제 받아야 하나요?")
        )

    def test_ambiguous_property_verdict_is_not_keyword_refused_without_judge(self):
        decision = classify_scope("이 집 괜찮을까요?")

        self.assertFalse(decision.out_of_scope)
        self.assertTrue(decision.needs_semantic_review)
        self.assertEqual(decision.source, "default_allow")

    def test_semantic_judge_skips_clear_rental_question(self):
        calls = []

        def judge(question):
            calls.append(question)
            return False

        decision = classify_scope(
            "대항력은 언제부터 생기나요?",
            semantic_judge=judge,
        )

        self.assertFalse(decision.out_of_scope)
        self.assertEqual(decision.source, "deterministic")
        self.assertFalse(decision.needs_semantic_review)
        self.assertEqual(calls, [])

    def test_uploaded_registry_analysis_is_clearly_in_scope(self):
        calls = []

        decision = classify_scope(
            "업로드한 등기부 등본 분석해줘",
            semantic_judge=lambda question: calls.append(question) or True,
        )

        self.assertFalse(decision.out_of_scope)
        self.assertEqual(decision.source, "deterministic")
        self.assertFalse(decision.needs_semantic_review)
        self.assertEqual(calls, [])

    def test_semantic_judge_can_refuse_unrelated_domain(self):
        decision = classify_scope(
            "내일 서울 날씨 알려줘",
            semantic_judge=lambda _: True,
        )

        self.assertTrue(decision.out_of_scope)
        self.assertEqual(decision.reason, "semantic_out_of_scope")
        self.assertEqual(decision.source, "semantic_judge")

    def test_hard_refusal_skips_semantic_judge(self):
        def must_not_run(_):
            raise AssertionError("semantic judge should not run")

        decision = classify_scope(
            "이 집 계약해도 안전할까요?",
            semantic_judge=must_not_run,
        )

        self.assertTrue(decision.out_of_scope)
        self.assertEqual(decision.source, "deterministic")

    def test_semantic_judge_failure_defaults_to_allow(self):
        def broken_judge(_):
            raise RuntimeError("judge unavailable")

        decision = classify_scope(
            "이 집 괜찮을까요?",
            semantic_judge=broken_judge,
        )

        self.assertFalse(decision.out_of_scope)
        self.assertTrue(decision.needs_semantic_review)
        self.assertEqual(decision.source, "default_allow")

    def test_scope_judge_prompt_keeps_typo_tolerant_policy(self):
        prompt = build_scope_judge_prompt("대항력언제생겨요?")

        self.assertIn("대항력언제생겨요?", prompt)
        self.assertIn("오탈자", SCOPE_JUDGE_SYSTEM)
        self.assertIn("ALLOW", SCOPE_JUDGE_SYSTEM)
        self.assertIn("REFUSE", SCOPE_JUDGE_SYSTEM)

    def test_dev_scope_regression(self):
        dev_path = Path("data/eval/dev.jsonl")
        if not dev_path.exists():
            self.skipTest("data/eval/dev.jsonl is not available")

        false_refusals = []
        missed_refusals = []

        with dev_path.open(encoding="utf-8") as file:
            for line in file:
                row = json.loads(line)
                actual = is_out_of_scope(row["question"])
                expected = row["answer_type"] == "out_of_scope"

                if actual and not expected:
                    false_refusals.append(row["qid"])
                if expected and not actual:
                    missed_refusals.append(row["qid"])

        self.assertEqual(false_refusals, [])
        self.assertEqual(missed_refusals, [])


if __name__ == "__main__":
    unittest.main()
