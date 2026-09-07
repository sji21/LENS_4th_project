"""판례 SQLite 적재와 공통 청크 추출 테스트."""

from __future__ import annotations

import json
import gc
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from src.database.relational import connect_database, initialize_relational_database
from src.ingestion.load_cases import (
    CaseRecord,
    export_case_chunks,
    load_case_records,
    read_case_records,
    write_case_records,
)
from src.ingestion.validate_chunks import Report, check_structure


def make_record(**overrides: str) -> CaseRecord:
    data = {
        "case_id": "CASE-TEST-1",
        "case_number": "2024다12345",
        "court_name": "대법원",
        "decision_date": "2024-01-02",
        "case_type": "민사",
        "case_name": "임대차보증금반환",
        "holding": "대항력을 갖춘 임차인의 보증금반환청구권을 판단했다.",
        "summary": "대항력을 갖춘 임차인의 보증금반환청구권을 판단했다.",
        "full_text": "대항력을 갖춘 임차인의 보증금반환청구권을 판단한 공식 판례 전문이다.",
        "source_url": "https://www.law.go.kr/LSW/precInfoP.do?evtNo=2024다12345",
        "collected_at": "2026-08-28",
        "file_path": "cases/CASE-TEST-1.md",
    }
    data.update(overrides)
    return CaseRecord(**data)


def test_case_records_round_trip_through_jsonl():
    with TemporaryDirectory() as temp:
        path = Path(temp) / "cases.jsonl"
        write_case_records([make_record()], path)
        assert read_case_records(path) == [make_record()]


def test_case_ingestion_leaves_law_citations_empty_and_exports_common_schema():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        database = root / "knowledge.sqlite3"
        chunks_path = root / "cases.jsonl"
        initialize_relational_database(database)

        with closing(connect_database(database)) as connection:
            summary = load_case_records([make_record()], connection)
            exported = export_case_chunks(connection, chunks_path)
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("documents", "cases", "chunks", "case_law_citations")
            }

        assert summary.cases == 1
        assert exported == 1
        assert counts == {
            "documents": 1,
            "cases": 1,
            "chunks": 1,
            "case_law_citations": 0,
        }

        chunk = json.loads(chunks_path.read_text(encoding="utf-8"))
        assert chunk["metadata"]["doc_type"] == "case"
        assert chunk["metadata"]["case_number"] == "2024다12345"
        assert chunk["metadata"]["article_id"] == "CASE-TEST-1"
        assert "공식 판례 전문" not in chunk["text"]
        assert chunk["text"].endswith(make_record().holding)
        report = Report()
        check_structure([chunk], report)
        assert report.errors == []
        gc.collect()


def test_case_ingestion_is_idempotent():
    with TemporaryDirectory() as temp:
        database = Path(temp) / "knowledge.sqlite3"
        initialize_relational_database(database)
        with closing(connect_database(database)) as connection:
            load_case_records([make_record()], connection)
            load_case_records([make_record(holding="수정된 판결요지", summary="수정된 판결요지")], connection)
            assert connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
            assert connection.execute("SELECT content FROM chunks").fetchone()[0].endswith("수정된 판결요지")
        gc.collect()


def test_case_ingestion_allows_same_case_number_for_distinct_cases():
    with TemporaryDirectory() as temp:
        database = Path(temp) / "knowledge.sqlite3"
        initialize_relational_database(database)
        second = make_record(
            case_id="CASE-TEST-2",
            court_name="서울고등법원",
            case_name="별도 임대차보증금반환",
            source_url="https://www.law.go.kr/LSW/precInfoP.do?evtNo=2024다12345-2",
        )
        with closing(connect_database(database)) as connection:
            summary = load_case_records([make_record(), second], connection)
            assert summary.cases == 2
            assert connection.execute(
                "SELECT COUNT(*) FROM cases WHERE case_number = '2024다12345'"
            ).fetchone()[0] == 2
        gc.collect()
