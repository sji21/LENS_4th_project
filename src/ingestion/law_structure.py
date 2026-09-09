"""Lossless segmentation of stored article text, independent of retrieval chunks.

Offsets address LawArticleRecord.content (Unicode code points), not HTML bytes.
Only explicit line-leading numbering is interpreted. Unrecognized text, amendment
notes and provisos remain verbatim; this parser does not infer legal relations.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

PARSER_VERSION = "law-structure-1"
MARKER = re.compile(
    r"(?m)^[ \t]*(?:([①-⑳㉑-㉟㊱-㊿])|([0-9]{1,3})\.(?=[ \t])|([가나다라마바사아자차카타파하])\.(?=[ \t]))"
)


@dataclass(frozen=True)
class ArticleUnit:
    unit_key: str
    parent_key: str | None
    unit_type: str
    unit_number: str
    ordinal: int
    start_offset: int
    end_offset: int
    content: str


def parse_units(content: str) -> list[ArticleUnit]:
    """Root holds the whole text; non-root spans partition it without loss.

Parents indicate numbering hierarchy, not containing text spans. For example a
paragraph node holds its opening text and its item nodes hold the following text.
Unnumbered text is never relabelled as paragraph 1. An orphan subitem remains text.
"""
    units = [ArticleUnit("root", None, "article", "", 0, 0, len(content), content)]
    matches = list(MARKER.finditer(content))
    starts = [(0, None)] if not matches or matches[0].start() else []
    starts.extend((m.start(), m) for m in matches)
    paragraph = item = None
    for i, (start, match) in enumerate(starts, 1):
        end = starts[i][0] if i < len(starts) else len(content)
        key = f"u{i}"
        kind, number, parent = "text", "", "root"
        if match:
            p, n, letter = match.groups()
            if p:
                kind, number = "paragraph", str(int(unicodedata.numeric(p)))
                paragraph, item = key, None
            elif n:
                kind, number, parent = "item", n, paragraph or "root"
                item = key
            elif item:
                kind, number, parent = "subitem", letter, item
        units.append(ArticleUnit(key, parent, kind, number, i, start, end, content[start:end]))
    return units


def store_units(connection, article_id: str, content: str) -> int:
    """Replace structure only; existing retrieval records are never touched."""
    connection.execute("DELETE FROM law_article_units WHERE article_id = ?", (article_id,))
    units = parse_units(content)
    connection.executemany(
        """INSERT INTO law_article_units
           (article_id, unit_key, parent_key, unit_type, unit_number, ordinal,
            start_offset, end_offset, content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(article_id, u.unit_key, u.parent_key, u.unit_type, u.unit_number,
          u.ordinal, u.start_offset, u.end_offset, u.content) for u in units],
    )
    return len(units)


def backfill_units(connection) -> int:
    """Derive units from existing content without inventing missing raw sources."""
    total = 0
    with connection:
        for row in connection.execute("SELECT article_id, content FROM law_articles").fetchall():
            total += store_units(connection, row[0], row[1])
    return total


def export_structure(connection, out_path) -> int:
    """Review JSONL, not a Chroma input. Sources may be explicitly unavailable."""
    import json

    rows = connection.execute(
        """SELECT a.*, l.law_name, v.effective_from, v.effective_to,
                  v.proclamation_number, s.source_url, s.snapshot_id
           FROM law_articles a JOIN law_versions v USING(law_version_id)
           JOIN laws l USING(law_id)
           LEFT JOIN law_article_sources s USING(article_id)
           ORDER BY a.article_id"""
    ).fetchall()
    with out_path.open("x", encoding="utf-8") as target:
        for row in rows:
            payload = dict(row)
            payload["logical_article_id"] = f"{row['law_name']}-{row['article_number']}"
            payload["parser_version"] = PARSER_VERSION
            payload["source_status"] = "record_url" if row["source_url"] else "unavailable"
            payload["units"] = [dict(u) for u in connection.execute(
                "SELECT * FROM law_article_units WHERE article_id=? ORDER BY ordinal", (row["article_id"],)
            )]
            target.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return len(rows)


def main() -> int:
    import argparse
    from contextlib import closing
    from pathlib import Path
    from src.database.relational import connect_database, initialize_relational_database

    parser = argparse.ArgumentParser(description="지정한 DB에 법령 세부 구조를 보완하고 검토용 JSONL 추출")
    parser.add_argument("--database", type=Path, required=True, help="백업한 DB 복사본을 지정하세요")
    parser.add_argument("--out", type=Path, required=True, help="기존 파일은 덮어쓰지 않습니다")
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("기존 DB 파일이 필요합니다")
    if args.out.exists():
        parser.error("출력 파일이 이미 존재합니다")
    if not args.out.parent.is_dir():
        parser.error("출력 디렉터리를 먼저 만드세요")
    initialize_relational_database(args.database)
    with closing(connect_database(args.database)) as connection:
        count = backfill_units(connection)
        exported = export_structure(connection, args.out)
    print(f"조문 {exported}개 · 구조 노드 {count}개; 검색 청크/인덱스 변경 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
