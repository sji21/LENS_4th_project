import unittest

from src.security.secret_filter import (
    REDACTION,
    contains_secret,
    find_secrets,
    redact_secrets,
)


class SecretFilterTests(unittest.TestCase):
    def test_redacts_named_openai_key_assignment(self):
        result = redact_secrets(
            "OPENAI_API_KEY=abcd1234-secret-value"
        )

        self.assertEqual(
            result.text,
            f"OPENAI_API_KEY={REDACTION}",
        )
        self.assertTrue(result.contains_secret)
        self.assertEqual(
            result.findings[0].kind,
            "named_secret",
        )
        self.assertEqual(
            result.findings[0].label,
            "OPENAI_API_KEY",
        )

    def test_does_not_flag_empty_env_template(self):
        text = (
            "OPENAI_API_KEY=\n"
            "LAW_GO_KR_API_KEY=\n"
            "TESSERACT_CMD="
        )

        self.assertFalse(contains_secret(text))
        self.assertEqual(redact_secrets(text).text, text)

    def test_redacts_password_assignment(self):
        result = redact_secrets(
            "password: super-secret-password"
        )

        self.assertEqual(
            result.text,
            f"password: {REDACTION}",
        )

    def test_redacts_double_quoted_password_assignment(self):
        result = redact_secrets('PASSWORD="hunter22"')

        self.assertEqual(
            result.text,
            f'PASSWORD="{REDACTION}"',
        )
        self.assertTrue(result.contains_secret)

    def test_redacts_single_quoted_api_key_assignment(self):
        result = redact_secrets("API_KEY='abcd1234'")

        self.assertEqual(
            result.text,
            f"API_KEY='{REDACTION}'",
        )
        self.assertTrue(result.contains_secret)

    def test_redacts_bearer_token_but_keeps_header(self):
        result = redact_secrets(
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
        )

        self.assertEqual(
            result.text,
            f"Authorization: Bearer {REDACTION}",
        )
        self.assertEqual(
            result.findings[0].kind,
            "bearer_token",
        )

    def test_redacts_openai_prefix_without_label(self):
        secret = "sk-" + "A" * 28
        result = redact_secrets(
            f"토큰은 {secret} 입니다"
        )

        self.assertNotIn(secret, result.text)
        self.assertEqual(
            result.findings[0].kind,
            "openai_key",
        )

    def test_redacts_github_token_without_label(self):
        secret = "ghp_" + "B" * 28
        result = redact_secrets(secret)

        self.assertEqual(result.text, REDACTION)
        self.assertEqual(
            result.findings[0].kind,
            "github_token",
        )

    def test_redacts_huggingface_token_without_label(self):
        secret = "hf_" + "C" * 28
        result = redact_secrets(secret)

        self.assertEqual(result.text, REDACTION)
        self.assertEqual(
            result.findings[0].kind,
            "huggingface_token",
        )

    def test_redacts_multiple_secrets(self):
        openai = "sk-" + "D" * 28
        github = "ghp_" + "E" * 28

        result = redact_secrets(
            f"OPENAI_API_KEY={openai}\n"
            f"GITHUB_TOKEN={github}"
        )

        self.assertEqual(
            result.text.count(REDACTION),
            2,
        )
        self.assertEqual(
            len(result.findings),
            2,
        )

    def test_findings_do_not_store_secret_value(self):
        secret = "sk-" + "F" * 28
        findings = find_secrets(secret)

        self.assertTrue(findings)
        self.assertNotIn(
            secret,
            repr(findings),
        )

    def test_legal_case_number_is_not_secret(self):
        self.assertFalse(
            contains_secret(
                "대법원 2021다238650 판결을 설명해 주세요."
            )
        )

    def test_law_article_and_money_are_not_secret(self):
        text = (
            "주택임대차보호법 제3조의2와 "
            "보증금 165,000,000원을 비교해 주세요."
        )

        self.assertFalse(contains_secret(text))

    def test_normal_question_with_token_word_is_not_secret(self):
        self.assertFalse(
            contains_secret(
                "전세계약 갱신에 필요한 토큰이 있나요?"
            )
        )

    def test_custom_replacement(self):
        result = redact_secrets(
            "API_KEY=abcd1234",
            replacement="<secret>",
        )

        self.assertEqual(
            result.text,
            "API_KEY=<secret>",
        )


if __name__ == "__main__":
    unittest.main()
