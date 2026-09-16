"""Manual UI fixture server: isolated temporary DB, no LLM or OCR quality claim.

Run from repository root: python -m tests.browser_chatting_fixture
Only binds loopback port 8800. The database is removed when this process exits.
"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock


def main():
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.test_settings"
    from django.conf import settings
    with TemporaryDirectory(prefix="lens-browser-fixture-") as directory:
        settings.DATABASES["default"]["NAME"] = str(Path(directory) / "web.sqlite3")
        settings.DEBUG = True
        settings.CHAT_CONVERSATION_ENABLED = True
        settings.SESSION_COOKIE_NAME = "lens_fixture_session"
        settings.CSRF_COOKIE_NAME = "lens_fixture_csrf"
        import django
        django.setup()
        from django.core.management import call_command
        from chat import services, dialogue_planner
        from chat.dialogue_contract import Decision
        from src.generation import chain
        from src.generation.models import Answer
        from src.document_check.extraction_models import ExtractionResult, PageExtraction

        def plan(state, user, document_id=None):
            if user == "오류 확인":
                raise RuntimeError("FIXTURE_PRIVATE_ERROR")
            clarify = user == "확인 질문 테스트"
            style = "simple" if "쉽게" in user else "brief" if "요약" in user else "standard"
            return SimpleNamespace(decision=Decision(
                intent="explain" if style != "standard" else "document_question" if document_id else "question",
                action="clarify" if clarify else "rag", topic="보증금반환", topic_changed=False,
                updates={}, clarify_field="contract_ended" if clarify else None, question=None,
                search_query="" if clarify else user, document_id=document_id, style=style,
            ))

        def answer(question, *args, **kwargs):
            return Answer(question=question, status="answered", text="화면 검증용 합성 답변 · " + kwargs.get("response_style", "standard"), raw_text="FIXTURE_PRIVATE_CONTEXT")

        ready = SimpleNamespace(start=lambda: ready, result=lambda: object(),
                                snapshot=lambda: SimpleNamespace(state="ready", elapsed_seconds=0), retry=lambda: False)
        services.retrieval_loader = lambda: ready
        dialogue_planner.plan_turn = plan
        services.graph.answer_question = answer
        services.graph.answer_document_question = answer
        chain.get_llm = Mock(side_effect=AssertionError("Real model calls forbidden in UI fixture"))
        services.analyze_uploaded_document = lambda filename, data: SimpleNamespace(
            extraction=ExtractionResult(pages=(PageExtraction(1, "임대차계약서 보증금 삼천만원 특약 확인", "embedded_text", 30),), elapsed_seconds=0),
            classification=SimpleNamespace(kind="contract", confidence="high"),
            analysis=SimpleNamespace(to_public_dict=lambda: {"headline": "합성 계약서", "summary": "UI 검사 fixture", "fields": []}),
        )
        call_command("migrate", verbosity=0)
        print("UI fixture only; no real LLM/OCR evaluation. http://127.0.0.1:8800/", flush=True)
        call_command("runserver", "127.0.0.1:8800", use_reloader=False)


if __name__ == "__main__":
    main()
