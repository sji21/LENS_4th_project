"""Reproducible synthetic conversation capture; never imports expected values into the model."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import subprocess
import time
import urllib.request
from unittest.mock import patch

from .chatting_scenarios import load_suite, SUITE_DIR
from .chatting_scoring import score_turn, summarize
from .chatting_provenance import capture_retrieval_provenance, capture_ollama_identity, retrieval_semantically_equal

ROOT = Path(__file__).resolve().parents[2]


def fingerprints():
    files = [*ROOT.glob("chat/*.py"), *ROOT.glob("src/generation/*.py"), *ROOT.glob("src/retrieval/*.py"), *ROOT.glob("src/evaluation/chatting*.py"), *SUITE_DIR.glob("*.json")]
    files += list((ROOT / "data/chunks").glob("*.jsonl"))
    return {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def seed_state(case, *, dialogue_enabled=False):
    from chat.services import initial_state
    from src.document_check.extraction_models import ExtractionResult, PageExtraction
    from src.document_check.session_retrieval import build_session_document_context
    state = initial_state()
    seed = deepcopy(case.get("initial_state", {}))
    if seed:
        # Both paths receive identical prior user statements and the pending assistant question.
        labels = {"contract_type": "계약 형태", "contract_ended": "계약 종료", "deposit_returned": "보증금 반환", "living_in_property": "현재 거주"}
        content = "; ".join(f"{labels.get(k, k)}: {v}" for k, v in seed.get("facts", {}).items())
        content = f"상담 주제: {seed.get('topic', '')}. {content}"
        state["messages"] = [
            {"id": "seed-user", "role": "user", "content": content},
            {"id": "seed-assistant", "role": "assistant", "content": seed.get("pending_question") or "앞선 답변은 보류되었습니다.", "status": seed.get("last_answer_status", "clarifying"), "context_content": ""},
        ]
        state["evaluation_seed"] = seed
        if dialogue_enabled:
            from chat.dialogue_state import apply_user_update
            dialogue = apply_user_update(state, user=content, topic=seed.get("topic"), updates={
                k: {"value": v, "evidence": v} for k, v in seed.get("facts", {}).items()
            })
            if seed.get("pending_question"):
                dialogue["pending"] = {"field": seed["pending_field"], "question": seed["pending_question"], "attempts": 1}
            dialogue["last_status"] = seed.get("last_answer_status")
    for doc in case.get("documents", []):
        text = doc["text"]
        extraction = ExtractionResult((PageExtraction(1, text, "embedded_text", len(text)),), 0)
        context = build_session_document_context(f"{doc['id']}.pdf", extraction, case["id"], document_id=doc["id"], document_kind=doc["label"])
        state["documents"].append({
            "document_id": doc["id"], "kind": doc["kind"], "label": doc["label"],
            "filename": f"{doc['id']}.pdf", "page_count": 1, "confidence": 1.0,
            "checksum": sha256(text.encode()).hexdigest(), "context": asdict(context), "analysis": {},
        })
    return state


@contextmanager
def capture_calls():
    from src.generation import graph
    stats = {"model_calls": 0, "failed_model_calls": 0, "connection_failures": 0, "query": ""}
    urlopen = urllib.request.urlopen
    answer = graph.answer_question

    class ObservedResponse:
        def __init__(self, response):
            self.response = response
            self.failed = False

        def __enter__(self):
            self.response.__enter__()
            return self

        def __exit__(self, *args):
            return self.response.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.response, name)

        def read(self, *args, **kwargs):
            try:
                body = self.response.read(*args, **kwargs)
                # Native Ollama uses one non-streamed JSON response. Observe decode
                # failures here too, even if an auxiliary model's caller recovers.
                json.loads(body)
                return body
            except Exception:
                if not self.failed:
                    stats["failed_model_calls"] += 1
                    self.failed = True
                raise

    def counted(request, *args, **kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        is_model = url.endswith("/api/chat")
        if is_model:
            stats["model_calls"] += 1
        try:
            response = urlopen(request, *args, **kwargs)
            return ObservedResponse(response) if is_model else response
        except Exception:
            if url.endswith(("/api/chat", "/api/tags")):
                stats["connection_failures"] += 1
            if is_model:
                stats["failed_model_calls"] += 1
            raise

    def question(query, *args, **kwargs):
        stats["query"] = query
        return answer(query, *args, **kwargs)

    with patch.object(urllib.request, "urlopen", counted), patch.object(graph, "answer_question", question):
        yield stats


def observed_state(state, message, query, *, dialogue_enabled=True):
    dialogue = state.get("dialogue") if dialogue_enabled else None
    facts = None
    if dialogue is not None:
        facts = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in dialogue.get("facts", {}).items()}
        if dialogue.get("topic"):
            facts["topic"] = dialogue["topic"]
    action = message.get("action")
    if action is None and message.get("status") != "error":
        if message.get("status") == "refused":
            action = "refuse"
        elif query:
            action = "rag"
    return {
        "action": action,
        "intent": message.get("intent"), "facts": facts,
        "query": message.get("search_query", query),
    }


def run(split, mode, output, selected=None):
    from django.conf import settings
    from django.test.utils import override_settings
    from chat import services
    from src.generation import llm
    from langsmith import tracing_context
    if output.exists():
        raise ValueError("Output exists; choose a new capture path")
    ready, _ = llm.probe(timeout=5)
    if not ready:
        raise RuntimeError("Ollama preflight failed; no quality measurement was performed")
    model_identity = capture_ollama_identity(llm.LLM_MODEL)
    if not model_identity["available"]:
        raise RuntimeError("Ollama identity unavailable; no quality measurement was performed")
    suite = load_suite(split)
    cases = [c for c in suite["cases"] if not selected or c["id"] in selected]
    if not cases or selected and set(selected) != {c["id"] for c in cases}:
        raise ValueError("Unknown case selection")
    before = fingerprints()
    report = {
        "schema_version": 1, "complete": False, "mode": mode, "split": split,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files": before, "model": llm.LLM_MODEL, "model_identity": model_identity,
        "settings": {"temperature": llm.LLM_TEMPERATURE, "max_tokens": llm.LLM_MAX_TOKENS, "context": llm.LLM_NUM_CTX, "timeout": llm.LLM_TIMEOUT, "think_off": llm.THINK_OFF},
        "versions": {p: importlib.metadata.version(p) for p in ("Django", "langchain-core", "langgraph", "chromadb", "sentence-transformers")},
        "limits": "Synthetic conversations; automated dialogue checks are not legal accuracy. Document text is injected after extraction; OCR is not measured.",
        "rows": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    save()
    # Model loading happens once; report its cold start separately from per-turn latency.
    start = time.perf_counter()
    service = services.retrieval_loader().start().result()
    report["retrieval_initialization_seconds"] = round(time.perf_counter() - start, 3)
    report["retrieval"] = capture_retrieval_provenance(service)
    save()
    if not report["retrieval"]["ready"]:
        raise RuntimeError("Hybrid retrieval preflight failed; see recorded readiness issues")
    with override_settings(CHAT_CONVERSATION_ENABLED=mode == "upgrade"), tracing_context(enabled=False):
        for case in cases:
            state = seed_state(case, dialogue_enabled=mode == "upgrade")
            for index, turn in enumerate(case["turns"]):
                start = time.perf_counter()
                error = None
                before_state = deepcopy(state.get("dialogue"))
                with capture_calls() as calls:
                    try:
                        message = services.respond(state, turn["user"])
                    except Exception as exception:
                        error = type(exception).__name__
                        message = {"content": "", "status": "error"}
                from src.generation.prompt import GENERATION_FAILED_TEXT
                if message.get("content", "").startswith(GENERATION_FAILED_TEXT):
                    error = error or "GenerationFailed"
                observation = observed_state(state, message, calls["query"], dialogue_enabled=mode == "upgrade")
                row = {
                    "case_id": case["id"], "turn": index + 1, "input": turn["user"],
                    "before_state": before_state, "after_state": deepcopy(state.get("dialogue")),
                    "observation": observation,
                    "message": {k: v for k, v in message.items() if k != "context_content"},
                    "elapsed_seconds": round(time.perf_counter() - start, 3),
                    "model_calls": calls["model_calls"], "failed_model_calls": calls["failed_model_calls"],
                    "connection_failures": calls["connection_failures"],
                    "execution_error": error,
                    "checks": score_turn(turn["expect"], {**observation, "execution_error": error}),
                }
                report["rows"].append(row)
                save()
                print(f"{case['id']} turn={index + 1} status={message.get('status')} seconds={row['elapsed_seconds']} calls={calls['model_calls']}", flush=True)
    report["source_unchanged"] = before == fingerprints()
    report["retrieval_after"] = capture_retrieval_provenance(service)
    report["retrieval_unchanged"] = retrieval_semantically_equal(report["retrieval"], report["retrieval_after"])
    report["retrieval_artifacts_unchanged"] = report["retrieval"] == report["retrieval_after"]
    if report["retrieval_unchanged"] and not report["retrieval_artifacts_unchanged"]:
        report["artifact_note"] = "Physical Chroma files changed during reads; source, text, metadata, stored vectors and collection settings remained identical."
    report["model_identity_after"] = capture_ollama_identity(llm.LLM_MODEL)
    report["model_unchanged"] = model_identity == report["model_identity_after"]
    report["summary"] = summarize(report["rows"])
    report["complete"] = report["source_unchanged"] and report["retrieval_unchanged"] and report["model_unchanged"] and not any(r["execution_error"] or r["connection_failures"] or r["failed_model_calls"] for r in report["rows"])
    save()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "acceptance"], default="dev")
    parser.add_argument("--mode", choices=["legacy", "upgrade"], default="legacy")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    logging.basicConfig(level=logging.ERROR)
    result = run(args.split, args.mode, args.output, args.case)
    if not result["complete"]:
        raise SystemExit("Capture incomplete; see recorded failures")


if __name__ == "__main__":
    main()
