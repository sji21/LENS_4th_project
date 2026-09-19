"""Migration gates; MySQL tests are opt-in and use their own temporary database."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import sqlalchemy as sa

from src.database.mysql import configured_engine, initialize
from src.database.mysql_schema import CASE_EXTENSION, SNAPSHOTS, TABLES, EXPORTS
from src.database.relational import initialize_relational_database, connect_database
from src.ingestion.load_laws import LawArticleRecord, load_records, export_chunks
from src.ingestion.load_cases import CaseRecord, load_case_records, export_case_chunks
from src.ingestion.load_guides import GuideRecord, load_guide_records, export_guide_chunks
from src.ingestion.mysql_transfer import open_source, import_source, export_snapshot, verify_snapshot, trace_chunk


@pytest.fixture
def source_files(tmp_path):
    database = tmp_path / "knowledge.sqlite3"
    initialize_relational_database(database)
    connection = connect_database(database)
    law = LawArticleRecord(law_name="테스트법", law_type="법률", ministry="테스트",
        law_code="TEST", proclamation_number="제1호", proclaimed_at="2026-01-01",
        effective_from="2026-01-01", content="① 조건을 확인한다.\n1. 예외를 확인한다.",
        source_url="https://example.test/law/1", collected_at="", article_number="제1조",
        source_text="전문과 부칙\r\n① 조건을 확인한다.", source_document_url="https://example.test/law")
    load_records([law], connection)
    case = CaseRecord(case_id="Case-A", case_number="2026다1234", court_name="대법원",
        decision_date="2026-01-02", case_type="민사", case_name="보증금",
        holding="조건을 검토했다.", summary="요약", full_text="판결 전문\r\n줄바꿈 및 한글 😀",
        source_url="https://example.test/case?query=" + "x" * 2500, collected_at="2026-01-03")
    load_case_records([case], connection)
    guide = GuideRecord(guide_id="guide-1", title="기관 안내", agency="기관", guide_type="공식",
        topic="보증금", published_at="", collected_at="2026-01-03", content="문의 절차\n예외 조건",
        source_url="https://example.test/guide")
    load_guide_records([guide], connection)
    connection.commit()
    connection.executescript(CASE_EXTENSION)
    connection.execute("UPDATE cases SET court_level=0, corpus_active=1")
    connection.execute("INSERT INTO case_versions VALUES (?, ?, ?, ?, ?)",
                       (case.case_id, "checksum-v1", '{"본문":"이력"}', "", "2026-01-03"))
    connection.execute("INSERT INTO case_observations VALUES (1,?,?,?,?,?,?,?)",
        ("run-1", case.case_id, "checksum-v1", "official-1", "공식", "2026-01-03", "원수집 응답\r\n"))
    connection.execute("INSERT INTO case_aliases VALUES (?,?)", ("official-1", case.case_id))
    connection.commit()
    streams = {name: tmp_path / f"{name}.jsonl" for name in ("laws", "cases", "guides")}
    export_chunks(connection, streams["laws"])
    export_case_chunks(connection, streams["cases"])
    export_guide_chunks(connection, streams["guides"])
    connection.close()
    rows = [json.loads(line) for line in streams["cases"].read_text(encoding="utf-8").splitlines()]
    rows[0]["metadata"].update(source_page_sha256="a" * 64, missing_date=None, active=False)
    rows[0]["extra_provenance"] = {"section": "판결요지", "pages": [1, 2]}
    streams["cases"].write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return database, streams


@pytest.fixture
def mysql_engine():
    if os.getenv("LENS_RUN_MYSQL_TESTS") != "1":
        pytest.skip("실제 MySQL 테스트는 LENS_RUN_MYSQL_TESTS=1 및 접속 설정이 필요합니다.")
    admin = configured_engine()
    name = "lens_test_" + uuid.uuid4().hex
    with admin.connect() as c:
        c.exec_driver_sql(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")
    engine = sa.create_engine(admin.url.set(database=name), hide_parameters=True,
                              isolation_level="REPEATABLE READ")
    try:
        initialize(engine)
        yield engine
    finally:
        engine.dispose()
        # Only this fixture's freshly generated database is removed.
        with admin.connect() as c:
            c.exec_driver_sql(f"DROP DATABASE `{name}`")
        admin.dispose()


def test_schema_hash_is_stable_across_processes():
    code = "from src.database.mysql_schema import schema_sql; import hashlib; print(hashlib.sha256(schema_sql().encode()).hexdigest())"
    outputs = [subprocess.check_output([sys.executable, "-c", code]) for _ in range(2)]
    assert outputs[0] == outputs[1]


def test_source_rejects_missing_or_changed_chunks(source_files):
    database, streams = source_files
    rows = [json.loads(line) for line in streams["cases"].read_text(encoding="utf-8").splitlines()]
    rows[0]["text"] += "변조"
    streams["cases"].write_text(json.dumps(rows[0], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="불일치"):
        with open_source(database, streams, "fixture", "v1"):
            pass


def test_source_rejects_unmapped_history_table(source_files):
    database, streams = source_files
    connection = connect_database(database)
    connection.execute("CREATE TABLE unseen_history (id INTEGER)")
    connection.close()
    with pytest.raises(ValueError, match="테이블"):
        with open_source(database, streams, "fixture", "v1"):
            pass


def test_mysql_roundtrip_without_source_and_idempotent_import(mysql_engine, source_files, tmp_path):
    database, streams = source_files
    originals = {name: [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                 for name, path in streams.items()}
    with open_source(database, streams, "fixture", "v1") as source:
        first = import_source(mysql_engine, source)
        assert first["created"] is True
        assert import_source(mysql_engine, source)["created"] is False
        expected = source.manifest
    # Export must work after every input file is unavailable.
    database.rename(database.with_suffix(".saved"))
    for path in streams.values():
        path.rename(path.with_suffix(".saved"))
    output = tmp_path / "export"
    exported = export_snapshot(mysql_engine, first["snapshot_id"], output)
    assert exported["snapshot"] == expected
    assert exported["index_status"] == "not_built"
    for name, rows in originals.items():
        assert [json.loads(line) for line in (output / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()] == rows
    trace = trace_chunk(mysql_engine, first["snapshot_id"], originals["cases"][0]["chunk_id"])
    assert trace["source"]["cases"]["full_text"] == "판결 전문\r\n줄바꿈 및 한글 😀"
    assert trace["source"]["cases"]["court_level"] == 0
    assert trace["document"]["source_url"] == originals["cases"][0]["metadata"]["source_url"]
    assert trace["exports"][0]["envelope"]["metadata"]["source_page_sha256"] == "a" * 64
    with pytest.raises(FileExistsError):
        export_snapshot(mysql_engine, first["snapshot_id"], output)


def test_mysql_corpus_isolation_and_version_immutability(mysql_engine, source_files):
    database, streams = source_files
    ids = []
    for corpus in ("one", "two"):
        with open_source(database, streams, corpus, "v1") as source:
            ids.append(import_source(mysql_engine, source)["snapshot_id"])
    assert ids[0] != ids[1]
    rows = [json.loads(line) for line in streams["cases"].read_text(encoding="utf-8").splitlines()]
    rows[0]["metadata"]["source_page_sha256"] = "b" * 64
    streams["cases"].write_text(json.dumps(rows[0], ensure_ascii=False), encoding="utf-8")
    with open_source(database, streams, "one", "v1") as source:
        with pytest.raises(ValueError, match="새 버전"):
            import_source(mysql_engine, source)
    with mysql_engine.begin() as c:
        for sid in ids:
            verify_snapshot(c, sid)


def test_mysql_rolls_back_late_import_failure(mysql_engine, source_files):
    database, streams = source_files
    with open_source(database, streams, "fixture", "v1") as source:
        # Deliberate corruption after source validation exercises transaction rollback.
        source.streams["cases"][0]["metadata"]["source_page_sha256"] = "tampered"
        with pytest.raises(ValueError, match="메타데이터"):
            import_source(mysql_engine, source)
        with mysql_engine.connect() as c:
            assert c.execute(sa.select(sa.func.count()).select_from(SNAPSHOTS)).scalar_one() == 0
            assert c.execute(sa.select(sa.func.count()).select_from(TABLES["cases"])).scalar_one() == 0


def test_mysql_enforces_scoped_foreign_keys_and_detects_tampering(mysql_engine, source_files):
    database, streams = source_files
    with open_source(database, streams, "fixture", "v1") as source:
        sid = import_source(mysql_engine, source)["snapshot_id"]
    cases = TABLES["cases"]
    with pytest.raises(sa.exc.IntegrityError):
        with mysql_engine.begin() as c:
            c.execute(cases.update().where(cases.c._snapshot_id == sid).values(document_id="missing"))
    with mysql_engine.begin() as c:
        c.execute(cases.update().where(cases.c._snapshot_id == sid).values(full_text="변경된 전문"))
    with mysql_engine.begin() as c:
        with pytest.raises(ValueError, match="cases"):
            verify_snapshot(c, sid)
