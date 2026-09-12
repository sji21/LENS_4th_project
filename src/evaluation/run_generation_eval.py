"""현재 dev 평가셋을 실제 Retrieval + Qwen runtime으로 평가한다.

기존 ``src.evaluation.run_eval``은 검색기만 평가한다. 이 실행기는
운영 웹과 동일한 ``src.generation.graph.answer_question``으로 단일 질문의 생성
흐름을 평가한다. 웹 세션·멀티턴·업로드 UI 평가는 포함하지 않는다.
생성 모듈을 가져오기 전에 프로젝트 ``.env``를 로드하므로 Django를
거치지 않고 명령행에서 실행해도 동일한 LLM 설정을 사용한다.

기본 실행:
    python -m src.evaluation.run_generation_eval

한 문항만 먼저 확인:
    python -m src.evaluation.run_generation_eval --qid dev-024

출력:
    data/eval/runs/generation/<run_id>.json
    data/eval/runs/generation/<run_id>.csv
    data/eval/runs/generation/<run_id>.jsonl   # 문항별 즉시 저장(checkpoint)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from src.environment import load_project_environment

# src.generation.llm은 import 시점에 환경 변수를 읽으므로 반드시 생성 모듈보다
# 먼저 로드한다. 셸에서 명시한 값은 load_project_environment()가 덮어쓰지 않는다.
load_project_environment()

from src.generation.chain import get_default_service  # noqa: E402
from src.generation.graph import answer_question  # noqa: E402
from src.generation.models import Answer
from src.retrieval.retriever import load_chunks
from src.retrieval.service import CASE_CHUNKS, GUIDE_CHUNKS, LAW_CHUNKS

DEFAULT_EVAL_SET = Path("data/eval/dev.jsonl")
DEFAULT_OUTPUT_DIR = Path("data/eval/runs/generation")
RUNNER_NAME = "langgraph"
RUNNER_VERSION = 1

# --resume가 코드 변경 전 결과를 코드 변경 후 실행과 섞지 않도록, 생성 결과에
# 영향을 주는 핵심 모듈의 내용을 해시해 checkpoint 행마다 함께 남긴다.
# 버전이 다른 행은 재사용하지 않고 다시 평가한다.
_VERSION_SOURCE_FILES = (
    Path("src/generation/graph.py"),
    Path("src/generation/chain.py"),
    Path("src/generation/prompt.py"),
    Path("src/generation/llm.py"),
    Path("src/generation/validation.py"),
    Path("src/generation/abstention.py"),
    Path("src/generation/citation.py"),
    Path("src/retrieval/service.py"),
    Path("src/retrieval/hybrid.py"),
    Path("src/retrieval/dense.py"),
    Path("src/security/secret_filter.py"),
    Path("src/security/prompt_injection.py"),
)


def compute_code_version() -> str:
    """생성·검색·보안 핵심 모듈의 내용으로 짧은 버전 해시를 만든다.

    git 커밋 해시가 아니라 파일 내용 자체를 해시하므로 커밋하지 않은
    변경도 감지한다. 이 해시가 --resume 재사용 여부를 가르는 유일한
    기준이다.
    """
    hasher = hashlib.sha256()
    for path in _VERSION_SOURCE_FILES:
        try:
            hasher.update(path.read_bytes())
        except OSError:
            # 파일이 없으면 경로 자체를 해시에 포함해 버전이 달라지게 한다.
            hasher.update(f"MISSING:{path}".encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()[:12]


@dataclass
class EvalRow:
    qid: str
    question: str
    expected_type: str
    expected_status: str
    actual_status: str
    status_match: bool
    category: str
    difficulty: str
    gold_articles: list[str]
    retrieved_articles: list[str]
    retrieved_gold_articles: list[str]
    gold_recall: float | None
    retrieval_mode: str
    final_answer: str
    source_labels: list[str]
    source_chunk_ids: list[str]
    elapsed_seconds: float
    failure_stage: str
    manual_correct: str = ""
    manual_note: str = ""
    validation_mode: str = "not_applicable"
    code_version: str = ""


def load_questions(path: Path) -> list[dict]:
    questions: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    return questions


def expected_status(answer_type: str) -> str:
    mapping = {
        "answerable": "answered",
        "unanswerable": "abstained",
        "out_of_scope": "refused",
    }
    return mapping.get(answer_type, "")


def load_article_map(
    chunk_paths: Iterable[Path] = (LAW_CHUNKS, CASE_CHUNKS, GUIDE_CHUNKS),
) -> dict[str, str]:
    """chunk_id -> 평가셋에서 사용하는 article_id."""
    mapping: dict[str, str] = {}
    for path in chunk_paths:
        path = Path(path)
        if not path.exists():
            continue
        for chunk in load_chunks(path):
            article_id = str(chunk.get("metadata", {}).get("article_id", "")).strip()
            if article_id:
                mapping[str(chunk["chunk_id"])] = article_id
    return mapping


def article_ids(answer: Answer, chunk_to_article: dict[str, str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for evidence in answer.evidences:
        article_id = chunk_to_article.get(evidence.chunk_id)
        if not article_id:
            # guide는 chunk_id가 article_id#N 형태라 안전하게 보조 추출한다.
            if evidence.chunk_id.startswith("guide-") and "#" in evidence.chunk_id:
                article_id = evidence.chunk_id.rsplit("#", 1)[0]
            else:
                article_id = evidence.chunk_id
        if article_id not in seen:
            seen.add(article_id)
            out.append(article_id)
    return out


def gold_recall(gold: list[str], retrieved: list[str]) -> float | None:
    if not gold:
        return None
    found = set(gold) & set(retrieved)
    return len(found) / len(set(gold))


def infer_failure_stage(answer: Answer) -> str:
    """별도 내부 상태를 추가하지 않고 현재 Answer만으로 실패 단계를 요약한다."""
    if answer.status == "answered":
        return ""
    if answer.status == "refused":
        return "precheck"

    text = answer.text
    if "검색 근거와 일치하는지 충분히 확인하지 못해" in text:
        return "validation"
    if "답변을 생성하지 못했습니다" in text:
        return "generation"
    if not answer.evidences:
        return "retrieval"
    return "abstain_unknown"


def evaluate_one(
    question: dict,
    service,
    chunk_to_article: dict[str, str],
    code_version: str,
) -> EvalRow:
    started = time.perf_counter()
    answer = answer_question(
        question["question"],
        service=service,
    )
    elapsed = time.perf_counter() - started

    retrieved = article_ids(answer, chunk_to_article)
    gold = list(question.get("gold_articles", []))
    found_gold = [article for article in gold if article in set(retrieved)]
    target_status = expected_status(question.get("answer_type", ""))

    return EvalRow(
        qid=question["qid"],
        question=question["question"],
        expected_type=question.get("answer_type", ""),
        expected_status=target_status,
        actual_status=answer.status,
        status_match=answer.status == target_status,
        category=question.get("category", ""),
        difficulty=question.get("difficulty", ""),
        gold_articles=gold,
        retrieved_articles=retrieved,
        retrieved_gold_articles=found_gold,
        gold_recall=gold_recall(gold, retrieved),
        retrieval_mode="hybrid" if getattr(service, "dense", None) is not None else "lexical_only",
        final_answer=answer.text,
        source_labels=[source["label"] for source in answer.sources()],
        source_chunk_ids=[source["chunk_id"] for source in answer.sources()],
        elapsed_seconds=round(elapsed, 3),
        failure_stage=infer_failure_stage(answer),
        validation_mode=answer.validation_mode,
        code_version=code_version,
    )


def select_questions(
    questions: list[dict],
    *,
    qids: set[str],
    limit: int | None,
) -> list[dict]:
    selected = questions
    if qids:
        selected = [q for q in selected if q["qid"] in qids]
        missing = qids - {q["qid"] for q in selected}
        if missing:
            raise ValueError(f"평가셋에 없는 qid: {', '.join(sorted(missing))}")
    if limit is not None:
        selected = selected[:limit]
    return selected


def load_completed(
    checkpoint_path: Path, code_version: str
) -> dict[str, EvalRow]:
    """현재 코드 버전과 일치하는 완료 행만 재사용 대상으로 돌려준다.

    버전이 없거나(과거 checkpoint) 다른 행은 코드가 바뀐 뒤 결과가
    달라졌을 수 있으므로 반환하지 않는다. main()의 실행 루프는 이렇게
    걸러진 qid를 다시 평가한다.
    """
    if not checkpoint_path.exists():
        return {}
    rows: dict[str, EvalRow] = {}
    stale = 0
    with checkpoint_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                payload = json.loads(line)
                row = EvalRow(**payload)
                if row.code_version != code_version:
                    stale += 1
                    continue
                rows[payload["qid"]] = row
    if stale:
        print(
            f"checkpoint 중 {stale}개 문항은 코드 버전이 달라 재평가합니다."
        )
    return rows


def append_checkpoint(path: Path, row: EvalRow) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")


def summarize(rows: list[EvalRow]) -> dict:
    total = len(rows)
    status_matches = sum(row.status_match for row in rows)
    answerable = [row for row in rows if row.expected_type == "answerable"]
    refused_targets = [row for row in rows if row.expected_type == "out_of_scope"]

    retrieval_full = [
        row for row in answerable if row.gold_recall is not None and row.gold_recall == 1.0
    ]
    answered = [row for row in answerable if row.actual_status == "answered"]
    false_abstain = [row for row in answerable if row.actual_status == "abstained"]
    wrong_refuse = [row for row in answerable if row.actual_status == "refused"]
    correct_refuse = [row for row in refused_targets if row.actual_status == "refused"]

    elapsed = [row.elapsed_seconds for row in rows]
    semantic_rows = [row for row in rows if row.validation_mode == "semantic"]
    deterministic_rows = [
        row for row in rows if row.validation_mode == "deterministic"
    ]

    return {
        "n": total,
        "status_accuracy": round(status_matches / total, 4) if total else 0.0,
        "answerable_n": len(answerable),
        "answer_rate_on_answerable": (
            round(len(answered) / len(answerable), 4) if answerable else None
        ),
        "false_abstain_n": len(false_abstain),
        "wrong_refuse_n": len(wrong_refuse),
        "out_of_scope_n": len(refused_targets),
        "correct_refuse_n": len(correct_refuse),
        "full_gold_retrieval_n": len(retrieval_full),
        "full_gold_retrieval_rate": (
            round(len(retrieval_full) / len(answerable), 4) if answerable else None
        ),
        "mean_elapsed_seconds": (
            round(sum(elapsed) / len(elapsed), 3) if elapsed else 0.0
        ),
        "semantic_validation_n": len(semantic_rows),
        "deterministic_validation_n": len(deterministic_rows),
        "failures": [
            {
                "qid": row.qid,
                "expected": row.expected_status,
                "actual": row.actual_status,
                "stage": row.failure_stage,
                "gold_recall": row.gold_recall,
            }
            for row in rows
            if not row.status_match
            or (row.expected_type == "answerable" and row.gold_recall != 1.0)
        ],
    }


def write_csv(path: Path, rows: list[EvalRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(EvalRow.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            payload = asdict(row)
            for key in (
                "gold_articles",
                "retrieved_articles",
                "retrieved_gold_articles",
                "source_labels",
                "source_chunk_ids",
            ):
                payload[key] = " | ".join(payload[key])
            writer.writerow(payload)


def print_row(row: EvalRow) -> None:
    retrieval = (
        "-"
        if row.gold_recall is None
        else f"{row.gold_recall:.0%}"
    )
    status_flag = "PASS" if row.status_match else "FAIL"
    print(
        f"[{row.qid}] {status_flag} "
        f"expected={row.expected_status} actual={row.actual_status} "
        f"gold_recall={retrieval} time={row.elapsed_seconds:.1f}s"
        f" validation={row.validation_mode}"
    )
    if row.failure_stage:
        print(f"  stage={row.failure_stage}")
    if row.retrieved_articles:
        print(f"  retrieved={', '.join(row.retrieved_articles)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="실제 Retrieval + Qwen dev E2E 평가")
    parser.add_argument("--eval-set", type=Path, default=DEFAULT_EVAL_SET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--run-id",
        default=None,
        help="출력 파일 이름. 생략하면 현재 시각으로 자동 생성",
    )
    parser.add_argument(
        "--qid",
        action="append",
        default=[],
        help="특정 qid만 실행. 여러 번 지정 가능: --qid dev-024 --qid dev-026",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "같은 run-id의 jsonl checkpoint가 있으면 완료된 qid를 건너뜀. "
            "단, 생성·검색·보안 핵심 모듈이 바뀌어 코드 버전이 달라졌으면 "
            "그 qid는 checkpoint와 무관하게 다시 평가함"
        ),
    )
    args = parser.parse_args()

    run_id = args.run_id or datetime.now().strftime("generation-e2e-%Y%m%d-%H%M%S")
    output_dir = args.output_dir
    json_path = output_dir / f"{run_id}.json"
    csv_path = output_dir / f"{run_id}.csv"
    checkpoint_path = output_dir / f"{run_id}.jsonl"

    questions = select_questions(
        load_questions(args.eval_set),
        qids=set(args.qid),
        limit=args.limit,
    )

    code_version = compute_code_version()
    print("Retrieval service 준비 중...")
    service = get_default_service()
    mode = "hybrid" if getattr(service, "dense", None) is not None else "lexical_only"
    print(f"retrieval_mode={mode}")
    print(f"questions={len(questions)} mode=integrated-main-prompt")
    print()

    chunk_to_article = load_article_map()
    completed = (
        load_completed(checkpoint_path, code_version) if args.resume else {}
    )
    if not args.resume and checkpoint_path.exists():
        checkpoint_path.unlink()
    rows: list[EvalRow] = []

    for index, question in enumerate(questions, start=1):
        qid = question["qid"]
        if qid in completed:
            row = completed[qid]
            rows.append(row)
            print(f"[{qid}] RESUME 기존 결과 사용")
            continue

        print(f"({index}/{len(questions)}) {qid} 실행...")
        try:
            row = evaluate_one(
                question,
                service,
                chunk_to_article,
                code_version,
            )
        except KeyboardInterrupt:
            print("\n사용자 중단. 완료된 문항은 jsonl checkpoint에 남아 있습니다.")
            raise
        except Exception as error:
            # 한 문항의 예외 때문에 나머지 26개가 사라지지 않게 기록한다.
            row = EvalRow(
                qid=qid,
                question=question["question"],
                expected_type=question.get("answer_type", ""),
                expected_status=expected_status(question.get("answer_type", "")),
                actual_status="error",
                status_match=False,
                category=question.get("category", ""),
                difficulty=question.get("difficulty", ""),
                gold_articles=list(question.get("gold_articles", [])),
                retrieved_articles=[],
                retrieved_gold_articles=[],
                gold_recall=None,
                retrieval_mode=mode,
                final_answer="",
                source_labels=[],
                source_chunk_ids=[],
                elapsed_seconds=0.0,
                failure_stage="exception",
                manual_note=f"{type(error).__name__}: {error}",
                code_version=code_version,
            )
        rows.append(row)
        append_checkpoint(checkpoint_path, row)
        print_row(row)
        print()

    summary = summarize(rows)
    payload = {
        "run_id": run_id,
        "eval_set": str(args.eval_set),
        "runner": {"name": RUNNER_NAME, "version": RUNNER_VERSION},
        "generation_mode": "integrated-main-prompt",
        "retrieval_mode": mode,
        "summary": summary,
        "per_question": [asdict(row) for row in rows],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(csv_path, rows)

    print("=" * 72)
    print(f"status_accuracy          {summary['status_accuracy']:.1%}")
    if summary["answer_rate_on_answerable"] is not None:
        print(f"answer_rate(answerable)  {summary['answer_rate_on_answerable']:.1%}")
    print(f"false_abstain            {summary['false_abstain_n']}")
    print(f"wrong_refuse             {summary['wrong_refuse_n']}")
    print(
        f"correct_refuse           "
        f"{summary['correct_refuse_n']}/{summary['out_of_scope_n']}"
    )
    if summary["full_gold_retrieval_rate"] is not None:
        print(f"full_gold_retrieval      {summary['full_gold_retrieval_rate']:.1%}")
    print(f"mean_elapsed             {summary['mean_elapsed_seconds']:.1f}s")
    print(f"semantic_validation      {summary['semantic_validation_n']}")
    print(f"deterministic_validation {summary['deterministic_validation_n']}")
    print(f"JSON  {json_path}")
    print(f"CSV   {csv_path}")
    print(f"JSONL {checkpoint_path}")
    print()
    print("※ 자동 지표는 상태/검색 근거 중심입니다. 최종 법률 답변의 정답성은")
    print("   CSV의 manual_correct/manual_note 열로 문항별 수동 확인하세요.")


if __name__ == "__main__":
    main()
