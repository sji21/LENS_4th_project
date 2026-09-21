"""Apply explicit document replacements/deletions as a new MySQL snapshot.

The parent is read exclusively from MySQL. SQLite is disposable compiler work
space for the existing parsers, never the authoritative input or live store.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime
import json
from pathlib import Path
import tempfile

import sqlalchemy as sa

from src.database.mysql import configured_engine
from src.database.mysql_schema import CASE_EXTENSION, TABLES, reference_schema
from src.database.relational import initialize_relational_database, connect_database
from src.ingestion import load_laws, load_cases, load_guides
from src.ingestion.mysql_transfer import (
    _identifier, digest, encoded, export_rows, export_snapshot, import_source,
    open_source, verify_snapshot,
)

SCHEMA = "lens-mysql-document-changes-v1"
REFERENCE = reference_schema()


def q(name):
    return '"' + name.replace('"', '""') + '"'


def insert(connection, table, row):
    names = list(row)
    connection.execute(f"INSERT INTO {q(table)} ({','.join(q(n) for n in names)}) "
                       f"VALUES ({','.join('?' for _ in names)})", tuple(row[n] for n in names))


def read_plan(path):
    from src.ingestion.knowledge_release import _read_json
    return validate_plan(_read_json(path))


def validate_plan(plan):
    if (set(plan) != {"schema", "parent_snapshot", "corpus", "version", "observed_at", "documents"}
            or plan["schema"] != SCHEMA or not isinstance(plan["documents"], list)
            or not plan["documents"]):
        raise ValueError("문서 변경 요청 형식이 잘못됐습니다.")
    _identifier(plan["corpus"], "corpus")
    _identifier(plan["version"], "version")
    stamp = datetime.fromisoformat(plan["observed_at"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("observed_at에는 시간대가 필요합니다.")
    seen = set()
    for change in plan["documents"]:
        if (not isinstance(change, dict)
                or set(change) - {"operation", "document_id", "doc_type", "records", "metadata", "fields", "case_provenance"}
                or change.get("operation") not in {"replace", "delete"}
                or change.get("doc_type") not in {"law", "decree", "rule", "case", "guide"}
                or not isinstance(change.get("document_id"), str) or not change["document_id"]
                or change["document_id"] in seen):
            raise ValueError("문서 변경 대상·작업 종류가 잘못됐거나 중복됐습니다.")
        seen.add(change["document_id"])
        if change["operation"] == "delete":
            if set(change) != {"operation", "document_id", "doc_type"}:
                raise ValueError("삭제 요청에는 원문·메타데이터를 넣지 마세요.")
        elif not isinstance(change.get("records"), list) or not change["records"]:
            raise ValueError("문서 교체에는 해당 문서의 전체 레코드가 필요합니다.")
    return plan


def restore_parent(engine, parent, database):
    initialize_relational_database(database)
    db = connect_database(database)
    try:
        db.executescript(CASE_EXTENSION)
        db.execute("DELETE FROM schema_migrations")
        # Article units can reference another row in the same table. MySQL row
        # iteration need not return a parent unit before its child unit.
        db.execute("PRAGMA defer_foreign_keys=ON")
        streams = {}
        with engine.begin() as connection:
            manifest = verify_snapshot(connection, parent)
            for table in REFERENCE.sorted_tables:
                spec = manifest["tables"].get(table.name)
                if not spec:
                    continue
                target = TABLES[table.name]
                selected = sa.select(*(target.c[n] for n in spec["columns"])).where(target.c._snapshot_id == parent)
                for row in connection.execute(selected).mappings():
                    insert(db, table.name, dict(row))
            for stream in manifest["streams"]:
                streams[stream] = list(export_rows(connection, parent, stream))
        db.commit()
        return db, manifest, streams
    except BaseException:
        db.close()
        raise


def descendants(db, document_id):
    root = db.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
    if root is None:
        return []
    queue, seen = [("documents", dict(root))], set()
    result = []
    for name, row in queue:
        identity = (name, digest(row))
        if identity in seen:
            continue
        seen.add(identity)
        result.append((name, row))
        for child in REFERENCE.tables.values():
            for fk in child.foreign_key_constraints:
                if fk.referred_table.name != name:
                    continue
                condition = " AND ".join(q(e.parent.name) + " IS ?" for e in fk.elements)
                values = tuple(row[e.column.name] for e in fk.elements)
                queue.extend((child.name, dict(r)) for r in db.execute(
                    f"SELECT * FROM {q(child.name)} WHERE {condition}", values))
    return result


def remove_rows(db, affected):
    # Reverse topological table order also handles rows reached by multiple FK
    # paths (e.g. observations reference a case version as well as case metadata).
    order = {t.name: i for i, t in enumerate(REFERENCE.sorted_tables)}
    for name, row in sorted(affected, key=lambda item: order[item[0]], reverse=True):
        keys = [c.name for c in REFERENCE.tables[name].primary_key.columns] or list(row)
        db.execute(f"DELETE FROM {q(name)} WHERE " + " AND ".join(q(n) + " IS ?" for n in keys),
                   tuple(row[n] for n in keys))


def compile_document(change, directory, observed_at):
    database = directory / "compiled.sqlite3"
    initialize_relational_database(database)
    db = connect_database(database)
    kind = change["doc_type"]
    record_type, loader, exporter = (
        (load_cases.CaseRecord, load_cases.load_case_records, load_cases.export_case_chunks) if kind == "case" else
        (load_guides.GuideRecord, load_guides.load_guide_records, load_guides.export_guide_chunks) if kind == "guide" else
        (load_laws.LawArticleRecord, load_laws.load_records, load_laws.export_chunks))
    try:
        allowed = {f.name for f in fields(record_type)}
        records = []
        for row in change["records"]:
            if not isinstance(row, dict) or set(row) - allowed:
                raise ValueError("알 수 없는 원문 레코드 필드가 있습니다.")
            records.append(record_type(**row))
        if kind in {"case", "guide"} and len(records) != 1:
            raise ValueError("판례·안내 교체는 문서당 원문 한 건이어야 합니다.")
        if kind in {"law", "decree", "rule"}:
            ids = [load_laws.article_row_id_of(r) for r in records]
            if len(ids) != len(set(ids)) or any(r.document_type != kind for r in records):
                raise ValueError("중복 조문 키 또는 법령 종류 불일치")
            if len({load_laws.law_version_id_of(r) for r in records}) != 1:
                raise ValueError("하나의 법령 판본에 속한 전체 조문을 지정하세요.")
        if kind == "guide":
            for record in records:
                if any(not isinstance(getattr(record, key), str) or not getattr(record, key).strip()
                       for key in ("guide_id", "title", "agency", "guide_type", "topic", "source_url", "content")):
                    raise ValueError("안내의 필수 원문·출처 정보가 비어 있습니다.")
                if record.published_at and record.published_at_source != "page":
                    raise ValueError("공식 페이지에서 확인한 게시일만 published_at에 지정하세요.")
        summary = loader(records, db)
        if summary.skipped:
            raise ValueError("원문 레코드 검증 실패: " + str(summary.skipped))
        documents = list(db.execute("SELECT document_id,document_type FROM documents"))
        if [tuple(r) for r in documents] != [(change["document_id"], kind)]:
            raise ValueError("원문에서 생성한 문서 ID·종류가 교체 대상과 다릅니다.")
        for name in ("documents", "chunks"):
            db.execute(f"UPDATE {name} SET created_at=?", (observed_at,))
        db.execute("UPDATE documents SET updated_at=?", (observed_at,))
        db.commit()
        path = directory / "chunks.jsonl"
        exporter(db, path)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if not rows:
            raise ValueError("검색 청크가 없는 원문은 교체할 수 없습니다.")
        compiled = {t.name: [dict(r) for r in db.execute(f"SELECT * FROM {q(t.name)}")]
                    for t in REFERENCE.sorted_tables
                    if t.name not in {"schema_migrations", "case_versions", "case_observations", "case_aliases"}}
        return compiled, rows
    finally:
        db.close()


def case_history(db, change, rows, old_history, plan):
    expected = {"canonical_case_key", "source_name", "scope_tier", "court_level", "corpus_active",
                "official_id", "raw_response"}
    provenance = change.get("case_provenance", {})
    if (set(provenance) != expected or any(not isinstance(provenance[k], str) or not provenance[k].strip()
            for k in expected - {"court_level", "corpus_active"})
            or type(provenance["court_level"]) is not int or type(provenance["corpus_active"]) is not bool):
        raise ValueError("판례의 식별 정보·수집 출처·원응답이 필요합니다.")
    record = change["records"][0]
    case_id = record["case_id"]
    old_case = next((r for name, r in old_history if name == "cases"), None)
    if old_case and old_case.get("canonical_case_key") not in {None, "", provenance["canonical_case_key"]}:
        raise ValueError("동일 case_id의 사건 식별자를 바꿀 수 없습니다.")
    if db.execute("SELECT 1 FROM cases WHERE canonical_case_key=? AND case_id<>?",
                  (provenance["canonical_case_key"], case_id)).fetchone():
        raise ValueError("이미 저장된 사건 식별자입니다. 기존 case_id를 사용하세요.")
    for table, row in old_history:
        if table in {"case_versions", "case_observations", "case_aliases"}:
            insert(db, table, row)
    version_record = {"record": record, "provenance": provenance}
    checksum = digest(version_record)
    stamp = plan["observed_at"]
    db.execute("""UPDATE cases SET canonical_case_key=?,source_name=?,scope_tier=?,court_level=?,
                  corpus_active=?,current_version_checksum=?,observed_at=? WHERE case_id=?""",
               tuple(provenance[k] for k in ("canonical_case_key", "source_name", "scope_tier", "court_level", "corpus_active"))
               + (checksum, stamp, case_id))
    db.execute("""INSERT INTO case_versions VALUES (?,?,?,?,?) ON CONFLICT(case_id,checksum)
                  DO UPDATE SET last_seen=excluded.last_seen""",
               (case_id, checksum, encoded(version_record).decode(), stamp, stamp))
    alias = db.execute("SELECT case_id FROM case_aliases WHERE official_id=?", (provenance["official_id"],)).fetchone()
    if alias and alias[0] != case_id:
        raise ValueError("공식 판례 ID가 다른 사건에 연결돼 있습니다.")
    db.execute("INSERT OR IGNORE INTO case_aliases VALUES (?,?)", (provenance["official_id"], case_id))
    db.execute("""INSERT INTO case_observations(run_id,case_id,checksum,official_id,source_name,observed_at,raw_response)
                  VALUES (?,?,?,?,?,?,?)""", (digest(plan), case_id, checksum, provenance["official_id"],
                  provenance["source_name"], stamp, provenance["raw_response"]))
    for row in rows:
        row["metadata"].update({k: provenance[k] for k in
            ("canonical_case_key", "source_name", "scope_tier", "court_level", "corpus_active")})
        row["metadata"].update(version_checksum=checksum, observed_at=stamp)


def apply_document(db, streams, change, directory, plan):
    old = descendants(db, change["document_id"])
    if old and old[0][1]["document_type"] != change["doc_type"]:
        raise ValueError("기존 문서와 요청한 자료 종류가 다릅니다.")
    previous = [row for items in streams.values() for row in items if row["doc_id"] == change["document_id"]]
    if change["operation"] == "delete":
        if not old:
            raise ValueError("삭제할 문서가 부모 스냅샷에 없습니다.")
        remove_rows(db, old)
        for stream, items in streams.items():
            streams[stream] = [r for r in items if r["doc_id"] != change["document_id"]]
        return
    compiled, chunks = compile_document(change, directory, plan["observed_at"])
    if (any(name == "law_source_snapshots" for name, _ in old)
            and not compiled["law_source_snapshots"]):
        raise ValueError("기존 법령 수집 원문이 있으므로 새 source_text도 제공해야 합니다.")
    remove_rows(db, old)
    for table in REFERENCE.sorted_tables:
        for row in compiled.get(table.name, []):
            # laws are parents of versions, shared across documents. Never
            # silently rewrite shared descriptive data while editing one version.
            if table.name == "laws":
                existing = db.execute("SELECT * FROM laws WHERE law_id=?", (row["law_id"],)).fetchone()
                if existing:
                    if dict(existing) != row:
                        raise ValueError("다른 판본과 공유하는 법령 기본 정보가 다릅니다.")
                    continue
            insert(db, table.name, row)
    if change["doc_type"] == "case":
        case_history(db, change, chunks, old, plan)
    elif "case_provenance" in change:
        raise ValueError("판례 외 자료에 case_provenance를 지정할 수 없습니다.")
    overrides = change.get("metadata", {})
    if not isinstance(overrides, dict) or set(overrides) - {r["chunk_id"] for r in chunks}:
        raise ValueError("metadata 키는 새 문서의 실제 chunk_id여야 합니다.")
    prior_keys = {key for r in previous for key in r["metadata"]}
    fields = change.get("fields", {})
    core = {"chunk_id", "doc_id", "chunk_index", "text", "metadata"}
    prior_fields = {key for r in previous for key in set(r) - core}
    if not isinstance(fields, dict) or set(fields) - {r["chunk_id"] for r in chunks}:
        raise ValueError("fields 키는 새 문서의 실제 chunk_id여야 합니다.")
    for row in chunks:
        extra = overrides.get(row["chunk_id"], {})
        if not isinstance(extra, dict):
            raise ValueError("metadata 값은 객체여야 합니다.")
        if any(key in row["metadata"] and row["metadata"][key] != value for key, value in extra.items()):
            raise ValueError("원문에서 생성한 검색 메타데이터를 덮어쓸 수 없습니다.")
        row["metadata"].update(extra)
        if prior_keys - row["metadata"].keys():
            raise ValueError("기존 JSONL 전용 메타데이터의 새 값을 명시하세요: " +
                             ", ".join(sorted(prior_keys - row["metadata"].keys())))
        top = fields.get(row["chunk_id"], {})
        if not isinstance(top, dict) or set(top) & core or prior_fields - top.keys():
            raise ValueError("기존 추가 최상위 필드의 새 값을 fields에 명시하세요.")
        row.update(top)
    assigned = {name for name, items in streams.items() if any(r["doc_id"] == change["document_id"] for r in items)}
    stream = next(iter(assigned)) if len(assigned) == 1 else (
        "civil" if set(streams) == {"civil"} else {"case": "cases", "guide": "guides"}.get(change["doc_type"], "laws"))
    if len(assigned) > 1 or stream not in streams or (stream == "civil" and any(r["metadata"].get("title") != "민법" for r in chunks)):
        raise ValueError("문서의 검색 stream을 결정할 수 없습니다.")
    for name, items in streams.items():
        streams[name] = [r for r in items if r["doc_id"] != change["document_id"]]
    streams[stream].extend(chunks)


@contextmanager
def prepare(engine, plan):
    validate_plan(plan)
    with tempfile.TemporaryDirectory(prefix="lens-mysql-ingest-") as temp:
        root = Path(temp)
        database = root / "candidate.sqlite3"
        db, parent, streams = restore_parent(engine, plan["parent_snapshot"], database)
        try:
            if parent["corpus"] != plan["corpus"] or parent["version"] == plan["version"]:
                raise ValueError("부모와 같은 코퍼스의 새 버전을 지정하세요.")
            db.execute("PRAGMA defer_foreign_keys=ON")
            for index, change in enumerate(sorted(plan["documents"], key=lambda c: c["document_id"])):
                directory = root / str(index)
                directory.mkdir()
                apply_document(db, streams, change, directory, plan)
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("변경 후 원문 관계 무결성이 깨졌습니다.")
            db.commit()
        finally:
            db.close()
        paths = {}
        for stream, items in streams.items():
            paths[stream] = root / (stream + ".jsonl")
            paths[stream].write_bytes(b"".join(encoded(row) + b"\n" for row in items))
        with open_source(database, paths, plan["corpus"], plan["version"]) as source:
            from src.ingestion.knowledge_release import file_hash
            source.manifest["ingestion"] = {"schema": SCHEMA, "parent_snapshot": plan["parent_snapshot"],
                "changes_sha256": digest(plan), "observed_at": plan["observed_at"],
                "parsers": {"laws": load_laws.PARSER_VERSION, "cases": load_cases.PARSER_VERSION,
                            "guides": load_guides.PARSER_VERSION},
                "parser_files": {name: file_hash(Path(__file__).with_name(name)) for name in
                                 ("mysql_ingest.py", "load_laws.py", "load_cases.py", "load_guides.py", "law_structure.py")}}
            source.snapshot_id = digest(source.manifest)
            yield source


def main(argv=None):
    from dotenv import load_dotenv
    parser = argparse.ArgumentParser(description="MySQL 부모 스냅샷 → 원문 교체·삭제·청킹 → 새 스냅샷")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("command", choices=("inspect", "apply"))
    parser.add_argument("--changes", type=Path, required=True)
    parser.add_argument("--export", type=Path)
    args = parser.parse_args(argv)
    if args.env_file and not args.env_file.is_file():
        parser.error("지정한 접속 설정 파일이 없습니다.")
    load_dotenv(args.env_file, override=bool(args.env_file))
    plan = read_plan(args.changes)
    if args.command == "inspect" and args.export:
        parser.error("inspect에는 --export를 사용할 수 없습니다.")
    if args.export and args.export.exists():
        parser.error("기존 내보내기 경로는 덮어쓰지 않습니다.")
    engine = configured_engine()
    try:
        with prepare(engine, plan) as source:
            result = {"snapshot_id": source.snapshot_id, "manifest": source.manifest,
                      "applied": args.command == "apply"}
            if args.command == "apply":
                result.update(import_source(engine, source))
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            if args.export:
                export_snapshot(engine, source.snapshot_id, args.export)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
