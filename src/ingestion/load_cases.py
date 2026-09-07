"""판례 원천 레코드를 공통 SQLite DB와 검색용 청크로 적재한다.

이 모듈은 법령 적재와 같은 관계형 스키마를 쓰되 ``documents``, ``cases``,
``chunks``만 채운다. Streamlit MVP에서는 판례 본문을 검색 결과로 보여 주는
것이 목적이므로 판례와 법령 조문을 구조적으로 잇는 작업은 여기서 하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from src.database.config import resolve_database_paths
from src.database.relational import connect_database, initialize_relational_database


PARSER_VERSION = "case-ingest-1"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class CaseRecord:
    """판례 한 건의 서비스용 원천 레코드.

    ``holding``·``summary``는 공식 판결요지이고, ``full_text``는 국가법령정보센터
    공식 판례 전문이다. 검색 청크에는 짧은 공식 판결요지만 사용한다.
    """

    case_id: str
    case_number: str
    court_name: str
    decision_date: str
    case_type: str
    case_name: str
    holding: str
    summary: str
    full_text: str
    source_url: str
    collected_at: str
    status: str = "current"
    file_path: str = ""
    summary_type: str = "official"
    summary_model: str | None = None

    def validate(self) -> list[str]:
        problems: list[str] = []
        for name in (
            "case_id", "case_number", "court_name", "decision_date", "case_type",
            "case_name", "holding", "summary", "full_text", "source_url", "collected_at",
        ):
            if not str(getattr(self, name)).strip():
                problems.append(f"{name} 이 비어 있음")
        if not _DATE.match(self.decision_date):
            problems.append(f"decision_date '{self.decision_date}' 는 YYYY-MM-DD 형식이 아님")
        else:
            try:
                date.fromisoformat(self.decision_date)
            except ValueError:
                problems.append(f"decision_date '{self.decision_date}' 는 실제 달력 날짜가 아님")
        if self.status not in {"current", "historical", "repealed"}:
            problems.append(f"status '{self.status}' 는 허용값이 아님")
        if self.summary_type not in {"official", "generated"}:
            problems.append(f"summary_type '{self.summary_type}' 는 허용값이 아님")
        return problems


@dataclass
class CaseLoadSummary:
    documents: int = 0
    cases: int = 0
    chunks: int = 0
    skipped: list[str] = field(default_factory=list)


def document_id_of(record: CaseRecord) -> str:
    return f"case-document:{record.case_id}"


def chunk_id_of(record: CaseRecord) -> str:
    return f"case:{record.case_id}#0"


def checksum_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def token_count_of(text: str) -> int:
    return max(1, len(_SPACE.sub("", text)) // 2)


def chunk_body(record: CaseRecord) -> str:
    return f"[{record.court_name} {record.case_number} {record.case_name}]\n{record.holding.strip()}"


def load_case_records(records: list[CaseRecord], connection: sqlite3.Connection) -> CaseLoadSummary:
    """판례를 멱등적으로 적재한다.

    MVP에서는 ``case_law_citations``를 채우지 않는다. 추후 화면에서 특정 조문의
    관련 판례를 구조적으로 제시해야 할 때, 검증된 인용·적용 조문 원천 데이터를
    별도로 확보해 이 테이블에 적재한다.
    """

    summary = CaseLoadSummary()
    for index, record in enumerate(records):
        problems = record.validate()
        if problems:
            summary.skipped.extend(
                f"[{index}] {record.case_id}: {problem}" for problem in problems
            )
            continue

        document_id = document_id_of(record)
        content = chunk_body(record)
        connection.execute(
            """
            INSERT INTO documents (
                document_id, document_type, title, agency, source_url, collected_at,
                checksum, status, file_path
            ) VALUES (?, 'case', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                title = excluded.title,
                agency = excluded.agency,
                source_url = excluded.source_url,
                collected_at = excluded.collected_at,
                checksum = excluded.checksum,
                status = excluded.status,
                file_path = excluded.file_path,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                document_id, record.case_name, record.court_name, record.source_url,
                record.collected_at, checksum_of(record.full_text), record.status,
                record.file_path,
            ),
        )
        summary.documents += 1
        connection.execute(
            """
            INSERT INTO cases (
                case_id, document_id, case_number, court_name, decision_date, case_type,
                case_name, holding, summary, full_text, summary_type, summary_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET
                document_id = excluded.document_id,
                case_number = excluded.case_number,
                court_name = excluded.court_name,
                decision_date = excluded.decision_date,
                case_type = excluded.case_type,
                case_name = excluded.case_name,
                holding = excluded.holding,
                summary = excluded.summary,
                full_text = excluded.full_text,
                summary_type = excluded.summary_type,
                summary_model = excluded.summary_model
            """,
            (
                record.case_id, document_id, record.case_number, record.court_name,
                record.decision_date, record.case_type, record.case_name, record.holding,
                record.summary, record.full_text, record.summary_type, record.summary_model,
            ),
        )
        summary.cases += 1
        connection.execute(
            """
            INSERT INTO chunks (
                chunk_id, document_id, source_type, article_id, case_id, guide_id,
                chunk_index, content, token_count, checksum, parser_version
            ) VALUES (?, ?, 'case', NULL, ?, NULL, 0, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                document_id = excluded.document_id,
                case_id = excluded.case_id,
                content = excluded.content,
                token_count = excluded.token_count,
                checksum = excluded.checksum,
                parser_version = excluded.parser_version
            """,
            (
                chunk_id_of(record), document_id, record.case_id, content,
                token_count_of(content), checksum_of(content), PARSER_VERSION,
            ),
        )
        summary.chunks += 1

    connection.commit()
    return summary


CASE_EXPORT_SQL = """
SELECT
    c.chunk_id,
    c.document_id,
    c.chunk_index,
    c.content,
    c.token_count,
    c.checksum,
    d.title,
    d.source_url,
    d.status,
    ca.case_id,
    ca.case_number,
    ca.court_name,
    ca.decision_date,
    ca.case_name
FROM chunks AS c
JOIN documents AS d ON d.document_id = c.document_id
JOIN cases AS ca ON ca.case_id = c.case_id
WHERE c.source_type = 'case'
ORDER BY ca.decision_date DESC, ca.case_number, c.chunk_index
"""


def export_case_chunks(connection: sqlite3.Connection, out_path: Path) -> int:
    """판례 청크를 법령 청크와 같은 Chroma 입력 규격(JSONL)으로 추출한다."""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for row in connection.execute(CASE_EXPORT_SQL):
            chunk = {
                "chunk_id": row["chunk_id"],
                "doc_id": row["document_id"],
                "chunk_index": row["chunk_index"],
                "text": row["content"],
                "metadata": {
                    # 법령 조문 ID가 아닌 판례의 안정적 논리 ID다.
                    "article_id": row["case_id"],
                    "title": row["case_name"],
                    "doc_type": "case",
                    "article_no": "",
                    "article_title": row["case_name"],
                    "source_url": row["source_url"],
                    "status": row["status"],
                    "effective_date": row["decision_date"],
                    "expiry_date": "",
                    "case_id": row["case_id"],
                    "case_number": row["case_number"],
                    "court_name": row["court_name"],
                    "decision_date": row["decision_date"],
                    "checksum": row["checksum"],
                    "token_count": row["token_count"],
                },
            }
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_case_records(path: Path) -> list[CaseRecord]:
    return [
        CaseRecord(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_case_records(records: list[CaseRecord], path: Path) -> None:
    """완전한 JSONL을 만든 뒤에만 대상 파일을 교체한다."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def main() -> int:
    paths = resolve_database_paths()
    parser = argparse.ArgumentParser(description="판례 원천 레코드를 SQLite에 적재")
    parser.add_argument("--records", required=True, help="판례 원천 레코드 JSONL")
    parser.add_argument("--database", default=str(paths.relational))
    parser.add_argument("--export", default="data/chunks/cases.jsonl")
    args = parser.parse_args()

    records_path = Path(args.records)
    if not records_path.exists():
        print(f"원천 레코드 파일이 없습니다: {records_path}")
        return 1

    database = Path(args.database)
    initialize_relational_database(database)
    with closing(connect_database(database)) as connection:
        summary = load_case_records(read_case_records(records_path), connection)
        exported = export_case_chunks(connection, Path(args.export))

    print(f"  DB: {database}")
    print(f"  판례 {summary.cases}건 · 판례 청크 {summary.chunks}건")
    print(f"  case_law_citations: MVP에서는 적재하지 않음")
    print(f"  청크 추출: {args.export} ({exported}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
