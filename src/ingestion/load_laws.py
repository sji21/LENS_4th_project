"""법령 원천 레코드를 SQLite 지식 DB에 적재한다.

임베딩과 Chroma 적재는 여기서 하지 않는다. 원문과 메타데이터를 관계형 DB에
먼저 넣어 파싱 품질을 검증할 수 있게 하고, 벡터 적재는 임베딩 모델이 확정된
뒤 별도 단계에서 수행한다. 모델이 바뀌어도 이 단계 결과는 재사용된다.

원천 레코드 형식은 docs/case-data-handoff.md 2절을 따른다. 수집 경로(공식 API,
원문 페이지 등)와 무관하게 이 형식만 맞추면 그대로 적재된다.

    수집 → 파싱 → LawArticleRecord 목록 → load_records() → SQLite
                                                              ↓
                                              export_chunks() → chunks.jsonl
                                                              ↓
                                          validate_chunks / 검색 실험 / 임베딩

실행:
    python -m src.ingestion.load_laws --records data/parsed/law_records.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.database.config import resolve_database_paths
from src.database.relational import connect_database, initialize_relational_database

PARSER_VERSION = "law-ingest-1"

_SPACE = re.compile(r"\s+")


@dataclass
class LawArticleRecord:
    """조문 한 건. 항·호 단위로 쪼갤 경우 같은 조문에 여러 건이 생긴다."""

    law_name: str
    law_type: str
    ministry: str
    law_code: str

    proclamation_number: str
    proclaimed_at: str
    effective_from: str
    content: str
    source_url: str
    collected_at: str

    article_number: str
    article_title: str = ""
    paragraph_number: str = ""
    item_number: str = ""

    effective_to: str | None = None
    status: str = "current"
    document_type: str = "law"
    file_path: str = ""

    def validate(self) -> list[str]:
        problems = []
        for name in ("law_name", "law_code", "article_number", "content",
                     "effective_from", "proclamation_number"):
            if not str(getattr(self, name)).strip():
                problems.append(f"{name} 이 비어 있음")
        if self.status not in {"current", "historical", "repealed"}:
            problems.append(f"status '{self.status}' 는 허용값이 아님")
        if self.document_type not in {"law", "decree", "rule", "interp", "guide"}:
            problems.append(f"document_type '{self.document_type}' 는 허용값이 아님")
        for name in ("effective_from", "proclaimed_at"):
            value = getattr(self, name)
            if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                problems.append(f"{name} '{value}' 는 YYYY-MM-DD 형식이 아님")
        return problems


# ── 식별자 ─────────────────────────────────────────────────────────────
# 모두 입력에서 결정론적으로 만든다. 같은 원천을 다시 적재해도 같은 ID가 나오므로
# 재실행이 중복을 만들지 않는다.

def _slug(text: str) -> str:
    return _SPACE.sub("", text.strip())


def law_id_of(record: LawArticleRecord) -> str:
    return f"law-{_slug(record.law_code)}"


def document_id_of(record: LawArticleRecord) -> str:
    return f"law-{_slug(record.law_name)}-{record.effective_from.replace('-', '')}"


def law_version_id_of(record: LawArticleRecord) -> str:
    return f"{law_id_of(record)}-{_slug(record.proclamation_number)}"


def article_row_id_of(record: LawArticleRecord) -> str:
    """SQLite law_articles 의 대리키.

    Chroma metadata 에 넣는 논리 ID(`{법령명}-{조문번호}`)와는 다른 값이다.
    이쪽은 판본과 항·호까지 구분하고, 논리 ID 는 조문 단위로 고정된다.
    """
    parts = [law_version_id_of(record), record.article_number]
    if record.paragraph_number:
        parts.append(f"p{record.paragraph_number}")
    if record.item_number:
        parts.append(f"i{record.item_number}")
    return "#".join(parts)


def logical_article_id_of(record: LawArticleRecord) -> str:
    """평가셋 정답 라벨과 맞추는 논리 ID. 항·호를 붙이지 않는다."""
    return f"{record.law_name.strip()}-{record.article_number}"


def chunk_id_of(record: LawArticleRecord, chunk_index: int) -> str:
    return f"{document_id_of(record)}#{record.article_number}#{chunk_index}"


def checksum_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_body(record: LawArticleRecord) -> str:
    """청크 본문. 헤더를 붙여 본문만 봐도 출처를 알 수 있게 한다."""
    title = f"({record.article_title})" if record.article_title else ""
    return f"[{record.law_name} {record.article_number}{title}]\n{record.content.strip()}"


def token_count_of(text: str) -> int:
    """대략적인 토큰 수. 한국어는 글자 수의 절반 정도로 잡는다.

    실제 토크나이저를 쓰지 않는 이유는 이 값이 청킹 판단의 참고 지표일 뿐
    검색·채점에 쓰이지 않기 때문이다. 정확한 값이 필요해지면 교체한다.
    """
    return max(1, len(text) // 2)


# ── 적재 ───────────────────────────────────────────────────────────────

@dataclass
class LoadSummary:
    documents: int = 0
    laws: int = 0
    versions: int = 0
    articles: int = 0
    chunks: int = 0
    skipped: list[str] = field(default_factory=list)


def load_records(
    records: list[LawArticleRecord],
    connection: sqlite3.Connection,
) -> LoadSummary:
    """원천 레코드를 관계형 테이블로 펼쳐 넣는다. 재실행해도 안전하다."""
    summary = LoadSummary()

    valid: list[LawArticleRecord] = []
    for i, record in enumerate(records):
        problems = record.validate()
        if problems:
            where = f"[{i}] {record.law_name} {record.article_number}"
            summary.skipped.extend(f"{where}: {p}" for p in problems)
            continue
        valid.append(record)

    # 이번 입력에 들어온 판본의 조문을 통째로 지우고 다시 넣는다. upsert 만 하면
    # 조문이 삭제되거나 항·호 구성이 바뀐 경우 옛 행이 남는다. law_articles 를
    # 지우면 chunks 는 외래키 CASCADE 로 함께 사라진다.
    for version_id in {law_version_id_of(r) for r in valid}:
        connection.execute(
            "DELETE FROM law_articles WHERE law_version_id = ?", (version_id,)
        )

    seen_documents: set[str] = set()
    seen_laws: set[str] = set()
    seen_versions: set[str] = set()
    # 같은 조문을 항·호로 나눌 때의 순번. 입력 전체가 아니라 조문 안에서만 센다.
    # 문서 단위 러닝 카운터로 매기면 레코드 순서가 바뀔 때 같은 조문이 다른
    # chunk_id 를 받아 중복 행이 생긴다.
    chunk_seq: dict[tuple[str, str], int] = {}

    for record in valid:
        law_id = law_id_of(record)
        document_id = document_id_of(record)
        version_id = law_version_id_of(record)
        article_id = article_row_id_of(record)

        if law_id not in seen_laws:
            connection.execute(
                """
                INSERT INTO laws (law_id, law_name, law_type, ministry, law_code)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(law_id) DO UPDATE SET
                    law_name = excluded.law_name,
                    law_type = excluded.law_type,
                    ministry = excluded.ministry
                """,
                (law_id, record.law_name, record.law_type, record.ministry,
                 record.law_code),
            )
            seen_laws.add(law_id)
            summary.laws += 1

        if document_id not in seen_documents:
            # 문서 단위 체크섬은 조문을 모아 계산할 수 없으므로 식별 정보로 대신한다.
            doc_checksum = checksum_of(f"{document_id}|{record.proclamation_number}")
            connection.execute(
                """
                INSERT INTO documents (
                    document_id, document_type, title, agency, source_url,
                    collected_at, checksum, status, file_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    title = excluded.title,
                    status = excluded.status,
                    collected_at = excluded.collected_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (document_id, record.document_type, record.law_name, record.ministry,
                 record.source_url, record.collected_at, doc_checksum,
                 record.status, record.file_path),
            )
            seen_documents.add(document_id)
            summary.documents += 1

        if version_id not in seen_versions:
            connection.execute(
                """
                INSERT INTO law_versions (
                    law_version_id, law_id, document_id, proclamation_number,
                    proclaimed_at, effective_from, effective_to, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(law_version_id) DO UPDATE SET
                    effective_from = excluded.effective_from,
                    effective_to = excluded.effective_to,
                    status = excluded.status
                """,
                (version_id, law_id, document_id, record.proclamation_number,
                 record.proclaimed_at, record.effective_from, record.effective_to,
                 record.status),
            )
            seen_versions.add(version_id)
            summary.versions += 1

        connection.execute(
            """
            INSERT INTO law_articles (
                article_id, law_version_id, article_number, article_title,
                paragraph_number, item_number, content
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                article_title = excluded.article_title,
                content = excluded.content
            """,
            (article_id, version_id, record.article_number, record.article_title,
             record.paragraph_number, record.item_number, record.content),
        )
        summary.articles += 1

        key = (document_id, record.article_number)
        index = chunk_seq.get(key, 0)
        chunk_seq[key] = index + 1
        body = chunk_body(record)
        connection.execute(
            """
            INSERT INTO chunks (
                chunk_id, document_id, source_type, article_id,
                chunk_index, content, token_count, checksum, parser_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                content = excluded.content,
                token_count = excluded.token_count,
                checksum = excluded.checksum,
                parser_version = excluded.parser_version
            """,
            (chunk_id_of(record, index), document_id, record.document_type,
             article_id, index, body, token_count_of(body), checksum_of(body),
             PARSER_VERSION),
        )
        summary.chunks += 1

    connection.commit()
    return summary


# ── 추출 ───────────────────────────────────────────────────────────────

EXPORT_SQL = """
SELECT
    c.chunk_id,
    c.document_id,
    c.chunk_index,
    c.content,
    c.token_count,
    c.checksum,
    c.source_type,
    d.title       AS doc_title,
    d.source_url  AS source_url,
    d.status      AS status,
    a.article_number,
    a.article_title,
    l.law_name,
    v.effective_from,
    v.effective_to,
    v.proclamation_number
FROM chunks AS c
JOIN documents     AS d ON d.document_id = c.document_id
JOIN law_articles  AS a ON a.article_id = c.article_id
JOIN law_versions  AS v ON v.law_version_id = a.law_version_id
JOIN laws          AS l ON l.law_id = v.law_id
ORDER BY c.document_id, c.chunk_index
"""


def export_chunks(connection: sqlite3.Connection, out_path: Path) -> int:
    """SQLite 조인 결과를 평평한 청크 JSONL 로 뽑는다.

    Chroma 에는 JOIN 이 없으므로 필터에 쓸 값을 여기서 미리 펼쳐 둔다.
    같은 파일을 validate_chunks 검증과 검색 실험에도 그대로 쓴다.
    규격은 docs/chunk-schema.md 참고.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0

    with out_path.open("w", encoding="utf-8") as f:
        for row in connection.execute(EXPORT_SQL):
            chunk = {
                "chunk_id": row["chunk_id"],
                "doc_id": row["document_id"],
                "chunk_index": row["chunk_index"],
                "text": row["content"],
                "metadata": {
                    # 평가 채점의 기준. 항·호를 붙이지 않는 조문 단위 논리 ID.
                    "article_id": f"{row['law_name']}-{row['article_number']}",
                    "title": row["doc_title"],
                    "doc_type": row["source_type"],
                    "article_no": row["article_number"],
                    "article_title": row["article_title"] or "",
                    "source_url": row["source_url"] or "",
                    "status": row["status"],
                    "effective_date": row["effective_from"] or "",
                    "expiry_date": row["effective_to"] or "",
                    "version": row["proclamation_number"] or "",
                    "checksum": row["checksum"],
                    "token_count": row["token_count"],
                },
            }
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            written += 1

    return written


def read_records(path: Path) -> list[LawArticleRecord]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(LawArticleRecord(**json.loads(line)))
    return records


def write_records(records: list[LawArticleRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def main() -> int:
    paths = resolve_database_paths()
    ap = argparse.ArgumentParser(description="법령 원천 레코드를 SQLite 에 적재")
    ap.add_argument("--records", required=True, help="원천 레코드 jsonl")
    ap.add_argument("--database", default=str(paths.relational))
    ap.add_argument("--export", default="data/chunks/chunks.jsonl",
                    help="적재 후 뽑아낼 청크 jsonl 경로")
    args = ap.parse_args()

    records_path = Path(args.records)
    if not records_path.exists():
        print(f"원천 레코드 파일이 없습니다: {records_path}")
        return 1

    database = Path(args.database)
    initialize_relational_database(database)

    records = read_records(records_path)
    # sqlite3 의 `with` 는 커밋만 하고 닫지 않는다. Windows 에서 파일이 잠긴 채
    # 남으므로 closing 으로 확실히 닫는다.
    with closing(connect_database(database)) as connection:
        summary = load_records(records, connection)
        exported = export_chunks(connection, Path(args.export))

    print()
    print(f"  DB: {database}")
    print(f"  법령 {summary.laws}건 · 판본 {summary.versions}건 · "
          f"문서 {summary.documents}건")
    print(f"  조문 {summary.articles}건 · 청크 {summary.chunks}건")
    if summary.skipped:
        print(f"\n  건너뜀 {len(summary.skipped)}건")
        for reason in summary.skipped[:10]:
            print(f"    - {reason}")
        if len(summary.skipped) > 10:
            print(f"    ... 외 {len(summary.skipped) - 10}건")
    print(f"\n  청크 추출: {args.export} ({exported}건)")
    print(f"  다음: python -m src.ingestion.validate_chunks {args.export} "
          f"--eval-set data/eval/dev.jsonl\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
