"""PATCH-018 판례 청크로 로컬 임베딩 모델을 비교한다.

공통 ``cases.jsonl``을 입력으로 쓰되, 모델마다 독립 Chroma 컬렉션을 만들어
차원 충돌 없이 비교한다. 운영용 ``knowledge_chunks`` 컬렉션은 수정하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


DEFAULT_CHROMA = ROOT / "data" / "index" / "chroma_case_embedding_comparison"
DEFAULT_JSON = ROOT / "data" / "eval" / "runs" / "housing_cases_local_embedding_comparison.json"
DEFAULT_MARKDOWN = ROOT / "data" / "eval" / "runs" / "housing_cases_local_embedding_comparison.md"
QUERY_INSTRUCTION = "Given a Korean housing lease question, retrieve relevant Supreme Court case passages that answer the question."


@dataclass(frozen=True)
class ModelSpec:
    key: str
    model_id: str
    label: str
    query_instruction: str | None = None
    trust_remote_code: bool = False


MODELS = (
    ModelSpec("qwen3_embedding_4b", "Qwen/Qwen3-Embedding-4B", "Qwen3 Embedding 4B", QUERY_INSTRUCTION, True),
    ModelSpec("kure_v1", "nlpai-lab/KURE-v1", "KURE-v1"),
    ModelSpec("bge_m3", "BAAI/bge-m3", "BGE-M3"),
)


class LocalEmbedder:
    def __init__(self, spec: ModelSpec, device: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.spec = spec
        self.model = SentenceTransformer(
            spec.model_id, device=device, trust_remote_code=spec.trust_remote_code
        )
        self.model.max_seq_length = 512

    def documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(
            texts, batch_size=8, normalize_embeddings=True, show_progress_bar=False
        ).tolist()

    def queries(self, texts: list[str]) -> list[list[float]]:
        options: dict[str, Any] = {
            "batch_size": 8,
            "normalize_embeddings": True,
            "show_progress_bar": False,
        }
        if self.spec.query_instruction:
            options["prompt"] = self.spec.query_instruction
        return self.model.encode(texts, **options).tolist()


def collection_name(spec: ModelSpec) -> str:
    return f"case_comparison_{spec.key}"


def run_model(
    spec: ModelSpec,
    chunks: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    chroma_path: Path,
    device: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    embedder = LocalEmbedder(spec, device)
    model_load_seconds = time.perf_counter() - started
    texts = [chunk["text"] for chunk in chunks]
    started = time.perf_counter()
    document_vectors = embedder.documents(texts)
    document_embedding_seconds = time.perf_counter() - started
    dimension = len(document_vectors[0])

    client = chromadb.PersistentClient(path=str(chroma_path))
    name = collection_name(spec)
    try:
        client.delete_collection(name)
    except ValueError:
        pass
    collection = client.create_collection(
        name,
        metadata={
            "hnsw:space": "cosine",
            "embedding_model": spec.model_id,
            "dimension": dimension,
            "corpus_scope": "housing_supreme_court_cases_only",
            "input_schema": "patch-018-cases-jsonl",
        },
    )
    collection.add(
        ids=[chunk["chunk_id"] for chunk in chunks],
        documents=texts,
        embeddings=document_vectors,
        metadatas=[clean_metadata(chunk["metadata"]) for chunk in chunks],
    )

    started = time.perf_counter()
    query_vectors = embedder.queries([str(item["question"]) for item in questions])
    query_embedding_seconds = time.perf_counter() - started
    rows: list[dict[str, Any]] = []
    search_seconds = 0.0
    for question_id, (item, vector) in enumerate(zip(questions, query_vectors), start=1):
        question = str(item["question"])
        if question_id not in CASE_ANSWERABLE_QUESTION_IDS:
            rows.append(make_abstain_row(question_id, question))
            continue
        started = time.perf_counter()
        response = collection.query(
            query_embeddings=[vector],
            n_results=min(5, len(chunks)),
            include=["metadatas", "distances"],
            where={"doc_type": "case"},
        )
        search_seconds += time.perf_counter() - started
        top5 = [
            {
                "rank": rank,
                "chunk_id": chunk_id,
                "source_id": metadata["case_id"],
                "case_number": metadata["case_number"],
                "distance": round(float(distance), 6),
            }
            for rank, (chunk_id, metadata, distance) in enumerate(
                zip(response["ids"][0], response["metadatas"][0], response["distances"][0]),
                start=1,
            )
        ]
        expected_source_ids = source_ids_for_question(SOURCES, question_id)
        rows.append(
            {
                "question_id": question_id,
                "question": question,
                "expected": "answer",
                "decision": "answer",
                "expected_source_ids": sorted(expected_source_ids),
                "rank": rank_of(top5, expected_source_ids),
                "top5": top5,
            }
        )
    return {
        "key": spec.key,
        "label": spec.label,
        "model_id": spec.model_id,
        "query_instruction": spec.query_instruction,
        "embedding_dimension": dimension,
        "vector_count": collection.count(),
        "model_load_seconds": round(model_load_seconds, 3),
        "document_embedding_seconds": round(document_embedding_seconds, 3),
        "query_embedding_seconds": round(query_embedding_seconds, 3),
        "mean_search_milliseconds": round(
            search_seconds * 1000 / len(CASE_ANSWERABLE_QUESTION_IDS), 3
        ),
        "metrics": metrics(rows),
        "results": rows,
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# PATCH-018 판례 전용 로컬 임베딩 비교",
        "",
        "- 입력: `data/chunks/cases.jsonl` (공통 SQLite에서 추출한 표준 판례 청크)",
        "- 운영용 통합 Chroma 인덱스는 수정하지 않고, 모델별 실험 컬렉션만 별도 생성",
        "- 평가: 27개 질문 중 판례로 답할 수 있는 13개에서 Hit@k·MRR을 계산",
        "",
        "| 모델 | 차원 | Hit@1 | Hit@3 | Hit@5 | MRR | 문서 임베딩(초) | 질의 임베딩(초) | 검색(ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in report["models"]:
        score = result["metrics"]
        lines.append(
            f"| {result['label']} | {result['embedding_dimension']} | {score['hit_at_1']:.2%} | {score['hit_at_3']:.2%} | {score['hit_at_5']:.2%} | {score['mrr']:.4f} | {result['document_embedding_seconds']:.3f} | {result['query_embedding_seconds']:.3f} | {result['mean_search_milliseconds']:.3f} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PATCH-018 판례 청크 임베딩 모델을 비교합니다.")
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CASE_CHUNKS)
    parser.add_argument("--eval-set", type=Path, default=DEFAULT_EVAL_SET)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--models", nargs="+", choices=[spec.key for spec in MODELS])
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-report", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    chunks = load_case_chunks(args.chunks)
    questions = load_case_questions(args.eval_set)
    selected = [spec for spec in MODELS if not args.models or spec.key in args.models]
    results = [run_model(spec, chunks, questions, args.chroma_path, args.device) for spec in selected]
    report = {
        "input_schema": "patch-018-cases-jsonl",
        "chunks": str(args.chunks),
        "eval_set": str(args.eval_set),
        "chroma_path": str(args.chroma_path),
        "case_chunk_count": len(chunks),
        "answerable_questions": len(CASE_ANSWERABLE_QUESTION_IDS),
        "models": results,
    }
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_report.write_text(markdown_report(report), encoding="utf-8")
    print(f"JSON report: {args.json_report.resolve()}")
    print(f"Markdown report: {args.markdown_report.resolve()}")


if __name__ == "__main__":
    main()
