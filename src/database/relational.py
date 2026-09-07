"""법령·판례·가이드와 근거 관계를 보존하는 SQLite DB."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
CASE_NUMBER_UNIQUENESS_MIGRATION = 2


@dataclass(frozen=True)
class DatabaseSummary:
    path: Path
    schema_version: int
    tables: tuple[str, ...]


def connect_database(path: Path) -> sqlite3.Connection:
    """외래키 검사를 활성화한 연결을 반환한다."""

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_relational_database(path: Path) -> DatabaseSummary:
    """멱등적으로 스키마를 생성하고 생성 결과를 반환한다."""

    database_path = path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    with connect_database(database_path) as connection:
        connection.executescript(schema)
        _migrate_case_number_uniqueness(connection)
        version = connection.execute(
            "SELECT MAX(version) AS version FROM schema_migrations"
        ).fetchone()["version"]
        table_rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

    return DatabaseSummary(
        path=database_path,
        schema_version=int(version),
        tables=tuple(row["name"] for row in table_rows),
    )


def _has_unique_case_number_constraint(connection: sqlite3.Connection) -> bool:
    """기존 v1 DB의 ``cases.case_number UNIQUE`` 자동 인덱스를 감지한다."""

    for index in connection.execute("PRAGMA index_list('cases')"):
        if not index[2]:
            continue
        columns = [row[2] for row in connection.execute(f"PRAGMA index_info('{index[1]}')")]
        if columns == ["case_number"]:
            return True
    return False


def _migrate_case_number_uniqueness(connection: sqlite3.Connection) -> None:
    """같은 사건번호의 상이한 판례를 보존하도록 v1 DB를 안전하게 이관한다."""

    applied = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?", (CASE_NUMBER_UNIQUENESS_MIGRATION,)
    ).fetchone()
    if applied:
        return

    if _has_unique_case_number_constraint(connection):
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.executescript(
                """
                BEGIN;
                CREATE TABLE cases_rebuilt (
                    case_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL UNIQUE REFERENCES documents(document_id) ON DELETE RESTRICT,
                    case_number TEXT NOT NULL,
                    court_name TEXT NOT NULL,
                    decision_date TEXT NOT NULL,
                    case_type TEXT NOT NULL,
                    case_name TEXT NOT NULL,
                    holding TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    full_text TEXT NOT NULL,
                    summary_type TEXT NOT NULL DEFAULT 'official'
                        CHECK (summary_type IN ('official', 'generated')),
                    summary_model TEXT
                );
                INSERT INTO cases_rebuilt
                    SELECT case_id, document_id, case_number, court_name, decision_date, case_type,
                           case_name, holding, summary, full_text, summary_type, summary_model
                    FROM cases;
                DROP TABLE cases;
                ALTER TABLE cases_rebuilt RENAME TO cases;
                CREATE INDEX idx_cases_case_number ON cases(case_number);
                CREATE INDEX idx_cases_decision_date ON cases(decision_date);
                COMMIT;
                """
            )
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError("cases 스키마 이관 후 외래키 무결성 오류")
    else:
        connection.execute("CREATE INDEX IF NOT EXISTS idx_cases_case_number ON cases(case_number)")

    connection.execute(
        "INSERT INTO schema_migrations (version) VALUES (?)", (CASE_NUMBER_UNIQUENESS_MIGRATION,)
    )
