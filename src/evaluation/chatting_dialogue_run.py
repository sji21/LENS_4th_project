"""Real dialogue planner/router evaluation with a fixed, non-legal RAG boundary."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from unittest.mock import Mock, patch

from .chatting_run import capture_calls, fingerprints, observed_state, seed_state
from .chatting_provenance import capture_ollama_identity
from .chatting_scenarios import load_suite
from .chatting_scoring import score_turn, summarize


def run(split, output):
    from django.test.utils import override_settings
    from chat import services
    from chat import dialogue_planner
    from .chatting_plan_run import ObservedModel
    from src.generation.llm import LLM_MODEL
    from src.generation.models import Answer

    if output.exists():
        raise ValueError("Output exists; use a new capture path")
    identity = capture_ollama_identity(LLM_MODEL)
    if not identity["available"]:
        raise RuntimeError("Model identity unavailable")
    suite = load_suite(split)
    before = fingerprints()
    report = {
        "schema_version": 1, "mode": "dialogue_only", "split": split,
        "complete": False, "started_at": datetime.now(timezone.utc).isoformat(),
        "files": before, "model_identity": identity,
        "limits": "Real planner, guards, state, routing and document selection; fixed non-legal RAG responses. No retrieval ranking, legal generation, legal verification or OCR quality is measured. Both existing splits are exposed regression data, not unseen acceptance.",
        "legal_backend": "fixed_test_double", "rows": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    save()
    boundary = {}
    models = []
    create_model = dialogue_planner.create_planner_model
    def observed_model(document_ids):
        model = ObservedModel(create_model(document_ids))
        models.append(model)
        return model
    def answer(query, *args, **kwargs):
        boundary.update(query=query, document_ids=sorted({e.document_id for e in args[0]}) if args else [])
        return Answer(question=query, status="answered", text="대화 계층 평가용 고정 응답입니다.")
    loader = Mock()
    loader.result.return_value = None
    with override_settings(CHAT_CONVERSATION_ENABLED=True), \
            patch.object(dialogue_planner, "create_planner_model", side_effect=observed_model), \
            patch.object(services, "retrieval_loader", return_value=loader), \
            patch.object(services.graph, "answer_question", side_effect=answer), \
            patch.object(services.graph, "answer_document_question", side_effect=answer), \
            patch.object(services, "_respond_legacy", side_effect=AssertionError("Unexpected legacy route")):
        for case in suite["cases"]:
            state = seed_state(case, dialogue_enabled=True)
            for index, turn in enumerate(case["turns"]):
                previous = deepcopy(state["dialogue"])
                boundary.clear()
                models.clear()
                started = time.perf_counter()
                error = None
                with capture_calls() as calls:
                    try:
                        message = services.respond(state, turn["user"])
                    except Exception as exc:
                        error = type(exc).__name__
                        message = {"status": "error", "content": ""}
                observation = observed_state(state, message, boundary.get("query", ""))
                row = {
                    "case_id": case["id"], "turn": index + 1, "input": turn["user"],
                    "before_state": previous, "after_state": deepcopy(state["dialogue"]),
                    "observation": observation, "boundary": deepcopy(boundary),
                    "routing": deepcopy(state.get("dialogue_runtime", {})),
                    "raw_proposal": models[-1].proposal if models else None,
                    "execution_error": error, "model_calls": calls["model_calls"],
                    "failed_model_calls": calls["failed_model_calls"], "connection_failures": calls["connection_failures"],
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "checks": score_turn(turn["expect"], {**observation, "execution_error": error}),
                }
                report["rows"].append(row)
                save()
                print(f"{case['id']} turn={index + 1} action={message.get('action')} error={error}", flush=True)
    report["source_unchanged"] = before == fingerprints()
    report["model_unchanged"] = identity == capture_ollama_identity(LLM_MODEL)
    report["summary"] = summarize(report["rows"])
    report["complete"] = report["source_unchanged"] and report["model_unchanged"] and not any(
        r["execution_error"] or r["failed_model_calls"] or r["connection_failures"] for r in report["rows"])
    save()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "acceptance"], default="dev")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    if not run(args.split, args.output)["complete"]:
        raise SystemExit("Dialogue capture contains execution failures")


if __name__ == "__main__":
    main()
