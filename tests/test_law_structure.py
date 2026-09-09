"""Lossless hierarchy, legacy migration and article-level retrieval compatibility."""
from contextlib import closing
from dataclasses import replace
import json
import sqlite3

import pytest

from src.database.relational import SCHEMA_PATH, connect_database, initialize_relational_database
from src.ingestion.law_structure import backfill_units, export_structure, parse_units
from src.ingestion.load_laws import LawArticleRecord, export_chunks, load_records
from src.ingestion.fetch_law_mock import build_records, parse_articles


def record(**overrides):
    values = dict(law_name="시험법", law_type="법률", ministry="시험부", law_code="stable-law",
                  proclamation_number="법률 제1호", proclaimed_at="2025-01-01",
                  effective_from="2025-02-01", content="① 본문\n1. 조건\n가. 세부 조건\n② 예외",
                  source_url="https://example.test/article/3-2", collected_at="2026-09-09",
                  article_number="제3조의2")
    return LawArticleRecord(**(values | overrides))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.sqlite3"
    initialize_relational_database(path)
    with closing(connect_database(path)) as connection:
        yield connection


@pytest.mark.parametrize("text", [
    "번호 없는 본문. 다만, 예외를 적용한다.\n<개정 2025. 1. 1.>",
    "① 조건\n1. 첫째\n가. 목\n나. 다음 목\n2. 둘째\n② 다른 항\n1. 항 아래 호\n",
    "서두\n1. 항번호 없는 호\n가. 목\n② 항\n가. 부모 없는 목은 평문",
    "㉑ 21항\n㊿ 50항\n③ 3항",
    "줄 안의 참조 ①, 제3조제2항과 제3조의2는 번호 시작 아님.",
    "", "삭제 <2025. 1. 1.>", "① 본문\r\n  1. 호\r\n  가. 목\r\n",
], ids=["unnumbered", "nested", "mixed", "large-circled", "inline", "empty", "deleted", "crlf"])
def test_spans_reconstruct_input_without_loss(text):
    units = parse_units(text)
    assert units[0].content == text
    assert "".join(u.content for u in units[1:]) == text
    by_key = {u.unit_key: u for u in units}
    for unit in units[1:]:
        assert text[unit.start_offset:unit.end_offset] == unit.content
        assert by_key[unit.parent_key].ordinal < unit.ordinal


def test_parent_resets_when_new_paragraph_starts():
    units = parse_units("① 본문\n1. 호\n가. 목\n② 다음\n가. 번호처럼 보이지만 부모 호 없음")
    assert [u.unit_type for u in units] == ["article", "paragraph", "item", "subitem", "paragraph", "text"]
    assert units[2].parent_key == units[1].unit_key
    assert units[3].parent_key == units[2].unit_key
    assert units[4].parent_key == "root"
    assert not any(u.unit_number == "1" for u in parse_units("번호 없는 본문"))
    assert not any(u.unit_type == "item" for u in parse_units("2026. 9. 9. 개정"))


def test_reload_preserves_snapshot_and_replaces_units(db, tmp_path):
    source = "시험법\n제3조의2 본문\n부칙 <법률 제1호>\n제1조 경과규정"
    first = record(source_text=source, source_document_url="https://example.test/version/1", source_version_id="v1")
    second = record(article_number="제4조", source_url="https://example.test/article/4", content="번호 없음")
    load_records([first, second], db)
    snap = db.execute("SELECT * FROM law_source_snapshots").fetchone()
    assert snap["source_text"] == source
    assert db.execute("SELECT COUNT(DISTINCT snapshot_id) FROM law_article_sources").fetchone()[0] == 1
    out = tmp_path / "out.jsonl"
    export_chunks(db, out)
    old = {c["metadata"]["article_id"]: c for c in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    assert old["시험법-제4조"]["metadata"]["source_url"] == second.source_url
    assert old["시험법-제3조의2"]["text"].endswith(first.content)
    assert len(old) == 2  # nested units do not become retrieval chunks
    load_records([second, replace(first, content="① 수정")], db)
    assert db.execute("SELECT COUNT(*) FROM law_source_snapshots").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM law_article_units WHERE unit_type='subitem'").fetchone()[0] == 0
    assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_conflicting_sources_fail_without_deleting_existing_rows(db):
    first = record(source_text="원문A", source_document_url="https://example.test/v")
    load_records([first], db)
    before = list(db.execute("SELECT * FROM law_articles"))
    with pytest.raises(ValueError, match="스냅샷 불일치"):
        load_records([first, replace(first, article_number="제4조", source_text="원문B")], db)
    assert list(db.execute("SELECT * FROM law_articles")) == before


def test_failed_insert_rolls_back_article_deletion(db):
    first = record()
    load_records([first], db)
    before = list(db.execute("SELECT * FROM law_articles"))
    with pytest.raises(sqlite3.IntegrityError):
        load_records([replace(first, content="수정"), replace(first, content="중복")], db)
    assert list(db.execute("SELECT * FROM law_articles")) == before


def test_versions_and_source_revisions_are_preserved(db):
    first = record(source_text="첫 원문", source_document_url="https://example.test/v1")
    load_records([first], db)
    load_records([replace(first, source_text="수집 정정 원문")], db)
    later = replace(first, effective_from="2026-01-01", proclamation_number="법률 제2호", source_text="개정판")
    load_records([later], db)
    assert db.execute("SELECT COUNT(*) FROM law_source_snapshots").fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM law_versions").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM law_articles").fetchone()[0] == 2


def test_migration_keeps_legacy_chunks_and_does_not_invent_sources(tmp_path):
    path = tmp_path / "v2.sqlite3"
    legacy = SCHEMA_PATH.read_text(encoding="utf-8").split("-- Additive storage:")[0]
    with sqlite3.connect(path) as connection:
        connection.executescript(legacy)
        connection.execute("INSERT INTO schema_migrations(version) VALUES (2)")
        connection.execute("INSERT INTO documents(document_id,document_type,title,agency,source_url,collected_at,checksum,status,file_path) VALUES ('d','law','옛 법','기관','https://example.test/old','2025-01-01','h','current','')")
    assert initialize_relational_database(path).schema_version == 3
    assert initialize_relational_database(path).schema_version == 3
    with closing(connect_database(path)) as connection:
        assert connection.execute("SELECT title FROM documents").fetchone()[0] == "옛 법"
        assert connection.execute("SELECT COUNT(*) FROM law_source_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM law_article_units").fetchone()[0] == 0


def test_collector_keeps_addenda_but_does_not_index_them(tmp_path, monkeypatch, db):
    from src.ingestion import fetch_law_mock as collector
    text = "시험법\n[시행 2025. 2. 1.] [법률 제1호, 2025. 1. 1., 일부개정]\n제1조(목적) 목적\n제2조 삭제\n부칙 <법률 제1호>\n제1조(시행일) 시행일\n부칙 <법률 제2호>\n제1조(경과조치) 과거 사건"
    monkeypatch.setattr(collector, "RAW_DIR", tmp_path)
    monkeypatch.setattr(collector, "LAWS", [("시험법", "123", "20250201", "법률", None)])
    (tmp_path / "시험법-20250201.txt").write_text(text, encoding="utf-8")
    records = build_records()
    assert len(records) == len(parse_articles(text)) == 1
    load_records(records, db)
    assert db.execute("SELECT source_text FROM law_source_snapshots").fetchone()[0] == text
    assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1


def test_backfill_is_idempotent_and_export_keeps_missing_sources_explicit(db, tmp_path):
    load_records([record()], db)
    db.execute("DELETE FROM law_article_units")
    db.execute("DELETE FROM law_article_sources")
    db.commit()
    before = list(db.execute("SELECT * FROM chunks"))
    assert backfill_units(db) == backfill_units(db)
    assert list(db.execute("SELECT * FROM chunks")) == before
    out = tmp_path / "structure.jsonl"
    assert export_structure(db, out) == 1
    exported = json.loads(out.read_text(encoding="utf-8"))
    assert exported["snapshot_id"] is None
    assert exported["source_status"] == "unavailable"
    assert exported["source_url"] is None
    assert exported["logical_article_id"] == "시험법-제3조의2"
    assert "".join(u["content"] for u in exported["units"][1:]) == exported["content"]
    with pytest.raises(FileExistsError):
        export_structure(db, out)
