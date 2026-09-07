"""PATCH-013 법령 SQLite 적재 테스트."""

from __future__ import annotations

import gc
import json
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from src.database.relational import connect_database, initialize_relational_database
from src.ingestion.fetch_law_mock import parse_law_header
from src.ingestion.load_laws import (
    LawArticleRecord,
    article_row_id_of,
    chunk_body,
    export_chunks,
    load_records,
    logical_article_id_of,
    read_records,
    write_records,
)


def make_record(**overrides) -> LawArticleRecord:
    base = {
        "law_name": "주택임대차보호법",
        "law_type": "법률",
        "ministry": "법무부",
        "law_code": "276291",
        "proclamation_number": "법률 제21065호",
        "proclaimed_at": "2025-10-01",
        "effective_from": "2026-01-02",
        "article_number": "제3조의2",
        "article_title": "보증금의 회수",
        "content": "② 대항요건과 확정일자를 갖춘 임차인은 우선하여 변제받을 권리가 있다.",
        "source_url": "https://www.law.go.kr/법령/주택임대차보호법/제3조의2",
        "collected_at": "2026-08-28",
    }
    base.update(overrides)
    return LawArticleRecord(**base)


class RecordTests(unittest.TestCase):
    def test_valid_record_has_no_problems(self):
        self.assertEqual(make_record().validate(), [])

    def test_missing_required_field_is_reported(self):
        problems = make_record(content="").validate()
        self.assertTrue(any("content" in p for p in problems))

    def test_rejects_unknown_status_and_bad_date(self):
        problems = make_record(status="현행", effective_from="2026.01.02").validate()
        self.assertTrue(any("status" in p for p in problems))
        self.assertTrue(any("effective_from" in p for p in problems))

    def test_records_round_trip_through_jsonl(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            write_records([make_record()], path)
            self.assertEqual(read_records(path), [make_record()])


class IdentifierTests(unittest.TestCase):
    def test_logical_id_stays_at_article_level(self):
        """평가 라벨과 맞춰야 하므로 항·호가 붙으면 안 된다."""
        record = make_record(paragraph_number="2", item_number="1")
        self.assertEqual(
            logical_article_id_of(record), "주택임대차보호법-제3조의2"
        )

    def test_surrogate_id_separates_paragraphs(self):
        """SQLite 대리키는 항·호를 구분해야 UNIQUE 제약에 걸리지 않는다."""
        first = article_row_id_of(make_record(paragraph_number="1"))
        second = article_row_id_of(make_record(paragraph_number="2"))
        self.assertNotEqual(first, second)

    def test_surrogate_id_is_deterministic(self):
        self.assertEqual(
            article_row_id_of(make_record()), article_row_id_of(make_record())
        )

    def test_chunk_body_carries_its_own_source(self):
        body = chunk_body(make_record())
        self.assertTrue(body.startswith("[주택임대차보호법 제3조의2(보증금의 회수)]"))


class HeaderParsingTests(unittest.TestCase):
    HEAD = (
        "주택임대차보호법 ( 약칭: 주택임대차법 )\n"
        "[시행 2026. 1. 2.] [법률 제21065호, 2025. 10. 1., 타법개정]\n"
        "법무부 (법무심의관실), 02-2110-3164\n"
    )

    def test_extracts_dates_number_and_ministry(self):
        meta = parse_law_header(self.HEAD)
        self.assertEqual(meta["effective_from"], "2026-01-02")
        self.assertEqual(meta["proclaimed_at"], "2025-10-01")
        self.assertEqual(meta["proclamation_number"], "법률 제21065호")
        self.assertEqual(meta["ministry"], "법무부")

    def test_missing_header_returns_blanks_not_errors(self):
        meta = parse_law_header("본문만 있고 머리말이 없다")
        self.assertEqual(meta["effective_from"], "")
        self.assertEqual(meta["ministry"], "")


class LoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "knowledge.sqlite3"
        initialize_relational_database(self.db)

    def tearDown(self):
        # initialize_relational_database 가 연 sqlite3 연결은 `with` 로 커밋만 되고
        # 닫히지 않는다. Windows 에서는 파일이 잠긴 채 남아 임시 폴더 정리가 실패하므로
        # 수거를 강제한다. (src/database/relational.py 쪽 정리가 필요한 사안)
        gc.collect()
        self._tmp.cleanup()

    def counts(self, connection):
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("documents", "laws", "law_versions", "law_articles", "chunks")
        }

    def test_loads_records_into_related_tables(self):
        records = [
            make_record(article_number="제3조", article_title="대항력 등"),
            make_record(article_number="제3조의2"),
        ]
        with closing(connect_database(self.db)) as connection:
            summary = load_records(records, connection)
            counts = self.counts(connection)

        self.assertEqual(summary.articles, 2)
        self.assertEqual(counts["laws"], 1)
        self.assertEqual(counts["law_versions"], 1)
        self.assertEqual(counts["documents"], 1)
        self.assertEqual(counts["law_articles"], 2)
        self.assertEqual(counts["chunks"], 2)

    def test_reloading_same_records_does_not_duplicate(self):
        records = [make_record(), make_record(article_number="제4조")]
        with closing(connect_database(self.db)) as connection:
            load_records(records, connection)
            first = self.counts(connection)
            load_records(records, connection)
            second = self.counts(connection)
        self.assertEqual(first, second)

    def test_reordering_records_does_not_duplicate(self):
        """chunk_index 를 문서 단위로 세면 순서가 바뀔 때 같은 조문이 중복된다."""
        first = [make_record(article_number=n) for n in ("제1조", "제2조", "제3조")]
        with closing(connect_database(self.db)) as connection:
            load_records(first, connection)
            load_records(list(reversed(first)), connection)
            counts = self.counts(connection)
        self.assertEqual(counts["chunks"], 3)
        self.assertEqual(counts["law_articles"], 3)

    def test_removed_articles_disappear_on_reload(self):
        """조문이 빠진 채 재적재하면 옛 행이 남으면 안 된다."""
        with closing(connect_database(self.db)) as connection:
            load_records(
                [make_record(article_number=n) for n in ("제1조", "제2조", "제3조")],
                connection,
            )
            load_records(
                [make_record(article_number=n) for n in ("제1조", "제3조")], connection
            )
            rows = [
                r[0] for r in connection.execute(
                    "SELECT article_number FROM law_articles ORDER BY article_number"
                )
            ]
        self.assertEqual(rows, ["제1조", "제3조"])

    def test_chunk_index_counts_within_the_article(self):
        """항 단위로 쪼갠 순번이지 입력 순서 번호가 아니다."""
        with closing(connect_database(self.db)) as connection:
            load_records(
                [
                    make_record(article_number="제3조", paragraph_number="1"),
                    make_record(article_number="제4조"),
                    make_record(article_number="제3조", paragraph_number="2"),
                ],
                connection,
            )
            rows = dict(connection.execute(
                "SELECT chunk_id, chunk_index FROM chunks"
            ).fetchall())
        self.assertEqual(sorted(rows.values()), [0, 0, 1])

    def test_invalid_records_are_skipped_and_reported(self):
        with closing(connect_database(self.db)) as connection:
            summary = load_records(
                [make_record(), make_record(article_number="")], connection
            )
            counts = self.counts(connection)
        self.assertEqual(summary.articles, 1)
        self.assertEqual(counts["chunks"], 1)
        self.assertTrue(summary.skipped)

    def test_export_flattens_metadata_for_chroma(self):
        out = self.root / "chunks.jsonl"
        with closing(connect_database(self.db)) as connection:
            load_records([make_record()], connection)
            written = export_chunks(connection, out)

        self.assertEqual(written, 1)
        chunk = json.loads(out.read_text(encoding="utf-8").strip())
        meta = chunk["metadata"]

        # 필터와 채점이 의존하는 값들이 조인 없이 바로 읽혀야 한다
        self.assertEqual(meta["article_id"], "주택임대차보호법-제3조의2")
        self.assertEqual(meta["title"], "주택임대차보호법")
        self.assertEqual(meta["doc_type"], "law")
        self.assertEqual(meta["status"], "current")
        self.assertEqual(meta["effective_date"], "2026-01-02")
        self.assertEqual(meta["expiry_date"], "")

    def test_exported_metadata_has_no_none_or_containers(self):
        """Chroma 는 스칼라만 받는다. None 이나 리스트가 있으면 적재 시 터진다."""
        out = self.root / "chunks.jsonl"
        with closing(connect_database(self.db)) as connection:
            load_records([make_record(effective_to=None)], connection)
            export_chunks(connection, out)

        meta = json.loads(out.read_text(encoding="utf-8").strip())["metadata"]
        for key, value in meta.items():
            self.assertIsNotNone(value, f"{key} 가 None")
            self.assertIsInstance(value, (str, int, float, bool), f"{key} 타입 위반")

    def test_decree_keeps_its_own_document_type(self):
        record = make_record(
            law_name="주택임대차보호법 시행령",
            law_type="시행령",
            law_code="287183",
            document_type="decree",
            proclamation_number="대통령령 제36423호",
        )
        out = self.root / "chunks.jsonl"
        with closing(connect_database(self.db)) as connection:
            load_records([record], connection)
            export_chunks(connection, out)

        meta = json.loads(out.read_text(encoding="utf-8").strip())["metadata"]
        self.assertEqual(meta["doc_type"], "decree")
        self.assertEqual(meta["article_id"], "주택임대차보호법 시행령-제3조의2")


if __name__ == "__main__":
    unittest.main()
