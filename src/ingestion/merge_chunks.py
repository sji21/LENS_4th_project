"""법령·판례 청크 JSONL을 하나의 Chroma 입력 파일로 합친다.

각 원본 파일은 보존하고, 통합 파일만 새로 만든다. 인덱서는 입력에 없는 ID를
삭제하므로 법령과 판례를 같은 컬렉션에 적재할 때는 이 통합 파일을 사용해야 한다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Sequence


def read_chunks(path: Path) -> list[dict]:
    chunks: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            chunks.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: JSON 파싱 실패: {error}") from error
    return chunks


def merge_chunks(inputs: Sequence[Path], output: Path) -> tuple[int, Counter[str]]:
    """입력 순서대로 합치되 chunk_id 중복은 오류로 막는다."""

    merged: list[dict] = []
    for path in inputs:
        if not path.exists():
            raise FileNotFoundError(f"청크 파일이 없습니다: {path}")
        merged.extend(read_chunks(path))

    ids = [chunk.get("chunk_id") for chunk in merged]
    duplicates = sorted(identifier for identifier, count in Counter(ids).items() if count > 1)
    if duplicates:
        preview = ", ".join(str(identifier) for identifier in duplicates[:10])
        raise ValueError(f"chunk_id 중복 {len(duplicates)}건: {preview}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for chunk in merged:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    doc_types = Counter(
        str(chunk.get("metadata", {}).get("doc_type", "")) for chunk in merged
    )
    return len(merged), doc_types


def main() -> int:
    parser = argparse.ArgumentParser(description="법령·판례 청크를 통합 JSONL로 생성")
    parser.add_argument(
        "--inputs",
        nargs="+",
        type=Path,
        default=[Path("data/chunks/chunks.jsonl"), Path("data/chunks/cases.jsonl")],
        help="합칠 청크 JSONL 파일들 (기본: 법령, 판례)",
    )
    parser.add_argument("--output", type=Path, default=Path("data/chunks/knowledge_chunks.jsonl"))
    args = parser.parse_args()

    try:
        count, doc_types = merge_chunks(args.inputs, args.output)
    except (FileNotFoundError, ValueError) as error:
        print(f"실패: {error}")
        return 1

    distribution = " · ".join(f"{kind} {count}건" for kind, count in sorted(doc_types.items()))
    print(f"  통합 청크: {args.output} ({count}건)")
    print(f"  출처 유형: {distribution}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
