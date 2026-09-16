"""Real planner + retrieval + generation + unchanged validation, with synthetic dialogue."""
import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.test.utils import override_settings
from chat import services, dialogue_planner
from src.evaluation.chatting_plan_run import ObservedModel
from src.evaluation.chatting_run import fingerprints

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--first-only", action="store_true")
args = parser.parse_args()
if args.output.exists():
    raise SystemExit("Choose a new output; prior failures must be preserved")

def source_hashes():
    files = fingerprints()
    path = Path("chat/renewal_guides.json")
    files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files

report = {"mode": "real_planner_and_full_rag", "files": source_hashes(), "rows": []}
state = services.initial_state()
create = dialogue_planner.create_planner_model
run_graph = services.graph.answer_question
models, answers = [], []

def observed(ids):
    model = ObservedModel(create(ids))
    models.append(model)
    return model

def graph(*args, **kwargs):
    answer = run_graph(*args, **kwargs)
    answers.append(asdict(answer))
    return answer

turns = ["지금 살고 싶은 월세집의 계약이 끝나가는데 계약 갱신은 어떻게 해?",
         "계약 만료일은 2026년 12월 31일이야.", "그럼 문자로 갱신 요청해도 돼?"]
with override_settings(CHAT_CONVERSATION_ENABLED=True), patch.object(dialogue_planner, "create_planner_model", side_effect=observed), patch.object(services.graph, "answer_question", side_effect=graph):
    for user in turns[:1] if args.first_only else turns:
        models.clear()
        answers.clear()
        started = time.perf_counter()
        message = services.respond(state, user)
        row = {"user": user, "seconds": round(time.perf_counter() - started, 2),
               "proposal": models[-1].proposal if models else None,
               "message": message, "answers": answers[:], "dialogue": deepcopy(state["dialogue"])}
        report["rows"].append(row)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"user": user, "seconds": row["seconds"], "message": message}, ensure_ascii=False), flush=True)
report["source_unchanged"] = report["files"] == source_hashes()
report["checks"] = {
    "first_answered": report["rows"][0]["message"]["status"] == "answered",
    "first_asks_end_date": (report["rows"][0]["dialogue"].get("pending") or {}).get("field") == "end_date",
    "substantive_questions_answered": all(row["message"]["status"] == "answered" for index, row in enumerate(report["rows"]) if index != 1),
    "date_acknowledged_and_not_reasked": len(report["rows"]) == 1 or (
        report["rows"][1]["message"]["action"] == "clarify"
        and report["rows"][1]["dialogue"]["facts"].get("end_date", {}).get("value") == "2026년 12월 31일"
        and (report["rows"][1]["dialogue"].get("pending") or {}).get("field") == "landlord_notified"
    ),
}
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(report["checks"], flush=True)
