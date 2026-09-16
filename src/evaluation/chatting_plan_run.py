"""Development-only planner calibration; never executes RAG or a public reply."""
import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import time

from .chatting_run import capture_calls, fingerprints, observed_state, seed_state
from .chatting_provenance import capture_ollama_identity
from .chatting_scenarios import load_suite
from .chatting_scoring import score_turn, summarize


class ObservedModel:
    def __init__(self, model):
        self.model = model
        self.proposal = None

    def invoke(self, messages):
        self.proposal = None
        response = self.model.invoke(messages)
        self.proposal = response.content if isinstance(response.content, str) else None
        return response


def run(output, selected=None):
    from chat.dialogue_planner import create_planner_model, expand_proposal, plan_turn, PlanningError, PLANNER_TIMEOUT, PLANNER_MAX_TOKENS, PLANNER_CONTEXT, PLANNER_THINK
    from chat.dialogue_state import apply_user_update, record_answer
    from src.generation.llm import LLM_MODEL
    if output.exists():
        raise ValueError("Output exists; use a new calibration file")
    identity = capture_ollama_identity(LLM_MODEL)
    if not identity["available"]:
        raise RuntimeError("Model identity unavailable")
    suite = load_suite("dev")
    cases = [c for c in suite["cases"] if not selected or c["id"] in selected]
    if not cases or selected and set(selected) != {c["id"] for c in cases}:
        raise ValueError("Unknown development cases")
    before = fingerprints()
    report = {
        "schema_version": 1, "mode": "planner_only", "split": "dev", "complete": False,
        "case_ids": [case["id"] for case in cases],
        "expected_turn_count": sum(len(case["turns"]) for case in cases),
        "started_at": datetime.now(timezone.utc).isoformat(), "files": before,
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "model_identity": identity, "settings": {"temperature": 0, "timeout": PLANNER_TIMEOUT, "max_tokens": PLANNER_MAX_TOKENS, "context": PLANNER_CONTEXT, "think": PLANNER_THINK, "route_fallback": False},
        "limits": "Planner proposals only, with accepted user-state transitions simulated. No RAG, legal validation or public answer is executed. Raw proposals are synthetic evaluation data, not approved answers. Acceptance cases are not used.",
        "rows": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    save()
    for case in cases:
        state = seed_state(case, dialogue_enabled=True)
        model = ObservedModel(create_planner_model([d["document_id"] for d in state["documents"]]))
        for index, turn in enumerate(case["turns"]):
            previous = deepcopy(state["dialogue"])
            start = time.perf_counter()
            message, error, proposal = {}, None, None
            normalizations = ()
            raw_output_valid = False
            raw_intent = None
            model.proposal = None
            with capture_calls() as calls:
                try:
                    result = plan_turn(state, turn["user"], llm=model)
                    decision = result.decision
                    normalizations = result.normalizations
                    proposal = asdict(decision)
                    dialogue = apply_user_update(state, user=turn["user"], updates=decision.updates, topic=decision.topic, topic_changed=decision.topic_changed, document_id=decision.document_id)
                    dialogue["pending"] = ({"field": decision.clarify_field, "question": decision.question, "attempts": 1} if decision.action == "clarify" else None)
                    record_answer(state, {"status": "not_executed"})
                    message = {"action": decision.action, "intent": decision.intent, "search_query": decision.search_query}
                except PlanningError as exception:
                    error = exception.code
            from chat.dialogue_contract import DecisionError, parse_decision, _unique_object
            try:
                raw_decision = parse_decision(expand_proposal(model.proposal, turn["user"]), state={**state, "dialogue": previous}, user=turn["user"], updates_as_list=True, preserved_user=True)
                raw_output_valid = True
                raw_intent = raw_decision.intent
            except DecisionError:
                # Report the original label even if another contract field failed.
                try:
                    raw_intent = json.loads(model.proposal, object_pairs_hook=_unique_object).get("intent")
                except (TypeError, ValueError, AttributeError, RecursionError):
                    pass
            observation = observed_state(state, message, "")
            row = {
                "case_id": case["id"], "turn": index + 1, "input": turn["user"],
                "before_state": previous, "after_state": deepcopy(state["dialogue"]),
                "decision": proposal, "raw_proposal": model.proposal,
                "raw_output_valid": raw_output_valid, "normalizations": list(normalizations),
                "raw_intent_check": score_turn(turn["expect"], {**observation, "intent": raw_intent})["intent"],
                "observation": observation, "execution_error": error,
                "model_calls": calls["model_calls"], "failed_model_calls": calls["failed_model_calls"],
                "connection_failures": calls["connection_failures"],
                "elapsed_seconds": round(time.perf_counter() - start, 3),
                "checks": score_turn(turn["expect"], observation),
            }
            report["rows"].append(row)
            save()
            print(f"{case['id']} turn={index + 1} action={message.get('action')} error={error} seconds={row['elapsed_seconds']}", flush=True)
    report["source_unchanged"] = before == fingerprints()
    report["model_unchanged"] = identity == capture_ollama_identity(LLM_MODEL)
    report["summary"] = summarize(report["rows"])
    report["raw_output_valid_turns"] = sum(row["raw_output_valid"] for row in report["rows"])
    report["normalization_turns"] = sum(bool(row["normalizations"]) for row in report["rows"])
    report["raw_intent_summary"] = {name: sum(row["raw_intent_check"]["passed"] is value for row in report["rows"]) for name, value in (("passed", True), ("failed", False), ("not_observable", None))}
    report["complete"] = len(report["rows"]) == report["expected_turn_count"] and report["source_unchanged"] and report["model_unchanged"] and not any(r["execution_error"] or r["failed_model_calls"] or r["connection_failures"] for r in report["rows"])
    save()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    logging.basicConfig(level=logging.ERROR)
    report = run(args.output, args.case)
    if not report["complete"]:
        raise SystemExit("Planner calibration contains failures")


if __name__ == "__main__":
    main()
