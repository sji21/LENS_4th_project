"""PATCH-021 프롬프트 테스트.

프롬프트 문구를 통째로 비교하지 않는다. 그렇게 하면 표현을 다듬을 때마다
테스트가 깨져서 결국 지워진다. 대신 **깨지면 안 되는 안전 규칙이 프롬프트에
남아 있는지**만 확인한다.
"""

from __future__ import annotations

import unittest

from src.generation import prompt as prompt_module
from src.retrieval.service import Evidence, RetrievalResult


def evidence(rank: int, citation: str, text: str, doc_type: str = "law") -> Evidence:
    return Evidence(
        rank=rank,
        chunk_id=f"{doc_type}-{rank}",
        doc_type=doc_type,
        citation=citation,
        text=text,
        score=1.0,
        source_url="https://law.go.kr/x",
    )


class SystemPromptTests(unittest.TestCase):
    def test_document_prompt_uses_only_uploaded_evidence(self) -> None:
        text = prompt_module.SYSTEM_DOCUMENT_QA

        self.assertIn("업로드 문서", text)
        self.assertIn("법령명이나 판례를 인용하지", text)
        self.assertIn("안전하다거나 위험하다고 최종 판정하지", text)

    def test_forbids_answering_outside_evidence(self) -> None:
        text = prompt_module.SYSTEM_QA

        self.assertIn("지어내지", text)
        self.assertIn("확인할 수 없습니다", text)

    def test_defines_role_and_target_user(self) -> None:
        text = prompt_module.SYSTEM_QA

        self.assertIn("대한민국 주택임대차 법령 안내 도우미", text)
        self.assertIn("법률 전문가가 아닌", text)
        self.assertIn("예비 세입자", text)
        self.assertIn("현재 세입자", text)
        self.assertIn("짧은 일상어 풀이", text)

    def test_uses_only_directly_relevant_evidence(self) -> None:
        self.assertIn("질문과 직접 관련 없는", prompt_module.SYSTEM_QA)

    def test_accuracy_has_priority_over_plain_language(self) -> None:
        text = prompt_module.SYSTEM_QA

        self.assertIn("정확성과 쉬운 표현이 충돌하면", text)
        self.assertIn("항상 정확성을 우선", text)

    def test_plain_language_must_preserve_legal_meaning(self) -> None:
        text = prompt_module.SYSTEM_QA

        for phrase in ("결론", "조건", "예외", "부정 표현", "주체", "시점", "숫자의 역할"):
            self.assertIn(phrase, text)

    def test_legal_term_is_kept_and_glossed(self) -> None:
        text = prompt_module.SYSTEM_QA

        self.assertIn("용어 자체를 없애거나 다른 말로 바꾸지 말고", text)
        self.assertIn("대항력(", text)

    def test_forbids_stronger_conclusion_or_new_advice(self) -> None:
        text = prompt_module.SYSTEM_QA

        self.assertIn("뒷받침하는 수준보다 강한 결론", text)
        self.assertIn("조언·사례·위험도 판단", text)

    def test_preserves_numeric_meaning_and_role(self) -> None:
        text = prompt_module.SYSTEM_QA

        self.assertIn("대상·조건·역할", text)
        self.assertIn("임차인의 보증금 범위", text)
        self.assertIn("우선변제받는 보증금 중 일정액", text)

    def test_requires_named_citations_not_numbers(self) -> None:
        """번호 인용을 시키면 안 된다.

        검색 결과의 `[1]` 은 묶음 안 순번이라 법령에도 판례에도 1번이 있다.
        번호로 인용하게 하면 그 인용이 어느 쪽을 가리키는지 알 수 없다.
        """
        text = prompt_module.SYSTEM_QA

        self.assertIn("번호가 아니라 이름으로", text)
        self.assertIn("최소 1개 반드시", text)
        self.assertIn("법원과 사건번호", text)

    def test_requires_verbatim_dates_and_amounts(self) -> None:
        """기간을 요약하면 하루 차이로 권리 순위가 바뀐다.

        실제로 Qwen3 가 "그 다음 날부터"를 "날부터"로 줄여 결론을 틀리게 쓴
        일이 있었다. 그 사례를 프롬프트에 예시로 박아 두었고, 이 테스트는
        그 예시가 지워지지 않았는지 지킨다.
        """
        system_text = prompt_module.SYSTEM_QA
        final_check = prompt_module.HUMAN_QA

        self.assertIn("글자 그대로", system_text)
        self.assertIn("첫 문장", system_text)

        # ★ "그 다음 날부터" 만 확인하면 안 된다. 그 문자열은 참고 자료 예시 줄에도
        #   있어서, 정작 지켜야 할 ✓/✗ 대조 예시를 지워도 테스트가 통과한다.
        #   (변이 검사에서 실제로 통과해 버리는 것을 확인하고 고쳤다.)
        #   작은 모델은 추상적 지시보다 이 대조 예시에 훨씬 잘 반응하므로
        #   두 줄이 함께 살아 있어야 한다.
        self.assertIn('✓ "마친 그 다음 날부터 효력이 생깁니다"', final_check)
        self.assertIn('✗ "마친 날부터 효력이 생깁니다"', final_check)

    def test_conclusion_must_not_paraphrase_periods(self) -> None:
        """'결론을 먼저 요약하라'와 '그대로 옮기라'가 부딪히면 안 된다.

        형식 지침이 뒤에 있어 더 강하게 먹히므로, 형식 쪽에서도 같은 규칙을
        다시 못 박아 둔다.
        """
        self.assertIn("결론 문장에서도", prompt_module.SYSTEM_QA)

    def test_forbids_self_contradiction(self) -> None:
        self.assertIn("표현이 달라지면 안 됩니다", prompt_module.SYSTEM_QA)

    def test_forbids_fabricated_direct_quotes(self) -> None:
        """조문 번호는 맞는데 따옴표 안 내용이 딴 조문인 경우가 실제로 있었다.

        (Qwen3 가 제3조의3 제8항이라며 전혀 다른 문장을 따옴표로 인용했다.)
        조문 번호만 대조하는 citation.py 로는 잡히지 않는 종류라 프롬프트가
        1차 방어선이다.
        """
        text = prompt_module.SYSTEM_QA

        self.assertIn("따옴표로 인용할 때는", text)
        self.assertIn("그대로 적혀 있는 문장만", text)

    def test_forbids_merging_amount_brackets(self) -> None:
        # 서울(5,500만원)을 과밀억제권역(4,800만원) 구간에 합쳐 넣은 사례가 있었다.
        self.assertIn("과밀억제권역", prompt_module.SYSTEM_QA)
        self.assertIn("항목을 합치거나", prompt_module.SYSTEM_QA)

    def test_forbids_writing_urls(self) -> None:
        """출처 링크는 코드가 붙인다.

        모델이 URL을 쓰다가 토큰 상한에 걸려 주소 한가운데서 끊긴 적이 있다.
        """
        self.assertIn("URL이나 링크를 쓰지 마십시오", prompt_module.SYSTEM_QA)

    def test_keeps_answers_concise_without_forcing_truncation(self) -> None:
        """길이는 제어하되 정확한 조건·예외를 글자 수 때문에 버리게 하면 안 된다."""
        text = prompt_module.SYSTEM_QA

        self.assertIn("결론·조건·예외·근거를 중심으로 간결하게", text)
        self.assertIn("같은 내용의 반복", text)
        self.assertIn("3~5문장 정도를 권장", text)
        self.assertIn("중요한 내용을 생략하거나 줄이지 마십시오", text)
        self.assertIn("줄글", text)
        self.assertNotIn("400자를 넘기지 마십시오", text)

    def test_separates_law_and_case_weight(self) -> None:
        self.assertIn("같은 무게로 쓰지 마십시오", prompt_module.SYSTEM_QA)

    def test_forbids_verdict_on_user_contract(self) -> None:
        # LENS의 기본 원칙. 이 줄이 사라지면 서비스 성격 자체가 바뀐다.
        self.assertIn("판정하지 마십시오", prompt_module.SYSTEM_QA)
        self.assertIn("판정하지 않습니다", prompt_module.NON_VERDICT_NOTICE)

    def test_no_think_switch_goes_in_the_user_turn(self) -> None:
        """시스템 프롬프트에 두면 무시된다.

        실제로 시스템 쪽에 두었을 때 Qwen3 가 사고 과정에 토큰을 다 써서
        답변이 통째로 비는 일이 있었다. Qwen3 문서가 정한 자리는 사용자 턴이다.
        """
        self.assertNotIn("/no_think", prompt_module.system_prompt())
        self.assertTrue(prompt_module.human_prompt().endswith("/no_think"))


class BuildQaPromptTests(unittest.TestCase):
    def test_takes_context_and_question(self) -> None:
        template = prompt_module.build_qa_prompt()

        self.assertEqual({"context", "question"}, set(template.input_variables))

    def test_renders_both_values(self) -> None:
        rendered = prompt_module.build_qa_prompt().format(
            context="[1] 근거 본문", question="대항력은 언제 생기나요?"
        )

        self.assertIn("[1] 근거 본문", rendered)
        self.assertIn("대항력은 언제 생기나요?", rendered)


class FormatContextTests(unittest.TestCase):
    def test_empty_result_says_no_data(self) -> None:
        result = RetrievalResult(question="아무 질문")

        self.assertIn("검색된 자료가 없습니다", prompt_module.format_context(result))

    def test_keeps_law_and_case_sections_apart(self) -> None:
        result = RetrievalResult(
            question="집주인이 바뀌면?",
            laws=[evidence(1, "주택임대차보호법 제3조", "[주택임대차보호법 제3조(대항력 등)] 본문")],
            cases=[evidence(1, "대법원 2011다49523", "양수인이 지위를 승계한다", "case")],
        )

        context = prompt_module.format_context(result)

        self.assertIn("## 관련 법령", context)
        self.assertIn("## 관련 판례", context)
        self.assertLess(context.index("## 관련 법령"), context.index("## 관련 판례"))

    def test_source_name_block_does_not_rebuild_retrieval_body(self) -> None:
        # 출처명 블록에는 한 번, Retrieval 원문 헤더에는 한 번 나타나는 것이 의도다.
        # 본문 자체를 Generation 쪽에서 다시 조립해 중복시키지는 않는다.
        result = RetrievalResult(
            question="q",
            laws=[evidence(1, "주택임대차보호법 제3조(대항력 등)", "[주택임대차보호법 제3조(대항력 등)] 본문")],
        )

        context = prompt_module.format_context(result)

        self.assertEqual(2, context.count("주택임대차보호법 제3조(대항력 등)"))
        self.assertEqual(1, context.count("[주택임대차보호법 제3조(대항력 등)] 본문"))




class FinalOutputGuardrailTests(unittest.TestCase):
    """dev-017/dev-023에서 실제로 빠졌던 제약을 사용자 턴 끝에 다시 둔다."""

    def test_user_turn_repeats_no_fabricated_time_or_number_rule(self) -> None:
        text = prompt_module.HUMAN_QA

        self.assertIn("숫자·연도·날짜·기간·금액은 절대 추가하지", text)
        self.assertIn("특정 연도 같은 시점 표현을 임의로 만들지", text)
        # 특정 실패 연도 자체를 프롬프트에 박아 모델을 유도하지 않는다.
        self.assertNotIn("2023", text)

    def test_user_turn_repeats_named_source_requirement(self) -> None:
        text = prompt_module.HUMAN_QA

        self.assertIn("첫 문장 또는 두 번째 문장", text)
        self.assertIn("실제로 사용한 출처명을 최소 1개 그대로", text)
        self.assertIn("어느 기관의 안내인지 반드시", text)


class CopyableSourceNameTests(unittest.TestCase):
    def test_dev017_context_exposes_nts_source_name(self) -> None:
        result = RetrievalResult(
            question="계약 전에 집주인이 세금을 안 낸 게 있는지 확인할 수 있나요?",
            laws=[
                evidence(
                    1,
                    "주택임대차보호법 제3조의7",
                    "[주택임대차보호법 제3조의7] 본문",
                )
            ],
            guides=[
                evidence(
                    1,
                    "국세청(미납국세열람)",
                    "[국세청(미납국세열람)] 안내 본문",
                    "guide",
                )
            ],
        )

        context = prompt_module.format_context(result)

        self.assertIn("[답변에 쓸 출처명]", context)
        self.assertIn("관련 법령: 주택임대차보호법 제3조의7", context)
        self.assertIn("관련 기관 안내: 국세청 안내", context)

    def test_dev023_context_exposes_hug_source_name(self) -> None:
        result = RetrievalResult(
            question="전세보증금반환보증은 어떤 제도인가요?",
            guides=[
                evidence(
                    1,
                    "주택도시보증공사(전세보증금반환보증)",
                    "[주택도시보증공사(전세보증금반환보증)] 안내 본문",
                    "guide",
                )
            ],
        )

        context = prompt_module.format_context(result)

        self.assertIn("[답변에 쓸 출처명]", context)
        self.assertIn("관련 기관 안내: 주택도시보증공사 안내", context)
        self.assertIn("전세보증금반환보증", context)

    def test_source_name_block_keeps_law_case_and_guide_separate(self) -> None:
        result = RetrievalResult(
            question="q",
            laws=[evidence(1, "주택임대차보호법 제3조", "법령 본문")],
            cases=[evidence(1, "대법원 2011다49523", "판례 본문", "case")],
            guides=[evidence(1, "HUG(전세보증금반환보증)", "안내 본문", "guide")],
        )

        context = prompt_module.format_context(result)

        self.assertIn("관련 법령: 주택임대차보호법 제3조", context)
        self.assertIn("관련 판례: 대법원 2011다49523", context)
        self.assertIn("관련 기관 안내: 주택도시보증공사 안내", context)


class ThinkSwitchLiveReadTests(unittest.TestCase):
    """사고 과정 스위치가 프롬프트와 서버 요청에서 같이 움직이는가.

    import 시점에 값을 복사해 두면, llm.THINK_OFF 가 나중에 바뀌었을 때
    서버에는 think:false 가 가는데 프롬프트에는 /no_think 가 안 붙는다.
    두 겹으로 막으려던 것이 한 겹만 남는다.
    """

    def test_human_prompt_follows_llm_module_at_call_time(self) -> None:
        from src.generation import llm as llm_module

        original = llm_module.THINK_OFF
        try:
            llm_module.THINK_OFF = True
            self.assertTrue(prompt_module.human_prompt().endswith("/no_think"))
            self.assertIs(False, llm_module._extra_body()["think"])

            llm_module.THINK_OFF = False
            self.assertFalse(prompt_module.human_prompt().endswith("/no_think"))
            self.assertNotIn("think", llm_module._extra_body())
        finally:
            llm_module.THINK_OFF = original


class GuideContextTests(unittest.TestCase):
    """세 번째 묶음(기관 안내)이 프롬프트까지 실려 가는가.

    검색이 안내를 별도 묶음으로 주기 시작했다. format_context 가 그것을 떨어뜨리면
    모델은 안내를 못 보는데 Answer 에는 출처로 남아, 화면과 답변이 어긋난다.
    """

    def _result(self):
        guide = Evidence(
            rank=1,
            chunk_id="guide1",
            doc_type="guide",
            citation="주택도시보증공사(전세보증금반환보증)",
            text="[주택도시보증공사(전세보증금반환보증)] 보증기관이 보증금을 대신 지급합니다.",
            score=1.0,
            source_url="https://example.kr/guide",
        )
        return RetrievalResult(question="전세보증금반환보증은 어떤 제도인가요?", guides=[guide])

    def test_guide_section_reaches_the_prompt(self) -> None:
        context = prompt_module.format_context(self._result())

        self.assertIn("보증기관이 보증금을 대신 지급합니다", context)
        # 법적 근거가 아니라는 표시가 함께 가야 한다(검색 쪽 GUIDE_HEADER).
        self.assertIn("법적 근거가 아닌", context)

    def test_guide_only_result_is_not_treated_as_empty(self) -> None:
        self.assertNotEqual("검색된 자료가 없습니다.", prompt_module.format_context(self._result()))


class GuideCitationRuleTests(unittest.TestCase):
    """안내를 인용할 형식이 프롬프트에 정의돼 있는가.

    6번 규칙에 안내가 없으면 모델이 기관명을 못 적고, 형식 규칙이 "법령명과 조문
    번호만" 이라고 못 박으면 검색 쪽 GUIDE_HEADER 의 "어느 기관인지 밝혀 주세요"
    와 정면으로 충돌한다. 실제로 그 상태였다.
    """

    def test_guide_has_a_citation_format(self) -> None:
        # ★ "기관 안내" 만 찾으면 8번 규칙에도 그 말이 있어 6번을 지워도 통과한다.
        #   인용 형식 줄 자체를 대조한다.
        self.assertIn(
            "- 기관 안내: `주택도시보증공사 안내` 처럼 자료를 낸 기관 이름",
            prompt_module.SYSTEM_QA,
        )

    def test_format_section_does_not_contradict_rule_six(self) -> None:
        self.assertNotIn("법령명과\n  조문 번호만 적으면 됩니다", prompt_module.SYSTEM_QA)

    def test_guide_is_not_to_be_quoted_as_law(self) -> None:
        self.assertIn("법에 따르면", prompt_module.SYSTEM_QA)

if __name__ == "__main__":
    unittest.main()
