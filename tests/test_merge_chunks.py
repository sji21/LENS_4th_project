from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingestion.merge_chunks import merge_chunks


def write_chunks(path: Path, chunks: list[dict]) -> None:
    path.write_text("".join(json.dumps(chunk) + "\n" for chunk in chunks), encoding="utf-8")


def chunk(chunk_id: str, doc_type: str) -> dict:
    return {"chunk_id": chunk_id, "metadata": {"doc_type": doc_type}}


def test_merges_inputs_without_changing_their_order(tmp_path: Path):
    laws = tmp_path / "laws.jsonl"
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "combined.jsonl"
    write_chunks(laws, [chunk("law:1", "law")])
    write_chunks(cases, [chunk("case:1", "case"), chunk("case:2", "case")])

    count, doc_types = merge_chunks([laws, cases], output)

    assert count == 3
    assert doc_types == {"law": 1, "case": 2}
    assert [row["chunk_id"] for row in map(json.loads, output.read_text().splitlines())] == [
        "law:1", "case:1", "case:2"
    ]


def test_rejects_duplicate_chunk_ids(tmp_path: Path):
    laws = tmp_path / "laws.jsonl"
    cases = tmp_path / "cases.jsonl"
    write_chunks(laws, [chunk("same", "law")])
    write_chunks(cases, [chunk("same", "case")])

    with pytest.raises(ValueError, match="chunk_id 중복"):
        merge_chunks([laws, cases], tmp_path / "combined.jsonl")
