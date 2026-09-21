"""Run the DEV100-v2 question inputs through the production LangGraph path.

This runner reads only questions and their selected query mode. It does not read
gold requirements or expected answers, and records answer status plus validation
diagnostics so two LLM-generation runs can be compared safely.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.environment import load_project_environment

load_project_environment(ROOT / ".env")

from src.evaluation.chatting_provenance import capture_ollama_identity, streaming_sha256
from src.evaluation.run_generation_eval import compute_code_version
from src.generation import llm as llm_module
from src.generation.chain import get_default_service
from src.generation.graph import answer_question


DATASET = ROOT / "data/eval/dev100-v2/questions.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_questions(path: Path, mode: str) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 100:
        raise ValueError("DEV100-v2 입력은 정확히 100문항이어야 합니다.")
    rows: list[dict[str, str]] = []
    for item in payload:
        qid = item.get("qid")
        query = item.get("modes", {}).get(mode, {}).get("query")
        if not isinstance(qid, str) or not qid or not isinstance(query, str) or not query.strip():
            raise ValueError(f"DEV100-v2 {mode} 질문 형식이 올바르지 않습니다: {qid!r}")
        rows.append({"qid": qid, "question": query})
    if len({row["qid"] for row in rows}) != 100:
        raise ValueError("DEV100-v2 질문 ID가 고유하지 않습니다.")
    return rows


def _select_questions(rows: list[dict[str, str]], qids: str | None) -> list[dict[str, str]]:
    """Return explicitly requested question IDs in the order given by the user."""
    if qids is None:
        return rows
    requested = [qid.strip() for qid in qids.split(",") if qid.strip()]
    if not requested:
        raise ValueError("--qids에는 하나 이상의 DEV100-v2 질문 ID가 필요합니다.")
    if len(requested) != len(set(requested)):
        raise ValueError("--qids에 중복된 질문 ID가 있습니다.")
    by_qid = {row["qid"]: row for row in rows}
    missing = [qid for qid in requested if qid not in by_qid]
    if missing:
        raise ValueError(f"DEV100-v2에 없는 질문 ID입니다: {', '.join(missing)}")
    return [by_qid[qid] for qid in requested]


def _answer_record(answer, *, include_rejected_draft: bool = False) -> dict[str, object]:
    record = {
        "status": answer.status,
        "text": answer.text,
        "validation_mode": answer.validation_mode,
        "validation_codes": list(answer.validation_codes),
        "repair_attempts": answer.repair_attempts,
        "refusal_reason": answer.refusal_reason,
        "source_chunk_ids": [item.chunk_id for item in answer.evidences],
    }
    # 실패 초안에는 개인정보나 업로드 문서 표현이 섞일 수 있다. 기본 결과에는
    # 저장하지 않고, 로컬 원인 분석을 명시적으로 요청한 경우에만 남긴다.
    if include_rejected_draft:
        if answer.status == "abstained":
            record["rejected_draft"] = answer.raw_text
        record["initial_validation_codes"] = list(answer.initial_validation_codes)
        record["repair_validation_codes"] = list(answer.repair_validation_codes)
        record["initial_draft"] = answer.diagnostic_initial_draft
        record["repair_draft"] = answer.diagnostic_repair_draft
    return record


def _summary(rows: list[dict[str, object]]) -> dict[str, object]:
    answers = [row["answer"] for row in rows]
    statuses = Counter(answer["status"] for answer in answers)
    validation_codes = Counter(
        code for answer in answers for code in answer["validation_codes"]
    )
    refusal_reasons = Counter(
        answer["refusal_reason"] for answer in answers if answer["refusal_reason"]
    )
    return {
        "total": len(rows),
        "answered": statuses["answered"],
        "abstained": statuses["abstained"],
        "refused": statuses["refused"],
        "generation_errors": sum(bool(row["execution_error"]) for row in rows),
        "repair_attempted": sum(answer["repair_attempts"] > 0 for answer in answers),
        "validation_code_counts": dict(sorted(validation_codes.items())),
        "refusal_reason_counts": dict(sorted(refusal_reasons.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="새 결과 폴더")
    parser.add_argument(
        "--mode",
        choices=("question_only", "context_diagnostic"),
        default="question_only",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--limit", type=int, default=0, help="점검용 상한; 0이면 100문항")
    selection.add_argument(
        "--qids",
        help="쉼표로 구분한 DEV100-v2 질문 ID만 실행 (입력 순서 유지)",
    )
    parser.add_argument(
        "--include-rejected-draft",
        action="store_true",
        help="로컬 원인 분석용: 보류된 생성 초안을 결과 JSONL에만 기록",
    )
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("출력 폴더가 이미 있습니다. 새 경로를 사용하세요.")
    questions = _load_questions(DATASET, args.mode)
    questions = _select_questions(questions, args.qids)
    if args.limit:
        questions = questions[: args.limit]

    ready, _ = llm_module.probe(timeout=10)
    if not ready:
        raise RuntimeError("Ollama Qwen preflight에 실패했습니다.")
    identity = capture_ollama_identity(llm_module.LLM_MODEL, timeout=30)
    if not identity.get("available"):
        raise RuntimeError("Ollama 모델 digest를 확인하지 못했습니다.")

    output.mkdir(parents=True)
    result_path = output / "results.jsonl"
    manifest_path = output / "manifest.json"
    manifest = {
        "schema_version": 1,
        "complete": False,
        "dataset": "dev100-v2",
        "mode": args.mode,
        "selected_qids": [item["qid"] for item in questions],
        "input_sha256": streaming_sha256(DATASET),
        "code_version": compute_code_version(),
        "model_identity": identity,
        "settings": {
            "model": llm_module.LLM_MODEL,
            "temperature": llm_module.LLM_TEMPERATURE,
            "max_tokens": llm_module.LLM_MAX_TOKENS,
            "context_tokens": llm_module.LLM_NUM_CTX,
            "think_off": llm_module.THINK_OFF,
        },
        "started_at": _now(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    service = get_default_service()
    rows: list[dict[str, object]] = []
    with result_path.open("w", encoding="utf-8") as stream:
        for index, item in enumerate(questions, start=1):
            started = time.perf_counter()
            error = ""
            try:
                answer = answer_question(
                    item["question"],
                    service=service,
                    retain_rejected_draft=args.include_rejected_draft,
                )
                answer_data = _answer_record(
                    answer,
                    include_rejected_draft=args.include_rejected_draft,
                )
            except Exception as exception:
                error = type(exception).__name__
                answer_data = {
                    "status": "generation_error", "text": "", "validation_mode": "not_applicable",
                    "validation_codes": [], "repair_attempts": 0, "refusal_reason": "", "source_chunk_ids": [],
                }
            row = {
                "qid": item["qid"],
                "question": item["question"],
                "answer": answer_data,
                "execution_error": error,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "completed_at": _now(),
            }
            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            print(f"[{index}/{len(questions)}] {item['qid']} {answer_data['status']}", flush=True)

    identity_after = capture_ollama_identity(llm_module.LLM_MODEL, timeout=30)
    manifest.update({
        "complete": identity_after == identity,
        "model_unchanged": identity_after == identity,
        "completed_at": _now(),
        "summary": _summary(rows),
        "results_sha256": streaming_sha256(result_path),
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not manifest["complete"]:
        raise RuntimeError("실행 중 Ollama 모델 식별 정보가 변경되어 결과를 완료 처리하지 않았습니다.")
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    print(f"manifest: {manifest_path}")
    print(f"results: {result_path}")


if __name__ == "__main__":
    main()
