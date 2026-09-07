"""PATCH-018 판례 청크를 Hugging Face Inference Providers로 비교한다.

``HF_TOKEN``은 요청 헤더에만 사용하며 결과 파일과 콘솔에 기록하지 않는다.
운영용 통합 Chroma 인덱스는 건드리지 않고 모델별 실험 컬렉션만 만든다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import chromadb


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.load_case_only_demo_corpus import SOURCES
from src.evaluation.case_only import (
    CASE_ANSWERABLE_QUESTION_IDS,
    DEFAULT_CASE_CHUNKS,
    DEFAULT_EVAL_SET,
    make_abstain_row,
    load_case_chunks,
    load_case_questions,
    metrics,
    rank_of,
    source_ids_for_question,
)
from src.retrieval.index import clean_metadata


ROUTER_BASE = "https://router.huggingface.co/hf-inference/models"
PROVIDER = "hf-inference"
REQUEST_TIMEOUT_SECONDS = 120
DEFAULT_CHROMA = ROOT / "data" / "index" / "chroma_case_hf_api_comparison"
DEFAULT_JSON = ROOT / "data" / "eval" / "runs" / "housing_cases_hf_api_comparison.json"
DEFAULT_MARKDOWN = ROOT / "data" / "eval" / "runs" / "housing_cases_hf_api_comparison.md"
QWEN_QUERY_INSTRUCTION = "Given a Korean housing lease question, retrieve relevant Supreme Court case passages that answer the question."


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    model_id: str
    query_instruction: str | None = None


MODELS = (
    ModelSpec("qwen3_embedding_4b", "Qwen3 Embedding 4B", "Qwen/Qwen3-Embedding-4B", QWEN_QUERY_INSTRUCTION),
    ModelSpec("kure_v1", "KURE-v1", "nlpai-lab/KURE-v1"),
    ModelSpec("bge_m3", "BGE-M3", "BAAI/bge-m3"),
)


def load_hf_token() -> str:
    token = os.getenv("HF_TOKEN", "").strip()
    if token:
        return token
    env_path = ROOT / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*HF_TOKEN\s*=\s*(.+)$", raw)
            if match and match.group(1).strip().strip("\"'"):
                return match.group(1).strip().strip("\"'")
    raise RuntimeError("HF_TOKEN이 .env 또는 환경 변수에 없습니다.")


def error_summary(error: Exception) -> str:
    if isinstance(error, HTTPError) and error.code in {401, 403}:
        return f"HTTP {error.code}: Hugging Face Inference Providers 인증 또는 권한이 없습니다."
    return str(error).replace("\n", " ")[:500]


def request_embeddings(model_id: str, texts: list[str], token: str) -> tuple[list[list[float]], float]:
    request = Request(
        f"{ROUTER_BASE}/{model_id}",
        data=json.dumps({"inputs": texts}, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    vectors = decoded if decoded and isinstance(decoded[0], list) else [decoded]
    if len(vectors) != len(texts) or not vectors or not all(vectors):
        raise ValueError("임베딩 응답의 벡터 수 또는 형식이 요청과 일치하지 않습니다.")
    dimension = len(vectors[0])
    if not dimension or any(len(vector) != dimension for vector in vectors):
        raise ValueError("임베딩 차원이 비어 있거나 일관되지 않습니다.")
    return [[float(value) for value in vector] for vector in vectors], time.perf_counter() - started


def embed_batches(
    spec: ModelSpec, texts: list[str], token: str, *, query: bool
) -> tuple[list[list[float]], list[float]]:
    prepared = [
        f"{spec.query_instruction}\n\n{text}" if query and spec.query_instruction else text
        for text in texts
    ]
    vectors: list[list[float]] = []
    elapsed: list[float] = []
    for start in range(0, len(prepared), 8):
        batch, seconds = request_embeddings(spec.model_id, prepared[start : start + 8], token)
        vectors.extend(batch)
        elapsed.append(seconds)
    return vectors, elapsed


def evaluate_model(
    spec: ModelSpec, token: str, chunks: list[dict[str, Any]], questions: list[dict[str, Any]], chroma_path: Path
) -> dict[str, Any]:
    document_vectors, document_latency = embed_batches(spec, [chunk["text"] for chunk in chunks], token, query=False)
    query_vectors, query_latency = embed_batches(spec, [str(item["question"]) for item in questions], token, query=True)
    client = chromadb.PersistentClient(path=str(chroma_path))
    name = f"case_hf_{spec.key}"
    try:
        client.delete_collection(name)
    except ValueError:
        pass
    collection = client.create_collection(
        name,
        metadata={
            "hnsw:space": "cosine",
            "embedding_model": spec.model_id,
            "provider": PROVIDER,
            "dimension": len(document_vectors[0]),
            "input_schema": "patch-018-cases-jsonl",
        },
    )
    collection.add(
        ids=[chunk["chunk_id"] for chunk in chunks],
        documents=[chunk["text"] for chunk in chunks],
        embeddings=document_vectors,
        metadatas=[clean_metadata(chunk["metadata"]) for chunk in chunks],
    )
    rows: list[dict[str, Any]] = []
    search_seconds = 0.0
    for question_id, (item, vector) in enumerate(zip(questions, query_vectors), start=1):
        question = str(item["question"])
        if question_id not in CASE_ANSWERABLE_QUESTION_IDS:
            rows.append(make_abstain_row(question_id, question))
            continue
        started = time.perf_counter()
        response = collection.query(query_embeddings=[vector], n_results=min(5, len(chunks)), include=["metadatas", "distances"], where={"doc_type": "case"})
        search_seconds += time.perf_counter() - started
        top5 = [
            {"rank": rank, "chunk_id": cid, "source_id": metadata["case_id"], "case_number": metadata["case_number"], "distance": round(float(distance), 6)}
            for rank, (cid, metadata, distance) in enumerate(zip(response["ids"][0], response["metadatas"][0], response["distances"][0]), start=1)
        ]
        expected_source_ids = source_ids_for_question(SOURCES, question_id)
        rows.append({"question_id": question_id, "question": question, "expected": "answer", "decision": "answer", "expected_source_ids": sorted(expected_source_ids), "rank": rank_of(top5, expected_source_ids), "top5": top5})
    all_latency = document_latency + query_latency
    return {
        "status": "success", "model_id": spec.model_id,
        "embedding_dimension": len(document_vectors[0]), "vector_count": collection.count(),
        "document_api_calls": len(document_latency), "query_api_calls": len(query_latency),
        "mean_api_call_milliseconds": round(sum(all_latency) * 1000 / len(all_latency), 3),
        "mean_chroma_search_milliseconds": round(search_seconds * 1000 / len(CASE_ANSWERABLE_QUESTION_IDS), 3),
        "metrics": metrics(rows), "results": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# PATCH-018 판례 청크 Hugging Face 임베딩 비교", "",
        "- 입력: `data/chunks/cases.jsonl` (공통 SQLite에서 추출한 표준 판례 청크)",
        "- 인증 토큰은 요청 헤더에만 사용하며 결과 파일에 기록하지 않음", "",
        "| 모델 | API 접근 | 차원 | Hit@1 | Hit@3 | Hit@5 | MRR | 평균 API 호출(ms) | 실패 사유 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["models"]:
        result = row.get("result", {})
        if result.get("status") == "success":
            score = result["metrics"]
            lines.append(f"| {row['label']} | 성공 | {result['embedding_dimension']} | {score['hit_at_1']:.2%} | {score['hit_at_3']:.2%} | {score['hit_at_5']:.2%} | {score['mrr']:.4f} | {result['mean_api_call_milliseconds']:.3f} | - |")
        else:
            lines.append(f"| {row['label']} | 실패 | - | - | - | - | - | - | {result.get('error', '사전 검증 실패')} |")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PATCH-018 판례 청크를 HF API 임베딩으로 비교합니다.")
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CASE_CHUNKS)
    parser.add_argument("--eval-set", type=Path, default=DEFAULT_EVAL_SET)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-report", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--preflight-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = load_hf_token()
    chunks = load_case_chunks(args.chunks)
    questions = load_case_questions(args.eval_set)
    models: list[dict[str, Any]] = []
    for spec in MODELS:
        row: dict[str, Any] = {"label": spec.label, "model_id": spec.model_id}
        try:
            vectors, elapsed = request_embeddings(spec.model_id, ["주택 임대차 판례 검색 확인"], token)
            row["preflight"] = {"available": True, "dimension": len(vectors[0]), "latency_seconds": round(elapsed, 3)}
            row["result"] = {"status": "preflight_only"} if args.preflight_only else evaluate_model(spec, token, chunks, questions, args.chroma_path)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            row["preflight"] = {"available": False}
            row["result"] = {"status": "failed", "error": error_summary(error)}
        models.append(row)
    report = {"provider": PROVIDER, "input_schema": "patch-018-cases-jsonl", "chunks": str(args.chunks), "case_chunk_count": len(chunks), "preflight_only": args.preflight_only, "models": models}
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_report.write_text(render_markdown(report), encoding="utf-8")
    print(f"JSON report: {args.json_report.resolve()}")
    print(f"Markdown report: {args.markdown_report.resolve()}")


if __name__ == "__main__":
    main()
