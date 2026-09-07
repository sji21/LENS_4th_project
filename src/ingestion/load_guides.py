"""공식 안내 원문을 공통 SQLite 에 적재하고 검색용 청크로 뽑는다.

`documents` 와 `guides` 만 채운다. `guide_law_references`(안내 ↔ 조문 연결)는
비워 둔다. 그 테이블의 `article_id` 는 법령 **버전**에 고정된 키를 참조하는데,
어느 안내가 어느 시점의 조문을 가리키는지는 검증된 원천 없이 정할 수 없다.
추측으로 채우면 틀린 데이터가 된다. 판례의 `case_law_citations` 와 같은 이유다.

안내는 긴 편이라(HUG 2,800자) 조문 하나가 한 청크인 법령과 달리 잘라야 한다.
문단 경계에서 자르고, 각 청크는 규격대로 `[제목]` 헤더로 시작한다.

실행:
    python -m src.ingestion.load_guides --records data/parsed/guide_records.jsonl \\
        --export data/chunks/guides.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from src.database.config import resolve_database_paths
from src.database.relational import connect_database, initialize_relational_database

PARSER_VERSION = "guide-ingest-1"
_SPACE = re.compile(r"\s+")

# 청크 하나의 목표 길이. 법령 조문 중앙값이 254자라 그와 크게 다르지 않게 잡는다.
# 너무 잘게 쪼개면 문맥이 끊기고, 통으로 두면 5건 중 한 칸을 2,800자가 먹는다.
TARGET_CHARS = 600


@dataclass(frozen=True)
class GuideRecord:
    guide_id: str
    title: str
    agency: str
    guide_type: str
    topic: str
    source_url: str
    published_at: str
    content: str
    collected_at: str
    status: str = "current"
    published_at_source: str = "unknown"


@dataclass
class GuideLoadSummary:
    documents: int = 0
    guides: int = 0
    chunks: int = 0
    removed: int = 0
    skipped: list[str] | None = None

    def __post_init__(self) -> None:
        if self.skipped is None:
            self.skipped = []


def document_id_of(record: GuideRecord) -> str:
    return f"guide-document:{record.guide_id}"


def chunk_id_of(record: GuideRecord, index: int) -> str:
    return f"{record.guide_id}#{index}"


def checksum_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def token_count_of(text: str) -> int:
    return max(1, len(_SPACE.sub("", text)) // 2)


def split_content(content: str, target: int = TARGET_CHARS) -> list[str]:
    """문단 경계에서 자른다.

    글자 수로 기계적으로 끊으면 "보증한도" 같은 소제목과 그 설명이 갈라진다.
    줄 단위로 모으다가 목표를 넘으면 거기서 끊는다.
    """
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in (l.strip() for l in content.splitlines()):
        if not line:
            continue
        if current and size + len(line) > target:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line)
    if current:
        chunks.append("\n".join(current))
    return chunks or [content.strip()]


def chunk_body(record: GuideRecord, body: str) -> str:
    """청크만 떼어 봐도 출처를 알 수 있게 헤더를 붙인다(규격 요구사항)."""
    return f"[{record.title}]\n{body}"


def load_guide_records(
    records: list[GuideRecord], connection: sqlite3.Connection
) -> GuideLoadSummary:
    """멱등 적재. 같은 guide_id 를 다시 넣어도 행이 늘지 않는다.

    청크는 안내가 짧아지면 개수가 줄 수 있으므로, 문서별로 지우고 다시 넣는다.
    upsert 만 하면 예전 청크가 남아 계속 검색된다.

    **커밋하지 않는다.** 건너뛴 레코드가 있으면 호출한 쪽이 되돌려야 하는데,
    여기서 커밋해 버리면 stale 정리와 일부 적재가 이미 반영된 뒤가 된다. 그러면
    실패로 보고하면서 DB 는 바꿔 놓은 상태가 되고, 다음 실행의 export 가 검증되지
    않은 상태를 내보낸다.
    """
    summary = GuideLoadSummary()

    # 입력에 없는 안내는 지운다. fetch_guides 가 전부 성공했을 때만 파일을 쓰므로
    # 입력은 항상 전량이고, 그래서 "입력이 현재 상태가 된다"를 지킬 수 있다.
    # 지우지 않으면 원천에서 뺀 안내가 DB 와 검색에 계속 남는다.
    incoming = {record.guide_id for record in records}
    existing = {
        row["guide_id"] for row in connection.execute("SELECT guide_id FROM guides")
    }
    for stale in sorted(existing - incoming):
        connection.execute("DELETE FROM chunks WHERE guide_id = ?", (stale,))
        connection.execute("DELETE FROM guides WHERE guide_id = ?", (stale,))
        connection.execute(
            "DELETE FROM documents WHERE document_id = ?", (f"guide-document:{stale}",)
        )
        summary.removed += 1

    for index, record in enumerate(records):
        if not record.content.strip():
            summary.skipped.append(f"[{index}] {record.guide_id}: 본문이 비어 있음")
            continue

        document_id = document_id_of(record)
        connection.execute(
            """
            INSERT INTO documents (
                document_id, document_type, title, agency, source_url, collected_at,
                checksum, status, file_path
            ) VALUES (?, 'guide', ?, ?, ?, ?, ?, ?, '')
            ON CONFLICT(document_id) DO UPDATE SET
                title = excluded.title,
                agency = excluded.agency,
                source_url = excluded.source_url,
                collected_at = excluded.collected_at,
                checksum = excluded.checksum,
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                document_id, record.title, record.agency, record.source_url,
                record.collected_at, checksum_of(record.content), record.status,
            ),
        )
        summary.documents += 1

        connection.execute(
            """
            INSERT INTO guides (
                guide_id, document_id, guide_type, published_at, updated_at,
                topic, content
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guide_id) DO UPDATE SET
                document_id = excluded.document_id,
                guide_type = excluded.guide_type,
                published_at = excluded.published_at,
                updated_at = excluded.updated_at,
                topic = excluded.topic,
                content = excluded.content
            """,
            (
                record.guide_id, document_id, record.guide_type,
                record.published_at if record.published_at_source == "page" else "",
                record.collected_at, record.topic, record.content,
            ),
        )
        summary.guides += 1

        connection.execute(
            "DELETE FROM chunks WHERE document_id = ? AND source_type = 'guide'",
            (document_id,),
        )
        for position, body in enumerate(split_content(record.content)):
            text = chunk_body(record, body)
            connection.execute(
                """
                INSERT INTO chunks (
                    chunk_id, document_id, source_type, article_id, case_id, guide_id,
                    chunk_index, content, token_count, checksum, parser_version
                ) VALUES (?, ?, 'guide', NULL, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id_of(record, position), document_id, record.guide_id,
                    position, text, token_count_of(text), checksum_of(text),
                    PARSER_VERSION,
                ),
            )
            summary.chunks += 1

    return summary


EXPORT_SQL = """
SELECT
    c.chunk_id, c.document_id, c.chunk_index, c.content, c.token_count, c.checksum,
    d.title, d.source_url, d.status, d.collected_at,
    g.guide_id, g.guide_type, g.topic, g.published_at
FROM chunks AS c
JOIN documents AS d ON d.document_id = c.document_id
JOIN guides AS g ON g.guide_id = c.guide_id
WHERE c.source_type = 'guide'
ORDER BY g.guide_id, c.chunk_index
"""


def export_guide_chunks(connection: sqlite3.Connection, out_path: Path) -> int:
    """법령·판례와 같은 Chroma 입력 규격(JSONL)으로 추출한다."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for row in connection.execute(EXPORT_SQL):
            chunk = {
                "chunk_id": row["chunk_id"],
                "doc_id": row["document_id"],
                "chunk_index": row["chunk_index"],
                "text": row["content"],
                "metadata": {
                    # 조문 ID 가 아니라 안내의 안정적 논리 ID 다. 평가 정답도 이것을 쓴다.
                    "article_id": row["guide_id"],
                    "title": row["title"],
                    "doc_type": "guide",
                    "article_no": "",
                    "article_title": row["topic"],
                    "source_url": row["source_url"],
                    "status": row["status"],
                    "effective_date": row["published_at"],
                    "expiry_date": "",
                    "collected_at": row["collected_at"],
                    "published_at_source": "page" if row["published_at"] else "unknown",
                    "guide_id": row["guide_id"],
                    "guide_type": row["guide_type"],
                    "topic": row["topic"],
                    "checksum": row["checksum"],
                    "token_count": row["token_count"],
                },
            }
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_guide_records(path: Path) -> list[GuideRecord]:
    return [
        GuideRecord(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    paths = resolve_database_paths()
    parser = argparse.ArgumentParser(description="공식 안내를 SQLite 에 적재")
    parser.add_argument("--records", default="data/parsed/guide_records.jsonl")
    parser.add_argument("--database", default=str(paths.relational))
    parser.add_argument("--export", default="data/chunks/guides.jsonl")
    args = parser.parse_args()

    records_path = Path(args.records)
    if not records_path.exists():
        print(f"원천 레코드 파일이 없습니다: {records_path}")
        return 1

    records = read_guide_records(records_path)
    if not records:
        # 빈 입력으로 진행하면 위의 stale 정리가 안내를 통째로 지운다.
        # 경로를 잘못 준 실행 한 번이 코퍼스를 비우면 안 된다.
        print(f"원천 레코드가 비어 있습니다: {records_path}")
        return 1

    database = Path(args.database)
    initialize_relational_database(database)
    with closing(connect_database(database)) as connection:
        summary = load_guide_records(records, connection)

        print(f"\n  DB: {database}")
        print(f"  원천 {len(records)}건 → 안내 {summary.guides}건 · 청크 {summary.chunks}건")
        if summary.removed:
            print(f"  입력에 없어 정리한 안내: {summary.removed}건")
        print("  guide_law_references: 검증된 원천이 없어 적재하지 않음")

        if summary.skipped:
            # 조용히 버리면 2건 넣고 1건만 들어가도 알 수 없다. 그리고 DB 도
            # 되돌린다 — 실패로 보고하면서 절반만 반영해 두면 안 된다.
            connection.rollback()
            print(f"\n  건너뛴 레코드 {len(summary.skipped)}건:")
            for problem in summary.skipped:
                print(f"    {problem}")
            print(
                "\n  DB 를 되돌렸고 청크도 추출하지 않았습니다. "
                "원천을 고친 뒤 다시 실행하세요."
            )
            return 1

        connection.commit()
        exported = export_guide_chunks(connection, Path(args.export))

    print(f"  청크 추출: {args.export} ({exported}건)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
