"""PATCH-021 Ollama LLM 연결 테스트.

실제 Ollama 를 부르지 않는다. 확인하려는 것은 접속 설정이 환경 변수대로 만들어
지는지, 그리고 Qwen3 의 사고 과정이 답변에서 제거되는지다.
"""

from __future__ import annotations

import unittest

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.generation import llm as llm_module


class GetLlmTests(unittest.TestCase):
    def test_fake_responses_returns_fake_model(self) -> None:
        model = llm_module.get_llm(fake_responses=["안녕하세요"])

        self.assertIsInstance(model, FakeListChatModel)
        self.assertEqual("안녕하세요", model.invoke("아무 질문").content)

    def test_real_model_points_at_configured_ollama(self) -> None:
        model = llm_module.get_llm()

        # 로컬 기본값과 RunPod 환경변수 모두 같은 native Ollama 경로를 사용한다.
        self.assertIn("11434", model.openai_api_base)
        self.assertEqual(llm_module._native_base_url(), model.openai_api_base)
        self.assertEqual(llm_module.LLM_MODEL, model.model_name)

    def test_max_tokens_caps_runaway_answers(self) -> None:
        """프롬프트의 길이 지시만으로는 막히지 않았다.

        dev 8문항 중 3문항이 1,700~2,300자로 발산했고 오류가 전부 거기 몰렸다.
        토큰 상한은 프롬프트와 달리 모델이 무시할 수 없는 하드 제한이다.
        """
        # ★ max_tokens= 로 주면 langchain-openai 1.x 가 max_completion_tokens 로
        #   바꿔 보내고, Ollama 는 그 이름을 몰라 상한을 조용히 무시한다.
        #   extra_body 로 보내야 이름이 그대로 간다. 실제 전송 내용으로 확인했다.
        body = llm_module.get_llm().extra_body
        self.assertEqual(llm_module.LLM_MAX_TOKENS, body["max_tokens"])
        self.assertLessEqual(llm_module.LLM_MAX_TOKENS, 2048)

    def test_thinking_is_disabled_on_the_server_side_too(self) -> None:
        """프롬프트의 /no_think 만으로는 무시되는 경우가 있었다.

        사고 과정이 토큰 예산을 다 쓰면 답변이 통째로 비어 버린다.
        """
        self.assertTrue(llm_module.THINK_OFF)
        self.assertIs(False, llm_module.get_llm().extra_body["think"])

    def test_overrides_apply(self) -> None:
        model = llm_module.get_llm(temperature=0.0)

        self.assertEqual(0.0, model.temperature)

    def test_native_request_sets_runpod_compatible_user_agent(self) -> None:
        """RunPod 프록시는 urllib 기본 User-Agent 요청을 403으로 거부한다."""
        from unittest import mock

        class FakeResponse:
            def __init__(self, payload: bytes):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self) -> bytes:
                return self.payload

        captured = []

        def fake_urlopen(request, timeout):
            captured.append(request.get_header("User-agent"))
            if request.full_url.endswith("/api/tags"):
                model = llm_module.LLM_MODEL.encode("utf-8")
                return FakeResponse(b'{"models":[{"name":"' + model + b'"}]}')
            return FakeResponse(b'{"message":{"content":"2"},"done":true}')

        llm_module._ROUTE_CACHE.clear()
        with mock.patch.object(
            llm_module.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            response = llm_module.get_llm(max_tokens=16).invoke("1+1")

        self.assertEqual("2", response.content)
        self.assertTrue(captured)
        self.assertTrue(
            all(value == llm_module.OLLAMA_USER_AGENT for value in captured)
        )

    def test_remote_failure_falls_back_to_local_ollama(self) -> None:
        from unittest import mock

        remote = "https://unavailable-pod-11434.proxy.runpod.net/v1"
        local = llm_module.LOCAL_OLLAMA_BASE_URL
        original = llm_module.LLM_BASE_URL
        requested_urls = []

        class FakeResponse:
            def __init__(self, payload: bytes):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self) -> bytes:
                return self.payload

        def fake_urlopen(request, timeout):
            requested_urls.append(request.full_url)
            if request.full_url.startswith("https://unavailable-pod"):
                raise OSError("RunPod unavailable")
            if request.full_url.endswith("/api/tags"):
                model = llm_module.LLM_MODEL.encode("utf-8")
                return FakeResponse(b'{"models":[{"name":"' + model + b'"}]}')
            return FakeResponse(
                b'{"message":{"content":"local answer"},"done":true}'
            )

        llm_module.LLM_BASE_URL = remote
        llm_module._ROUTE_CACHE.clear()
        try:
            with mock.patch.object(
                llm_module.urllib.request, "urlopen", side_effect=fake_urlopen
            ):
                response = llm_module.get_llm(max_tokens=16).invoke("test")
        finally:
            llm_module.LLM_BASE_URL = original
            llm_module._ROUTE_CACHE.clear()

        self.assertEqual("local answer", response.content)
        self.assertEqual(local, response.response_metadata["endpoint"])
        self.assertTrue(any(url.startswith("https://unavailable-pod") for url in requested_urls))
        self.assertTrue(any(url.startswith(local) for url in requested_urls))


class ProbeHeaderTests(unittest.TestCase):
    def test_probe_sets_runpod_compatible_user_agent(self) -> None:
        from unittest import mock

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self) -> bytes:
                model = llm_module.LLM_MODEL.encode("utf-8")
                return b'{"models":[{"name":"' + model + b'"}]}'

        captured = {}

        def fake_urlopen(request, timeout):
            captured["user_agent"] = request.get_header("User-agent")
            return FakeResponse()

        llm_module._ROUTE_CACHE.clear()
        with mock.patch.object(
            llm_module.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            ready, message = llm_module.probe()

        self.assertTrue(ready, message)
        self.assertEqual(llm_module.OLLAMA_USER_AGENT, captured["user_agent"])


class StripReasoningTests(unittest.TestCase):
    """Qwen3 는 `<think>...</think>` 로 혼잣말을 먼저 뱉는다.

    남겨 두면 화면에 그대로 보이고, 더 나쁜 것은 인용 검증이 사고 과정 안의
    조문 번호를 답변의 인용으로 세는 것이다. 실제로는 인용하지 않은 조문이
    검증을 통과한다.
    """

    def test_removes_think_block(self) -> None:
        raw = "<think>제3조를 봐야 하나 고민한다</think>\n대항력은 다음 날 0시부터 생깁니다."

        cleaned = llm_module.strip_reasoning(raw)

        self.assertEqual("대항력은 다음 날 0시부터 생깁니다.", cleaned)
        self.assertNotIn("제3조", cleaned)

    def test_removes_unclosed_think_block(self) -> None:
        # 길이 제한으로 닫는 태그 없이 잘린 경우.
        raw = "답변 앞부분입니다.\n<think>여기서 잘렸다"

        self.assertEqual("답변 앞부분입니다.", llm_module.strip_reasoning(raw))

    def test_keeps_text_without_think_block(self) -> None:
        raw = "주택임대차보호법 제3조에 따르면 대항력이 생깁니다."

        self.assertEqual(raw, llm_module.strip_reasoning(raw))

    def test_empty_input(self) -> None:
        self.assertEqual("", llm_module.strip_reasoning(""))


class TrimToLastSentenceTests(unittest.TestCase):
    """토큰 상한에 걸리면 문장 한가운데가 잘린다. 그대로 내보내면 고장처럼 보인다."""

    def test_keeps_complete_document_source_in_brackets(self) -> None:
        text = "보증금은 100,000,000원입니다. [오피스텔임대차계약서.pdf 2쪽]"

        self.assertEqual(text, llm_module.trim_to_last_sentence(text))

    def test_drops_incomplete_tail(self) -> None:
        text = "첫 문장입니다. 두 번째도 완성된 문장입니다. 세 번째는 여기서 끊기"

        self.assertEqual(
            "첫 문장입니다. 두 번째도 완성된 문장입니다.",
            llm_module.trim_to_last_sentence(text),
        )

    def test_drops_dangling_section_heading(self) -> None:
        """상한에 걸리면 소제목만 남기고 끝나는 일이 잦다.

        "### 4." 는 마침표로 끝나서 문장 단위 검사를 그냥 통과해 버린다.
        실제로 답변 두 건이 이 모양으로 화면까지 나갔다.
        """
        text = "앞 문장입니다. 내용이 이어집니다.\n\n### 4."

        self.assertEqual(
            "앞 문장입니다. 내용이 이어집니다.", llm_module.trim_to_last_sentence(text)
        )

    def test_drops_dangling_bold_heading(self) -> None:
        text = "앞 문장입니다.\n\n**결론**"

        self.assertEqual("앞 문장입니다.", llm_module.trim_to_last_sentence(text))

    def test_drops_truncated_markdown_link(self) -> None:
        """링크를 쓰다 끊기면 주소 안의 마침표 때문에 문장 검사를 통과해 버린다.

        실제로 "...효력이 생깁니다([주택임대차보호법 제3조 ①](https://www.law.go."
        로 끝난 답변이 화면까지 나갔다.

        ※ 예시를 실제 답변 길이로 쓴다. 짧은 예시로는 "잘라낼 양이 절반을 넘으면
          손대지 않는다"는 가드에 걸려 이 검사가 성립하지 않는다 — 그 가드가
          앞쪽 괄호로 답변 전체가 날아가는 것을 막는다.
        """
        text = ("임대차계약이 등기되지 않았더라도, 임차인이 주택을 인도받고 주민등록을 "
                "마친 다음 날부터 제삼자에 대해 효력이 생깁니다"
                "([주택임대차보호법 제3조 ①](https://www.law.go.")

        cleaned = llm_module.trim_to_last_sentence(text)

        self.assertTrue(cleaned.endswith("효력이 생깁니다"), cleaned)
        self.assertNotIn("https", cleaned)

    def test_keeps_balanced_parentheses(self) -> None:
        # 정상적으로 닫힌 괄호까지 지우면 안 된다.
        text = "보증금(전세금)을 반환받습니다."

        self.assertEqual(text, llm_module.trim_to_last_sentence(text))

    def test_keeps_complete_text(self) -> None:
        text = "완성된 답변입니다."

        self.assertEqual(text, llm_module.trim_to_last_sentence(text))

    def test_keeps_short_text_without_punctuation(self) -> None:
        # 마침표 없는 짧은 답까지 지워버리면 잘린 문장보다 나쁘다.
        self.assertEqual("짧은 답", llm_module.trim_to_last_sentence("짧은 답"))

    def test_half_guard_keeps_text_when_cut_would_remove_too_much(self) -> None:
        """잘라낼 양이 절반을 넘으면 손대지 않는다.

        실제 상한(700토큰)에 걸리는 답변은 500자쯤이고 끊긴 꼬리는 짧아서
        이 가드에 걸리지 않는다. 가드는 짧은 답변을 지키기 위한 것이다.
        """
        text = "네. 그리고 이어지는 긴 설명이 여기서 갑자기 끊어지는"

        self.assertEqual(text, llm_module.trim_to_last_sentence(text))

    def test_clean_output_does_both(self) -> None:
        body = "임차인이 인도와 주민등록을 마친 그 다음 날부터 대항력이 생깁니다. " * 5
        raw = f"<think>제99조를 쓸까 고민</think>{body}그리고 여기서 끊기"

        cleaned = llm_module.clean_output(raw)

        self.assertNotIn("<think>", cleaned)
        self.assertNotIn("제99조", cleaned)
        self.assertTrue(cleaned.endswith("대항력이 생깁니다."))
        self.assertNotIn("여기서 끊기", cleaned)


class UnlinkTests(unittest.TestCase):
    """출처 링크는 코드가 붙인다. 본문에 주소가 들어가면 손해만 있다.

    프롬프트로 금지했는데도 모델이 계속 URL을 썼고, 주소를 쓰다 토큰 상한에
    걸려 문장이 통째로 잘린 적이 있다.
    """

    def test_keeps_label_and_drops_address(self) -> None:
        text = "효력이 생깁니다([주택임대차보호법 제3조](https://www.law.go.kr/x)). 끝."

        self.assertEqual(
            "효력이 생깁니다(주택임대차보호법 제3조). 끝.", llm_module.unlink(text)
        )

    def test_complete_link_survives_cleaning(self) -> None:
        # 링크를 먼저 벗겨야 "닫히지 않은 괄호"로 오인돼 잘리지 않는다.
        text = "앞 문장([법령 제1조](https://x)). 뒤 문장입니다."

        cleaned = llm_module.clean_output(text)

        self.assertIn("뒤 문장입니다.", cleaned)
        self.assertNotIn("https", cleaned)

    def test_text_without_links_is_untouched(self) -> None:
        text = "주택임대차보호법 제3조에 따릅니다."

        self.assertEqual(text, llm_module.unlink(text))


class PostProcessingSafetyTests(unittest.TestCase):
    """후처리가 멀쩡한 답변을 파괴하지 않는지 지킨다.

    두 버그가 실제로 있었다. 코드 리뷰에서 잡히기 전까지 27문항 평가 결과
    (dev-001)를 오염시켰다.
    """

    def test_unclosed_bracket_early_in_text_is_left_alone(self) -> None:
        """앞쪽의 짝 없는 괄호로 답변 전체를 버리면 안 된다.

        가드가 없을 때 이 문장이 "판례" 두 글자로 잘렸다.
        """
        text = ("판례 [대법원 2011다49523 참조. 양수인이 임대인의 지위를 승계합니다. "
                "확정일자를 받으세요.")

        self.assertEqual(text, llm_module.clean_output(text))

    def test_unclosed_bracket_at_the_tail_is_dropped(self) -> None:
        # 반대로, 끝에서 잘린 링크는 여전히 걷어내야 한다.
        text = ("임대차계약이 등기되지 않았더라도, 임차인이 주택을 인도받고 주민등록을 "
                "마친 다음 날부터 제삼자에 대해 효력이 생깁니다"
                "([주택임대차보호법 제3조 ①](https://www.law.go.")

        cleaned = llm_module.clean_output(text)

        self.assertTrue(cleaned.endswith("효력이 생깁니다"))
        self.assertNotIn("https", cleaned)

    def test_short_korean_sentence_is_not_treated_as_a_stub(self) -> None:
        """길이만 보면 짧은 한국어 문장이 소제목으로 오인된다.

        기호·숫자를 걷어낸 뒤 8자 미만이라는 기준만 쓰면 "월세는 아닙니다."
        "1억 5천만원" 같은 정상 문장이 지워진다.
        """
        for line in ("월세는 아닙니다.", "감사합니다.", "1억 5천만원", "제3조 제2항"):
            self.assertFalse(llm_module._is_stub_line(line), line)

        for line in ("### 4.", "**결론**", "3)"):
            self.assertTrue(llm_module._is_stub_line(line), line)

    def test_last_short_line_survives_cleaning(self) -> None:
        text = "마친 그 다음 날부터 대항력이 생깁니다.\n월세는 아닙니다."

        self.assertEqual(text, llm_module.clean_output(text))


class ConfigConsistencyTests(unittest.TestCase):
    def test_max_tokens_override_reaches_the_server(self) -> None:
        """overrides 로 준 max_tokens 가 조용히 버려지면 안 된다.

        max_tokens= 로 넘기면 langchain 이 이름을 바꿔 보내 Ollama 가 무시하므로,
        extra_body 쪽으로 옮겨져야 실제로 반영된다.
        """
        self.assertEqual(500, llm_module.get_llm(max_tokens=500).extra_body["max_tokens"])

    def test_empty_fake_responses_is_rejected_at_creation(self) -> None:
        # 빈 리스트로 만들면 첫 호출에서 IndexError 로 터진다.
        with self.assertRaises(ValueError):
            llm_module.get_llm(fake_responses=[])


class ProbeTests(unittest.TestCase):
    def test_probe_reports_failure_without_raising(self) -> None:
        # 우선·로컬 후보를 모두 닫힌 포트로 향하게 해 전체 실패 경로를 확인한다.
        original = llm_module.LLM_BASE_URL
        original_local = llm_module.LOCAL_OLLAMA_BASE_URL
        llm_module.LLM_BASE_URL = "http://127.0.0.1:1/v1"
        llm_module.LOCAL_OLLAMA_BASE_URL = "http://127.0.0.1:2"
        llm_module._ROUTE_CACHE.clear()
        try:
            ok, message = llm_module.probe(timeout=1.0)
        finally:
            llm_module.LLM_BASE_URL = original
            llm_module.LOCAL_OLLAMA_BASE_URL = original_local
            llm_module._ROUTE_CACHE.clear()

        self.assertFalse(ok)
        self.assertIn("Ollama", message)




class MarkedSentenceSurvivalTests(unittest.TestCase):
    """표식이 붙은 정상 문장을 후처리가 지우지 않는가.

    `\\d+[.)]` 는 소제목뿐 아니라 날짜·금액·백분율도 잡는다. 노이즈 제거가
    숫자와 마침표를 걷어내면 한국어 단문은 쉽게 8자 미만이 되어 스텁으로 몰렸다.
    프롬프트 2번 규칙이 "원문 그대로 옮기라"고 지정한 바로 그 표현들이라
    후처리가 규칙을 정면으로 되돌리고 있었다.
    """

    def test_dates_amounts_and_numbered_sentences_survive(self) -> None:
        for line in (
            "1.5억원입니다.",
            "2026. 3. 1.부터 시행됩니다.",
            "5.5% 이내입니다.",
            "1. 네, 가능합니다.",
            "2) 됩니다.",
            "**결론**: 됩니다.",
        ):
            with self.subTest(line=line):
                self.assertFalse(llm_module._is_stub_line(line))

    def test_bare_headings_are_still_removed(self) -> None:
        for line in ("### 4.", "**결론**", "1.", "### 4. 우선변제"):
            with self.subTest(line=line):
                self.assertTrue(llm_module._is_stub_line(line))

    def test_clean_output_keeps_trailing_date_line(self) -> None:
        text = "확정일자를 받으시면 됩니다.\n2026. 3. 1.부터 적용됩니다."
        self.assertEqual(text, llm_module.clean_output(text))

    def test_clean_output_still_drops_bare_heading_tail(self) -> None:
        self.assertEqual(
            "본문입니다. 자세한 내용은 아래와 같습니다.",
            llm_module.clean_output("본문입니다. 자세한 내용은 아래와 같습니다.\n### 4."),
        )


class UnbalancedQuoteTests(unittest.TestCase):
    """곧은 따옴표를 열자마자 끊긴 답변을 완결로 보지 않는가."""

    def test_opening_quote_is_not_a_sentence_end(self) -> None:
        self.assertEqual(
            "조문은 다음과 같이 정합니다.",
            llm_module.trim_to_last_sentence('조문은 다음과 같이 정합니다. "'),
        )

    def test_balanced_quote_is_left_alone(self) -> None:
        text = '법원은 "임대인이 승계한다"고 보았습니다.'
        self.assertEqual(text, llm_module.trim_to_last_sentence(text))


class ExtraBodyOverrideTests(unittest.TestCase):
    """extra_body 를 넘겨도 토큰 상한과 think 스위치가 살아 있는가.

    settings.update(overrides) 가 딕셔너리를 통째로 갈아치우면, 상한이 조용히
    사라진다. max_tokens 오버라이드를 특별 처리한 것과 같은 이유로 병합한다.
    """

    def test_extra_body_is_merged_not_replaced(self) -> None:
        body = llm_module.get_llm(extra_body={"seed": 1}).extra_body
        self.assertEqual(llm_module.LLM_MAX_TOKENS, body["max_tokens"])
        self.assertEqual(1, body["seed"])

    def test_caller_key_wins(self) -> None:
        body = llm_module.get_llm(extra_body={"max_tokens": 7}).extra_body
        self.assertEqual(7, body["max_tokens"])


class EnvNumberTests(unittest.TestCase):
    """잘못된 숫자 환경 변수가 모듈 import 를 깨뜨리지 않는가.

    `.env` 에 키만 적고 값을 비워 두는 것은 흔한 표기이고 이 저장소의
    `.env.example` 도 그 방식이다. 그대로 int() 를 부르면 src.generation 전체가
    import 불가가 된다.
    """

    def test_blank_and_garbage_fall_back_to_default(self) -> None:
        import os
        from unittest import mock

        for raw in ("", "   ", "high"):
            with self.subTest(raw=raw):
                with mock.patch.dict(os.environ, {"JEONSEON_TEST_NUM": raw}):
                    self.assertEqual(
                        1200, llm_module._env_number("JEONSEON_TEST_NUM", "1200", int)
                    )

    def test_valid_value_is_used(self) -> None:
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"JEONSEON_TEST_NUM": "300"}):
            self.assertEqual(300, llm_module._env_number("JEONSEON_TEST_NUM", "1200", int))


class EnvTextTests(unittest.TestCase):
    def test_remote_runpod_url_is_used(self) -> None:
        import os
        from unittest import mock

        remote = "https://example-pod-11434.proxy.runpod.net/v1"
        with mock.patch.dict(os.environ, {"JEONSEON_TEST_URL": remote}):
            self.assertEqual(remote, llm_module._env_text("JEONSEON_TEST_URL", "local"))

    def test_blank_url_uses_local_default(self) -> None:
        import os
        from unittest import mock

        for raw in ("", "   "):
            with self.subTest(raw=raw):
                with mock.patch.dict(os.environ, {"JEONSEON_TEST_URL": raw}):
                    self.assertEqual(
                        "http://localhost:11434/v1",
                        llm_module._env_text(
                            "JEONSEON_TEST_URL", "http://localhost:11434/v1"
                        ),
                    )

    def test_runpod_v1_url_is_normalized_for_native_api(self) -> None:
        remote = "https://example-pod-11434.proxy.runpod.net/v1"
        original = llm_module.LLM_BASE_URL
        llm_module.LLM_BASE_URL = remote
        try:
            self.assertEqual(
                "https://example-pod-11434.proxy.runpod.net",
                llm_module._native_base_url(),
            )
            self.assertEqual(
                "https://example-pod-11434.proxy.runpod.net/api/chat",
                llm_module._native_chat_url(),
            )
        finally:
            llm_module.LLM_BASE_URL = original

if __name__ == "__main__":
    unittest.main()
