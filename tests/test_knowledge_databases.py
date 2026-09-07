"""PATCH-009 법령·판례 관계형 DB와 Chroma 초기화 테스트."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.database.relational import connect_database, initialize_relational_database
from src.database.vector import initialize_vector_store


ROOT = Path(__file__).resolve().parents[1]


EXPECTED_TABLES = {
    "documents",
    "laws",
    "law_versions",
    "law_articles",
    "cases",
    "case_law_citations",
    "guides",
    "guide_law_references",
    "risk_rules",
    "risk_rule_keywords",
    "rule_evidence",
    "chunks",
    "evaluation_questions",
    "evaluation_evidence",
}
SUPPORTED_SOURCE_TYPES = ("law", "decree", "rule", "case", "interp", "guide")


def insert_law_and_case(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (
            "doc-law-v1",
            "law",
            "주택임대차보호법",
            "법제처",
            "https://example.test/law",
            "2026-08-27",
            "law-checksum",
            "current",
            "data/raw/law.xml",
        ),
    )
    connection.execute(
        "INSERT INTO laws VALUES (?, ?, ?, ?, ?)",
        ("law-housing", "주택임대차보호법", "법률", "국토교통부", "L-HOUSING"),
    )
    connection.execute(
        "INSERT INTO law_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "law-housing-v1",
            "law-housing",
            "doc-law-v1",
            "법률 제1호",
            "2026-01-01",
            "2026-01-01",
            None,
            "current",
        ),
    )
    connection.execute(
        "INSERT INTO law_articles VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "article-3-3",
            "law-housing-v1",
            "제3조의3",
            "임차권등기명령",
            "",
            "",
            "테스트용 조문",
        ),
    )
    connection.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (
            "doc-case-1",
            "case",
            "임대차 판례",
            "대법원",
            "https://example.test/case",
            "2026-08-27",
            "case-checksum",
            "current",
            "data/raw/case.json",
        ),
    )
    connection.execute(
        "INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "case-1",
            "doc-case-1",
            "2026다1234",
            "대법원",
            "2026-05-01",
            "민사",
            "보증금 반환",
            "판결 요지",
            "공식 요약",
            "판결문 원문",
            "official",
            None,
        ),
    )


def test_initializes_expected_schema_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.sqlite3"

    first = initialize_relational_database(path)
    second = initialize_relational_database(path)

    assert first.schema_version == second.schema_version == 2
    assert EXPECTED_TABLES.issubset(first.tables)


def test_preserves_case_to_law_article_relationship(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.sqlite3"
    initialize_relational_database(path)

    with connect_database(path) as connection:
        insert_law_and_case(connection)
        connection.execute(
            """
            INSERT INTO case_law_citations
                (case_id, article_id, citation_type, verified)
            VALUES ('case-1', 'article-3-3', 'applied', 1)
            """
        )
        relation = connection.execute(
            """
            SELECT cases.case_number, law_articles.article_number
            FROM case_law_citations
            JOIN cases USING (case_id)
            JOIN law_articles USING (article_id)
            """
        ).fetchone()

    assert dict(relation) == {
        "case_number": "2026다1234",
        "article_number": "제3조의3",
    }


def test_accepts_all_supported_document_and_chunk_types(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.sqlite3"
    initialize_relational_database(path)

    with connect_database(path) as connection:
        for source_type in SUPPORTED_SOURCE_TYPES:
            connection.execute(
                """
                INSERT INTO documents
                    (document_id, document_type, title, agency, source_url,
                     collected_at, checksum, status, file_path)
                VALUES (?, ?, ?, '공식기관', ?, '2026-08-28', ?, 'current', ?)
                """,
                (
                    f"doc-{source_type}",
                    source_type,
                    f"{source_type} 문서",
                    f"https://example.test/{source_type}",
                    f"{source_type}-checksum",
                    f"data/raw/{source_type}.json",
                ),
            )
            connection.execute(
                """
                INSERT INTO chunks
                    (chunk_id, document_id, source_type, chunk_index, content,
                     token_count, checksum, parser_version)
                VALUES (?, ?, ?, 0, '공식 문서 청크', 4, ?, 'test-v1')
                """,
                (
                    f"chunk-{source_type}",
                    f"doc-{source_type}",
                    source_type,
                    f"chunk-{source_type}-checksum",
                ),
            )

        document_types = {
            row[0] for row in connection.execute("SELECT document_type FROM documents")
        }
        chunk_types = {row[0] for row in connection.execute("SELECT source_type FROM chunks")}

    assert document_types == set(SUPPORTED_SOURCE_TYPES)
    assert chunk_types == set(SUPPORTED_SOURCE_TYPES)


def test_rejects_unsupported_document_and_chunk_types(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.sqlite3"
    initialize_relational_database(path)

    with connect_database(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO documents
                    (document_id, document_type, title, agency, source_url,
                     collected_at, checksum, status, file_path)
                VALUES ('doc-unknown', 'unknown', '미지원 문서', '기관',
                        'https://example.test/unknown', '2026-08-28',
                        'unknown-checksum', 'current', 'data/raw/unknown.json')
                """
            )

        connection.execute(
            """
            INSERT INTO documents
                (document_id, document_type, title, agency, source_url,
                 collected_at, checksum, status, file_path)
            VALUES ('doc-guide', 'guide', '공식 안내', '기관',
                    'https://example.test/guide', '2026-08-28',
                    'guide-checksum', 'current', 'data/raw/guide.json')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO chunks
                    (chunk_id, document_id, source_type, chunk_index, content,
                     token_count, checksum, parser_version)
                VALUES ('chunk-unknown', 'doc-guide', 'unknown', 0,
                        '미지원 청크', 2, 'chunk-unknown-checksum', 'test-v1')
                """
            )


def test_rejects_evidence_without_exactly_one_source(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.sqlite3"
    initialize_relational_database(path)

    with connect_database(path) as connection:
        connection.execute(
            """
            INSERT INTO risk_rules
                (rule_id, title, section, severity, guidance, severity_basis, version)
            VALUES ('tenant', '임차권등기', '을구', 'high', '확인', '내부 정책', '1')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO rule_evidence (rule_id, reason) VALUES ('tenant', '근거 없음')"
            )


def test_initializes_persistent_chroma_collection(tmp_path: Path) -> None:
    path = tmp_path / "chroma"

    first = initialize_vector_store(path)
    second = initialize_vector_store(path)

    assert first.collection_name == second.collection_name == "knowledge_chunks"
    assert first.document_count == second.document_count == 0
    assert path.is_dir()


def test_readme_documents_db_relationships_and_generated_paths() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    for expected in (
        "python scripts/init_databases.py",
        "data/database/knowledge.sqlite3",
        # 인덱스 경로에는 임베딩 모델과 차원이 붙는다 (컬렉션당 차원은 하나뿐)
        "data/index/chroma_kurev1_1024/",
        "case_law_citations",
        "rule_evidence",
        "chunks.chunk_id",
    ):
        assert expected in readme

    assert "data/database/" in gitignore
    assert "data/index/" in gitignore
