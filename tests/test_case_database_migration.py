"""v1 판례 DB의 사건번호 UNIQUE 제약 이관 테스트."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.database.relational import SCHEMA_PATH, connect_database, initialize_relational_database


def test_migrates_legacy_unique_case_number_constraint(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    legacy_schema = SCHEMA_PATH.read_text(encoding="utf-8")
    legacy_schema = legacy_schema.replace(
        "case_number TEXT NOT NULL,", "case_number TEXT NOT NULL UNIQUE,"
    ).replace("CREATE INDEX IF NOT EXISTS idx_cases_case_number ON cases(case_number);\n\n", "")
    with sqlite3.connect(path) as connection:
        connection.executescript(legacy_schema)
        connection.execute(
            """INSERT INTO documents
               (document_id, document_type, title, agency, source_url, collected_at,
                checksum, status, file_path)
               VALUES ('legacy-doc', 'case', '기존 판례', '대법원', 'https://example.test/legacy',
                       '2026-08-30', 'legacy', 'current', 'legacy.json')"""
        )
        connection.execute(
            """INSERT INTO cases
               (case_id, document_id, case_number, court_name, decision_date, case_type,
               case_name, holding, summary, full_text)
               VALUES ('legacy-case', 'legacy-doc', '2024다12345', '대법원', '2024-01-01',
                       '민사', '기존 사건', '요지', '요지', '전문')"""
        )
        connection.execute(
            """INSERT INTO documents
               (document_id, document_type, title, agency, source_url, collected_at,
                checksum, status, file_path)
               VALUES ('law-doc', 'law', '주택임대차보호법', '국가법령정보센터',
                       'https://example.test/law', '2026-08-30', 'law', 'current', 'law.json')"""
        )
        connection.execute(
            """INSERT INTO laws (law_id, law_name, law_type, ministry, law_code)
               VALUES ('law-1', '주택임대차보호법', '법률', '법무부', 'LAW-1')"""
        )
        connection.execute(
            """INSERT INTO law_versions
               (law_version_id, law_id, document_id, proclamation_number, proclaimed_at,
                effective_from, status)
               VALUES ('law-version-1', 'law-1', 'law-doc', '1', '2024-01-01',
                       '2024-01-01', 'current')"""
        )
        connection.execute(
            """INSERT INTO law_articles
               (article_id, law_version_id, article_number, content)
               VALUES ('law-article-1', 'law-version-1', '제3조', '대항력')"""
        )
        connection.execute(
            """INSERT INTO case_law_citations
               (case_id, article_id, citation_type)
               VALUES ('legacy-case', 'law-article-1', 'applied')"""
        )
        connection.execute(
            """INSERT INTO chunks
               (chunk_id, document_id, source_type, case_id, chunk_index, content,
                token_count, checksum, parser_version)
               VALUES ('case:legacy-case#0', 'legacy-doc', 'case', 'legacy-case', 0,
                       '요지', 1, 'chunk', 'test')"""
        )

    assert initialize_relational_database(path).schema_version == 2
    with connect_database(path) as connection:
        connection.execute(
            """INSERT INTO documents
               (document_id, document_type, title, agency, source_url, collected_at,
                checksum, status, file_path)
               VALUES ('new-doc', 'case', '별도 판례', '서울고등법원', 'https://example.test/new',
                       '2026-08-30', 'new', 'current', 'new.json')"""
        )
        connection.execute(
            """INSERT INTO cases
               (case_id, document_id, case_number, court_name, decision_date, case_type,
                case_name, holding, summary, full_text)
               VALUES ('new-case', 'new-doc', '2024다12345', '서울고등법원', '2024-01-02',
                       '민사', '별도 사건', '요지', '요지', '전문')"""
        )
        assert connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM case_law_citations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM chunks WHERE case_id = 'legacy-case'").fetchone()[0] == 1
        indexes = {row[1] for row in connection.execute("PRAGMA index_list('cases')")}
        assert {"idx_cases_case_number", "idx_cases_decision_date"}.issubset(indexes)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
