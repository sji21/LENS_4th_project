"""Load evaluation conversations. Runtime chatbot code must not import these cases."""
import json
from pathlib import Path

SUITE_DIR = Path(__file__).resolve().parents[2] / "data" / "eval" / "chatting"
ACTIONS = {"social", "clarify", "rag", "refuse"}
INTENTS = {"greeting", "question", "followup", "correction", "clarification_answer", "explain", "topic_change", "document_question"}


def validate_suite(payload):
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("Unsupported conversation suite schema")
    split = payload.get("split")
    if not isinstance(split, str) or split not in {"dev", "acceptance"}:
        raise ValueError("Expected dev or acceptance split")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Conversation suite is empty")
    ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Invalid case")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise ValueError("Missing or duplicate case ID")
        ids.add(case_id)
        if not isinstance(case.get("category"), str) or not case["category"]:
            raise ValueError("Missing category")
        if not isinstance(case.get("initial_state", {}), dict):
            raise ValueError("Invalid initial state")
        documents = case.get("documents", [])
        if not isinstance(documents, list):
            raise ValueError("Invalid documents")
        doc_ids = set()
        for doc in documents:
            if not isinstance(doc, dict) or not all(isinstance(doc.get(k), str) and doc[k] for k in ("id", "kind", "label", "text")):
                raise ValueError("Invalid synthetic document")
            if doc["id"] in doc_ids or doc["kind"] not in {"contract", "registry"}:
                raise ValueError("Invalid document identity")
            doc_ids.add(doc["id"])
        turns = case.get("turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError("Missing turns")
        for turn in turns:
            if not isinstance(turn, dict) or not isinstance(turn.get("user"), str) or not turn["user"].strip():
                raise ValueError("Missing user message")
            expect = turn.get("expect")
            if not isinstance(expect, dict) or not isinstance(expect.get("actions"), list) or not expect["actions"]:
                raise ValueError("Missing expected actions")
            if any(not isinstance(a, str) or a not in ACTIONS for a in expect["actions"]):
                raise ValueError("Unknown expected action")
            if not isinstance(expect.get("intent"), str) or expect["intent"] not in INTENTS:
                raise ValueError("Unknown expected intent")
            for key in ("facts", "forbidden_facts"):
                values = expect.get(key, {})
                if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
                    raise ValueError("Invalid fact expectations")
            for key in ("query_contains", "query_excludes"):
                values = expect.get(key, [])
                if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
                    raise ValueError("Invalid query expectations")
    return payload


def load_suite(split="dev"):
    if split not in {"dev", "acceptance"}:
        raise ValueError("Unknown conversation suite")
    payload = validate_suite(json.loads((SUITE_DIR / f"{split}.json").read_text()))
    if payload["split"] != split:
        raise ValueError("Conversation split does not match filename")
    return payload
