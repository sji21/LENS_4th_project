"""Pass saved retrieval evidence to the existing Qwen generation chain.

Run after case_corpus_query.py exits so the embedding model can be released.
This is a connection smoke test, not the application's legal-quality gate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def result_from_payload(payload):
    from src.retrieval.service import Evidence, RetrievalResult
    if (payload.get("schema") != "lens-retrieval-evidence-v1"
            or not isinstance(payload.get("question"), str)
            or not payload["question"].strip()
            or not payload.get("profile_version")
            or not payload.get("channels", {}).get("cases")):
        raise ValueError("판례 검색 결과와 프로필 버전이 있는 근거 JSON이 필요합니다.")
    channels = {}
    for name in ("laws", "cases", "guides", "civil_laws"):
        rows = payload["channels"].get(name, [])
        channels[name] = [Evidence(**{key: row[key] for key in
            ("rank", "chunk_id", "doc_type", "citation", "text", "score", "source_url")})
            for row in rows]
    return RetrievalResult(question=payload["question"], **channels)


def generate(payload, llm=None):
    from src.generation.chain import build_qa_chain
    from src.generation.prompt import format_context
    result = result_from_payload(payload)
    context = format_context(result)
    answer = build_qa_chain(llm).invoke({"question": result.question, "context": context})
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("생성 체인에서 빈 답변을 반환했습니다.")
    return {"schema": "lens-case-llm-smoke-v1", "profile_version": payload["profile_version"],
            "question": result.question, "answer": answer,
            "context_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest(),
            "evidence": payload, "scope": "retrieval-evidence-to-existing-qa-chain",
            "application_quality_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="실제 검색 근거 → 기존 Qwen 생성 체인 연결 검사")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("기존 출력 파일을 덮어쓰지 않습니다.")
    from src.generation.llm import get_llm, LLM_MODEL
    raw = args.evidence.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    # Keep the existing model/prompt contract; no fake or smaller-model fallback.
    result = generate(payload, get_llm(allow_route_fallback=False))
    result.update(model=LLM_MODEL, evidence_sha256=hashlib.sha256(raw).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
