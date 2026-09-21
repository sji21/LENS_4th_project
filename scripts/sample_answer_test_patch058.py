"""PATCH-058 표본 문항을 실제 Generation Graph로 실행해 사람이 검토할 자료를 만든다.

자동 검증 통과는 법률 정답 판정이 아니다. 이 스크립트는 응답, 출처 앵커,
활성 MySQL knowledge release와 실행 조건을 함께 저장해 사람의 비교 채점을 돕는다.

예시:
  python scripts/sample_answer_test_patch058.py \
    --base-url https://<POD_ID>-11434.proxy.runpod.net \
    --model qwen3.8:27b --dataset dev100 --count 10 --seed 936592 --no-think
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import sys
import time
import traceback
import unicodedata
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEV100 = ROOT / "data/eval/dev100-v2"
HOLDOUT = ROOT / "data/eval/holdout-v2-ho30-20260918"
CHUNKS = ROOT / "data/chunks/chunks.jsonl"


def _normalise(value: object) -> str:
    if value is None:
        return ""
    return "".join(unicodedata.normalize("NFC", str(value)).split())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_get(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _native_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/api/{endpoint.lstrip('/')}"


def _require_model(base_url: str, model: str) -> list[str]:
    try:
        payload = _json_get(_native_url(base_url, "tags"))
    except Exception as error:
        raise RuntimeError(f"Ollama 서버에 연결할 수 없습니다: {base_url} ({error})") from error
    names = [item.get("name", "") for item in payload.get("models", [])]
    if model not in names:
        raise RuntimeError(
            f"지정 모델 {model!r}이 서버에 없습니다. 사용 가능 모델: {', '.join(names) or '(없음)'}"
        )
    return names


def _active_release() -> dict[str, object]:
    active_path = ROOT / "data/mysql-search/active.json"
    metadata: dict[str, object] = {"active_path": str(active_path), "release_path": None,
                                   "release_id": None, "release_sha256": None}
    if not active_path.exists():
        return metadata
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
        metadata["release_sha256"] = active.get("sha256")
        release_path = Path(active["release"])
        metadata["release_path"] = str(release_path)
        if release_path.exists():
            metadata["release_id"] = json.loads(
                release_path.read_text(encoding="utf-8")
            ).get("release_id")
    except (KeyError, OSError, ValueError, TypeError):
        pass
    return metadata


def _corpus_anchors() -> set[str]:
    """현재 MySQL release export를 우선해 앵커 존재 여부만 진단한다."""
    paths: list[Path] = []
    release = _active_release().get("release_path")
    if isinstance(release, str):
        root = Path(release).parent
        paths.extend((root / "exports/base/laws.jsonl", root / "exports/civil/civil.jsonl"))
    if not any(path.exists() for path in paths):
        paths = [CHUNKS]

    anchors: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                article_id = json.loads(line).get("metadata", {}).get("article_id")
            except json.JSONDecodeError:
                continue
            if article_id:
                anchors.add(_normalise(article_id))
    return anchors


def _dev100_items() -> list[dict[str, object]]:
    questions = json.loads((DEV100 / "questions.json").read_text(encoding="utf-8"))
    diagnostic = json.loads((DEV100 / "diagnostic-plan.json").read_text(encoding="utf-8"))
    targets = {
        item["qid"]: [_normalise(target["article_anchor"]) for target in item.get("law_targets", [])]
        for item in diagnostic
    }
    return [
        {
            "dataset": "dev100",
            "qid": item["qid"],
            "question": item["modes"]["question_only"]["query"],
            # 채점 기준은 실행 전 모델에 전달하지 않는다. 사람이 결과를 읽을 때만 쓴다.
            "required_anchors": targets.get(item["qid"], []),
        }
        for item in questions
    ]


def _holdout_items() -> list[dict[str, object]]:
    contract_path = HOLDOUT / "evaluation-contract.json"
    if not contract_path.exists():
        return []
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    return [
        {
            "dataset": "holdout",
            "qid": item["id"],
            "question": item["question"],
            "required_anchors": [],
        }
        for item in (
            json.loads(line)
            for line in (HOLDOUT / contract["input_file"]).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    ]


def _evidence_anchor(evidence) -> str:
    citation = getattr(evidence, "citation", "") or ""
    return _normalise(citation)


def _answer_row(answer, elapsed: float) -> dict[str, object]:
    evidences = tuple(getattr(answer, "evidences", ()) or ())
    return {
        "status": answer.status,
        "answer": answer.text,
        "raw_text": answer.raw_text,
        "validation_mode": answer.validation_mode,
        "validation_codes": list(answer.validation_codes),
        "repair_attempts": answer.repair_attempts,
        "refusal_reason": answer.refusal_reason,
        "source_chunk_ids": [item.chunk_id for item in evidences],
        "anchors": [anchor for item in evidences if (anchor := _evidence_anchor(item))],
        "elapsed_seconds": round(elapsed, 3),
    }


def _write_markdown(rows: list[dict[str, object]], output: Path, config: dict[str, object]) -> None:
    counts = Counter(row["status"] for row in rows)
    lines = [
        f"# PATCH-058 표본 답변 점검 ({datetime.now():%Y-%m-%d %H:%M})",
        "",
        f"모델: `{config['model']}`  ",
        f"서버: `{config['base_url']}`  ",
        f"release: `{config['mysql_release'].get('release_id') or '미확인'}`",
        "",
        "`answered`는 자동 검증 통과일 뿐 법률 정답 판정이 아닙니다.",
        "필수 앵커와 답변의 주장·조건·예외를 사람이 직접 대조하세요.",
        "",
        "## 상태 요약",
        "",
        *[f"- {status}: {count}" for status, count in sorted(counts.items())],
        "",
    ]
    for row in rows:
        lines.extend((
            f"## {row['qid']} · {row['status']}",
            "",
            f"질문: {row['question']}",
            "",
            f"검증: {row['validation_mode']} / codes={row['validation_codes']} / repair={row['repair_attempts']}",
            "",
            "답변:",
            "",
            str(row["answer"]),
            "",
            f"필수 앵커: {', '.join(row['required_anchors']) or '(홀드아웃은 결과 파일에서 별도 검토)'}",
            f"검색 출처: {', '.join(row['anchors']) or '(없음)'}",
            "",
        ))
    (output / "answers.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset", choices=("dev100", "holdout", "both"), default="dev100")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=936592)
    parser.add_argument("--no-think", action="store_true")
    parser.add_argument("--qid", action="append", default=[])
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/sample-answer-patch058")
    args = parser.parse_args(argv)
    if args.count <= 0:
        parser.error("--count는 1 이상이어야 합니다.")

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from src.environment import load_project_environment
    load_project_environment(ROOT / ".env")
    os.environ.update({
        "JEONSEON_LLM_BASE_URL": args.base_url,
        "JEONSEON_LLM_MODEL": args.model,
        "JEONSEON_LLM_ROUTE_FALLBACK": "0",
        "LANGSMITH_TRACING": "false",
    })
    if args.no_think:
        os.environ["JEONSEON_LLM_THINK_OFF"] = "1"

    available_models = _require_model(args.base_url, args.model)
    corpus = _corpus_anchors()
    candidates: list[dict[str, object]] = []
    if args.dataset in ("dev100", "both"):
        candidates.extend(_dev100_items())
    if args.dataset in ("holdout", "both"):
        candidates.extend(_holdout_items())
    if args.qid:
        requested = set(args.qid)
        candidates = [item for item in candidates if item["qid"] in requested]
    else:
        random.Random(args.seed).shuffle(candidates)
        candidates = candidates[:args.count]
    if not candidates:
        parser.error("선택한 문항이 없습니다.")

    output = (args.output / datetime.now().strftime("%Y%m%d-%H%M%S")).resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = {
        "schema_version": 1,
        "started_at": _utc_now(),
        "base_url": args.base_url,
        "model": args.model,
        "no_think": args.no_think,
        "dataset": args.dataset,
        "count": len(candidates),
        "seed": args.seed,
        "available_models": available_models,
        "mysql_release": _active_release(),
    }
    (output / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    from src.generation.graph import answer_question
    rows: list[dict[str, object]] = []
    with (output / "results.jsonl").open("w", encoding="utf-8") as stream:
        for index, item in enumerate(candidates, 1):
            started = time.perf_counter()
            try:
                row = _answer_row(answer_question(str(item["question"])), time.perf_counter() - started)
            except Exception as error:
                traceback.print_exc()
                row = {"status": "error", "answer": "", "raw_text": "", "validation_mode": "not_applicable",
                       "validation_codes": [type(error).__name__], "repair_attempts": 0, "refusal_reason": "",
                       "source_chunk_ids": [], "anchors": [], "elapsed_seconds": round(time.perf_counter() - started, 3)}
            required = list(item["required_anchors"])
            row.update(item, corpus_missing=[anchor for anchor in required if anchor not in corpus])
            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            print(f"[{index}/{len(candidates)}] {item['qid']} {row['status']}", flush=True)
    _write_markdown(rows, output, config)
    print(f"results: {output / 'results.jsonl'}")
    print(f"report: {output / 'answers.md'}")
    return 1 if any(row["status"] == "error" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
