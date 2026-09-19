"""Run the frozen HO30 questions through the production RAG and Qwen3.8-27B path.

The runner never reads the gold/review files. It writes a provenance manifest before
the first question and appends one JSONL checkpoint per completed question.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.environment import load_project_environment

load_project_environment(ROOT / ".env")

from src.evaluation.chatting_provenance import capture_ollama_identity, streaming_sha256
from src.generation import llm as llm_module
from src.generation.chain import (
    DEFAULT_K_CASE,
    DEFAULT_K_GUIDE,
    DEFAULT_K_LAW,
    _build_service,
    answer_question,
)


DATASET = ROOT / "data/eval/holdout-v2-ho30-20260918"
QUESTIONS = DATASET / "questions.jsonl"
CONTRACT = DATASET / "evaluation-contract.json"
PROTOCOL = DATASET / "run-protocol-27b.json"
CODE_FILES = (
    ROOT / "src/generation/llm.py",
    ROOT / "src/generation/prompt.py",
    ROOT / "src/generation/validation.py",
    ROOT / "src/generation/chain.py",
    ROOT / "src/generation/evidence_routing.py",
    ROOT / "src/retrieval/service.py",
    ROOT / "src/retrieval/expanded.py",
)
DATA_ROOTS = (
    ROOT / "data/chunks",
    ROOT / "data/database",
    ROOT / "data/index",
    ROOT / "data/case_corpus",
)
FROZEN_GENERATION_SETTINGS = {
    "temperature": 0.0,
    "max_tokens": 512,
    "context_tokens": 8192,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_questions() -> list[dict[str, str]]:
    rows = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 30 or len({row.get("id") for row in rows}) != 30:
        raise ValueError("HO30 입력은 고유한 30문항이어야 합니다.")
    if any(set(row) != {"id", "question"} for row in rows):
        raise ValueError("실행 입력에는 id와 question만 허용됩니다.")
    return rows


def _git_identity() -> dict[str, object]:
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True
    ).strip()
    return {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
        # 로컬 실험은 커밋 전에도 허용하되, 결과를 clean run으로
        # 오인하지 않도록 당시 상태를 manifest에 고정한다.
        "dirty": bool(status),
        "status_sha256": sha256(status.encode("utf-8")).hexdigest(),
        "changed_path_count": len(status.splitlines()) if status else 0,
    }


def _artifact_hashes() -> dict[str, dict[str, object]]:
    files = list(CODE_FILES) + [QUESTIONS, CONTRACT, PROTOCOL]
    for folder in DATA_ROOTS:
        if folder.exists():
            files.extend(
                path
                for path in folder.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and path.name not in {"runtime-profile.json", "runtime-profile.json.tmp"}
            )
    result = {}
    for path in sorted(set(files)):
        if not path.is_file():
            continue
        label = path.relative_to(ROOT).as_posix()
        result[label] = {"sha256": streaming_sha256(path), "bytes": path.stat().st_size}
    return result


def _evidence(evidence) -> dict[str, object]:
    return asdict(evidence)


class CapturingService:
    def __init__(self, service):
        self.service = service
        self.calls: list[dict[str, object]] = []

    def search(self, question: str, **kwargs):
        result = self.service.search(question, **kwargs)
        self.calls.append(
            {
                "question": question,
                "budgets": kwargs,
                "laws": [_evidence(item) for item in result.laws],
                "cases": [_evidence(item) for item in result.cases],
                "civil_laws": [_evidence(item) for item in result.civil_laws],
                "guides": [_evidence(item) for item in result.guides],
            }
        )
        return result


def _completed(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ids.add(json.loads(line)["id"])
    return ids


def _assert_frozen_protocol() -> None:
    """Reject runs whose generation settings differ from the HO30 contract."""
    if llm_module.LLM_MODEL != "qwen3.8:27b":
        raise RuntimeError(f"HO30 27B 프로토콜과 다른 모델입니다: {llm_module.LLM_MODEL}")
    if not llm_module.THINK_OFF:
        raise RuntimeError("HO30 27B 프로토콜은 think=False만 허용합니다.")
    actual = {
        "temperature": llm_module.LLM_TEMPERATURE,
        "max_tokens": llm_module.LLM_MAX_TOKENS,
        "context_tokens": llm_module.LLM_NUM_CTX,
    }
    if actual != FROZEN_GENERATION_SETTINGS:
        raise RuntimeError(
            "HO30 27B 고정 생성 설정(temperature=0.0·max_tokens=512·context=8192)이 변경됐습니다."
        )
    if (DEFAULT_K_LAW, DEFAULT_K_CASE, DEFAULT_K_GUIDE) != (3, 2, 2):
        raise RuntimeError("고정 검색 예산(법령3·판례2·안내2)이 변경됐습니다.")


def _capture_required_model_identity(*, expected: dict[str, object] | None = None) -> dict[str, object]:
    """Read the mutable Ollama tag and reject unavailable or retagged models."""
    identity = capture_ollama_identity(llm_module.LLM_MODEL, timeout=30)
    if not identity.get("available"):
        raise RuntimeError("Ollama에서 27B 모델 digest를 확인하지 못했습니다.")
    if expected is not None and identity != expected:
        raise RuntimeError("체크포인트 이후 Ollama 모델 digest 또는 모델 식별 정보가 변경됐습니다.")
    return identity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="새 실행 결과 폴더")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="점검용 처리 상한; 0이면 30문항")
    parser.add_argument("--confirm-frozen-protocol", action="store_true", required=True)
    args = parser.parse_args()

    questions = _load_questions()
    git = _git_identity()
    _assert_frozen_protocol()

    output = args.output.resolve()
    manifest_path = output / "manifest.json"
    results_path = output / "results.jsonl"
    if output.exists() and not args.resume:
        raise FileExistsError("출력 폴더가 이미 있습니다. --resume 또는 새 경로를 사용하세요.")
    output.mkdir(parents=True, exist_ok=True)

    if args.resume:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["git"] != git or manifest["artifacts"] != _artifact_hashes():
            raise RuntimeError("체크포인트 이후 코드·데이터·프로토콜이 변경되어 재개할 수 없습니다.")
        _capture_required_model_identity(expected=manifest["model_identity"])
    else:
        ready, _ = llm_module.probe(timeout=5)
        if not ready:
            raise RuntimeError("Ollama 27B preflight에 실패했습니다.")
        # RunPod HTTP proxy의 cold TLS/proxy 연결은 5초를 넘길 수 있다.
        # 생성 전 메타데이터 조회는 모델 호출이 아니므로 충분한 연결
        # 시간을 주고 digest만 엄격히 검증한다.
        model_identity = _capture_required_model_identity()
        manifest = {
            "schema_version": 1,
            "complete": False,
            "started_at": _now(),
            "dataset": "holdout-v2-ho30-20260918",
            "input_sha256": streaming_sha256(QUESTIONS),
            "contract_sha256": streaming_sha256(CONTRACT),
            "protocol_sha256": streaming_sha256(PROTOCOL),
            "git": git,
            "model_identity": model_identity,
            "settings": {
                "model": llm_module.LLM_MODEL,
                "temperature": llm_module.LLM_TEMPERATURE,
                "max_tokens": llm_module.LLM_MAX_TOKENS,
                "context_tokens": llm_module.LLM_NUM_CTX,
                "repeat_penalty": llm_module.LLM_REPEAT_PENALTY,
                "think_off": llm_module.THINK_OFF,
                "law_budget": DEFAULT_K_LAW,
                "case_budget": DEFAULT_K_CASE,
                "guide_budget": DEFAULT_K_GUIDE,
            },
            "artifacts": _artifact_hashes(),
        }
        _json_dump(manifest_path, manifest)

    service = _build_service()
    done = _completed(results_path)
    pending = [row for row in questions if row["id"] not in done]
    if args.limit:
        pending = pending[: args.limit]

    with results_path.open("a", encoding="utf-8") as stream:
        for row in pending:
            capture = CapturingService(service)
            started = time.perf_counter()
            error = None
            try:
                answer = answer_question(
                    row["question"],
                    service=capture,
                    k_law=DEFAULT_K_LAW,
                    k_case=DEFAULT_K_CASE,
                    k_guide=DEFAULT_K_GUIDE,
                )
                answer_record = {
                    "status": answer.status,
                    "text": answer.text,
                    "raw_text": answer.raw_text,
                    "validation_mode": answer.validation_mode,
                    "validation_codes": list(answer.validation_codes),
                    "repair_attempts": answer.repair_attempts,
                    "generation_evidence": [_evidence(item) for item in answer.evidences],
                }
            except Exception as exception:
                error = type(exception).__name__
                answer_record = {"status": "generation_error", "text": "", "raw_text": "", "validation_mode": "not_applicable", "validation_codes": [], "repair_attempts": 0, "generation_evidence": []}
            record = {
                "id": row["id"],
                "question": row["question"],
                "answer": answer_record,
                "retrieval_calls": capture.calls,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "execution_error": error,
                "completed_at": _now(),
            }
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            print(f"[{len(done) + 1}/30] {row['id']} {answer_record['status']}", flush=True)
            done.add(row["id"])

    if len(done) == 30:
        try:
            _capture_required_model_identity(expected=manifest["model_identity"])
        except RuntimeError:
            manifest["model_unchanged"] = False
            _json_dump(manifest_path, manifest)
            raise
        manifest["model_unchanged"] = True
        manifest["complete"] = True
        manifest["completed_at"] = _now()
        manifest["result_count"] = 30
        manifest["results_sha256"] = streaming_sha256(results_path)
        _json_dump(manifest_path, manifest)
    print(f"manifest: {manifest_path}")
    print(f"results: {results_path}")


if __name__ == "__main__":
    main()
