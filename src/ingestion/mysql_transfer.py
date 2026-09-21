"""Import immutable, auditable knowledge snapshots and export from MySQL alone.

SQLite is an initial migration input, not a runtime dependency of export. All
source columns, history rows, and JSONL-only metadata live in MySQL after import.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile

import sqlalchemy as sa

from src.database.mysql import configured_engine, initialize
from src.database.mysql_schema import TABLES, SNAPSHOTS, EXPORTS, schema_sql

IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def row_digest(rows):
    """Order-independent multiset identity; duplicates are not collapsed."""
    hashes = sorted(digest(row) for row in rows)
    return {"count": len(hashes), "sha256": digest(hashes)}


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 중복 키: " + key)
        result[key] = value
    return result


def _identifier(value, label):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(label + "는 영문·숫자·._- 1~64자로 지정하세요.")
    return value


def _q(name):
    return '"' + name.replace('"', '""') + '"'


class Source:
    """Read one SQLite transaction plus explicitly supplied export streams."""

    def __init__(self, database, streams, corpus, version):
        self.corpus = _identifier(corpus, "corpus")
        self.version = _identifier(version, "version")
        database = Path(database).resolve()
        if not database.is_file():
            raise ValueError("이전할 SQLite 파일이 없습니다.")
        self.connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("BEGIN")
        try:
            if self.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("이전 원본 SQLite 무결성 오류")
            if self.connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("이전 원본 SQLite 외래키 오류")
            names = {r[0] for r in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if names - set(TABLES):
                raise ValueError("스키마 매핑이 필요한 테이블: " + ", ".join(sorted(names - set(TABLES))))
            if not {"documents", "chunks"} <= names:
                raise ValueError("documents와 chunks 테이블이 필요합니다.")
            self.columns = {}
            for name in sorted(names):
                columns = [r[1] for r in self.connection.execute("PRAGMA table_info(" + _q(name) + ")")]
                if set(columns) - set(TABLES[name].c.keys()):
                    raise ValueError("스키마 매핑이 필요한 컬럼: " + name)
                if any(column.startswith("_") for column in columns):
                    raise ValueError("저장소 내부 컬럼 이름은 원본에서 사용할 수 없습니다: " + name)
                self.columns[name] = columns
            self.streams = self._read_streams(streams)
            self.manifest = {
                "schema": "lens-mysql-snapshot-v1", "corpus": self.corpus, "version": self.version,
                "tables": {name: dict(row_digest(self.rows(name)), columns=columns)
                           for name, columns in self.columns.items()},
                "streams": {name: {"count": len(rows), "sha256": digest(rows)}
                            for name, rows in self.streams.items()},
            }
            self.snapshot_id = digest(self.manifest)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.connection.close()

    def rows(self, name):
        # Structure nodes must be inserted before their children under MySQL FKs.
        order = " ORDER BY ordinal" if name == "law_article_units" else ""
        for row in self.connection.execute("SELECT * FROM " + _q(name) + order):
            value = dict(row)
            for col, entry in value.items():
                kind = TABLES[name].c[col].type
                if entry is not None:
                    if isinstance(kind, sa.Integer) and type(entry) is not int:
                        raise ValueError(f"{name}.{col}: 정수 타입 매핑이 필요합니다.")
                    if isinstance(kind, sa.String):
                        if not isinstance(entry, str):
                            raise ValueError(f"{name}.{col}: 문자열 타입 매핑이 필요합니다.")
                        if kind.length and len(entry) > kind.length:
                            raise ValueError(f"{name}.{col}: 키/색인 문자열은 {kind.length}자 이하가 필요합니다.")
            yield value

    def _read_streams(self, paths):
        if not paths:
            raise ValueError("검색 메타데이터를 보존할 청크 JSONL이 필요합니다.")
        stored = {r["chunk_id"]: dict(r) for r in self.connection.execute("SELECT * FROM chunks")}
        seen = set()
        streams = {}
        for name, path in sorted(paths.items()):
            _identifier(name, "stream")
            if len(name) > 32:
                raise ValueError("stream 이름은 32자 이하로 지정하세요.")
            rows = []
            local = set()
            with Path(path).open(encoding="utf-8-sig") as source:
                for line in source:
                    if not line.strip():
                        continue
                    row = json.loads(line, object_pairs_hook=_json_pairs)
                    if not isinstance(row, dict) or not isinstance(row.get("chunk_id"), str):
                        raise ValueError("청크 형식 오류: " + name)
                    cid = row["chunk_id"]
                    db = stored.get(cid)
                    if cid in local or db is None:
                        raise ValueError("중복 또는 DB에 없는 청크: " + cid)
                    if (row.get("doc_id"), row.get("chunk_index"), row.get("text")) != (
                            db["document_id"], db["chunk_index"], db["content"]):
                        raise ValueError("DB와 청크 본문·문서·순번 불일치: " + cid)
                    meta = row.get("metadata")
                    if not isinstance(meta, dict) or meta.get("doc_type") != db["source_type"]:
                        raise ValueError("청크 메타데이터 유형 불일치: " + cid)
                    if not row["text"] or not meta.get("source_url"):
                        raise ValueError("청크 본문·출처 URL이 필요합니다: " + cid)
                    encoded(row)  # Reject non-finite JSON numbers before importing.
                    local.add(cid)
                    seen.add(cid)
                    rows.append(row)
            streams[name] = rows
        if seen != set(stored):
            raise ValueError(f"JSONL에 없는 DB 청크가 {len(set(stored) - seen)}개 있습니다.")
        return streams


@contextmanager
def open_source(database, streams, corpus, version):
    source = Source(database, streams, corpus, version)
    try:
        yield source
    finally:
        source.close()


def _batches(rows, size=64):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def import_source(engine, source):
    """Publish every table and export stream in one transaction, or none."""
    with engine.begin() as connection:
        existing = connection.execute(sa.select(SNAPSHOTS.c._snapshot_id).where(
            SNAPSHOTS.c.corpus_id == source.corpus, SNAPSHOTS.c.version == source.version)).scalar_one_or_none()
        if existing:
            if existing != source.snapshot_id:
                raise ValueError("같은 코퍼스 버전의 내용이 달라졌습니다. 새 버전을 지정하세요.")
            verify_snapshot(connection, existing)
            return {"snapshot_id": existing, "created": False}
        connection.execute(SNAPSHOTS.insert().values(_snapshot_id=source.snapshot_id,
            corpus_id=source.corpus, version=source.version, manifest=source.manifest))
        # Declarative FK order, including extended case history tables.
        source_names = {table.name: name for name, table in TABLES.items()}
        for table in SNAPSHOTS.metadata.sorted_tables:
            name = source_names.get(table.name)
            if name not in source.columns:
                continue
            rows = []
            for index, value in enumerate(source.rows(name)):
                value["_snapshot_id"] = source.snapshot_id
                if "_row_number" in table.c:
                    value["_row_number"] = index
                rows.append(value)
                if len(rows) == 64:
                    connection.execute(table.insert(), rows)
                    rows = []
            if rows:
                connection.execute(table.insert(), rows)
        for name, rows in source.streams.items():
            values = ({"_snapshot_id": source.snapshot_id, "stream": name, "position": index,
                       "chunk_id": row["chunk_id"], "envelope": {k: v for k, v in row.items() if k != "text"}}
                      for index, row in enumerate(rows))
            for batch in _batches(values):
                connection.execute(EXPORTS.insert(), batch)
        verify_snapshot(connection, source.snapshot_id)
    return {"snapshot_id": source.snapshot_id, "created": True}


def _manifest(connection, snapshot_id):
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
        raise ValueError("snapshot_id는 SHA-256이어야 합니다.")
    manifest = connection.execute(sa.select(SNAPSHOTS.c.manifest).where(
        SNAPSHOTS.c._snapshot_id == snapshot_id)).scalar_one_or_none()
    if manifest is None or digest(manifest) != snapshot_id:
        raise ValueError("스냅샷이 없거나 manifest 해시가 다릅니다.")
    return manifest


def export_rows(connection, snapshot_id, stream):
    chunks = TABLES["chunks"]
    statement = sa.select(EXPORTS.c.envelope, chunks.c.content).join(chunks,
        sa.and_(EXPORTS.c._snapshot_id == chunks.c._snapshot_id, EXPORTS.c.chunk_id == chunks.c.chunk_id)
    ).where(EXPORTS.c._snapshot_id == snapshot_id, EXPORTS.c.stream == stream).order_by(EXPORTS.c.position)
    for envelope, content in connection.execute(statement):
        yield dict(envelope, text=content)


def verify_snapshot(connection, snapshot_id):
    manifest = _manifest(connection, snapshot_id)
    for name, table in TABLES.items():
        spec = manifest["tables"].get(name)
        if spec:
            rows = connection.execute(sa.select(*(table.c[c] for c in spec["columns"])).where(
                table.c._snapshot_id == snapshot_id)).mappings()
            actual = row_digest(dict(row) for row in rows)
            if actual != {key: spec[key] for key in ("count", "sha256")}:
                raise ValueError("MySQL 원문·관계·이력 검증 실패: " + name)
        elif connection.execute(sa.select(sa.func.count()).select_from(table).where(
                table.c._snapshot_id == snapshot_id)).scalar_one():
            raise ValueError("manifest에 없는 원문 테이블 데이터: " + name)
    actual_streams = set(connection.execute(sa.select(EXPORTS.c.stream).where(
        EXPORTS.c._snapshot_id == snapshot_id).distinct()).scalars())
    expected_streams = {name for name, spec in manifest["streams"].items() if spec["count"]}
    if actual_streams != expected_streams:
        raise ValueError("manifest와 내보내기 stream 목록이 다릅니다.")
    for name, spec in manifest["streams"].items():
        rows = list(export_rows(connection, snapshot_id, name))
        if len(rows) != spec["count"] or digest(rows) != spec["sha256"]:
            raise ValueError("MySQL 검색 청크·메타데이터 검증 실패: " + name)
    return manifest


def export_snapshot(engine, snapshot_id, output):
    """Create a new directory. No original DB/JSONL is read by this operation."""
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("기존 배포 경로는 덮어쓰지 않습니다: " + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mysql-export-", dir=output.parent) as temp:
        staging = Path(temp) / "release"
        staging.mkdir()
        with engine.begin() as connection:
            manifest = verify_snapshot(connection, snapshot_id)
            files = {}
            for stream in manifest["streams"]:
                _identifier(stream, "stream")
                path = staging / (stream + ".jsonl")
                with path.open("wb") as handle:
                    for row in export_rows(connection, snapshot_id, stream):
                        handle.write(encoded(row) + b"\n")
                with path.open("rb") as handle:
                    files[path.name] = hashlib.file_digest(handle, "sha256").hexdigest()
            result = {"schema": "lens-mysql-chunks-v1", "snapshot_id": snapshot_id,
                      "snapshot": manifest, "files": files, "index_status": "not_built"}
            (staging / "manifest.json").write_bytes(encoded(result) + b"\n")
        staging.rename(output)
    return result


def trace_chunk(engine, snapshot_id, chunk_id):
    """Follow one release's evidence ID to its original document and source row."""
    with engine.begin() as connection:
        manifest = _manifest(connection, snapshot_id)

        def one(name, key, value):
            spec = manifest["tables"].get(name)
            if not spec:
                return None
            table = TABLES[name]
            row = connection.execute(sa.select(*(table.c[c] for c in spec["columns"])).where(
                table.c._snapshot_id == snapshot_id, table.c[key] == value)).mappings().one_or_none()
            return dict(row) if row is not None else None

        chunk = one("chunks", "chunk_id", chunk_id)
        if chunk is None:
            raise ValueError("해당 스냅샷에 청크가 없습니다.")
        document = one("documents", "document_id", chunk["document_id"])
        source = {}
        for name, key in (("law_articles", "article_id"), ("cases", "case_id"), ("guides", "guide_id")):
            if chunk.get(key):
                source[name] = one(name, key, chunk[key])
        if article := source.get("law_articles"):
            source["law_versions"] = one("law_versions", "law_version_id", article["law_version_id"])
            if reference := one("law_article_sources", "article_id", article["article_id"]):
                source["law_article_sources"] = reference
                if reference.get("snapshot_id"):
                    source["law_source_snapshots"] = one("law_source_snapshots", "snapshot_id", reference["snapshot_id"])
        envelopes = [dict(row) for row in connection.execute(sa.select(
            EXPORTS.c.stream, EXPORTS.c.position, EXPORTS.c.envelope).where(
                EXPORTS.c._snapshot_id == snapshot_id, EXPORTS.c.chunk_id == chunk_id)).mappings()]
        return {"snapshot_id": snapshot_id, "corpus": manifest["corpus"], "version": manifest["version"],
                "chunk": chunk, "document": document, "source": source, "exports": envelopes}


def _streams(values):
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--chunks는 stream=파일.jsonl 형식입니다.")
        name, path = value.split("=", 1)
        if name in result:
            raise ValueError("중복 stream: " + name)
        result[name] = Path(path)
    return result


def main(argv=None):
    from dotenv import load_dotenv
    parser = argparse.ArgumentParser(description="MySQL 지식 스냅샷 이전·검증·청크 내보내기")
    parser.add_argument("--env-file", type=Path, help="명시한 접속 설정 파일을 프로세스 환경보다 우선 사용")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("schema")
    for command in ("inspect-source", "import"):
        p = sub.add_parser(command)
        p.add_argument("--database", type=Path, required=True)
        p.add_argument("--chunks", action="append", required=True)
        p.add_argument("--corpus", required=True)
        p.add_argument("--version", required=True)
    for command in ("verify", "export", "trace"):
        p = sub.add_parser(command)
        p.add_argument("--snapshot", required=True)
        if command == "export":
            p.add_argument("--output", type=Path, required=True)
        elif command == "trace":
            p.add_argument("--chunk", required=True)
    args = parser.parse_args(argv)
    if args.env_file:
        if not args.env_file.is_file():
            parser.error("지정한 접속 설정 파일이 없습니다.")
        load_dotenv(args.env_file, override=True)
    else:
        load_dotenv()
    if args.command == "schema":
        print(schema_sql())
        return 0
    if args.command == "inspect-source":
        with open_source(args.database, _streams(args.chunks), args.corpus, args.version) as source:
            print(json.dumps(dict(source.manifest, snapshot_id=source.snapshot_id), ensure_ascii=False, indent=2))
        return 0
    engine = configured_engine()
    try:
        if args.command == "init":
            result = initialize(engine)
        elif args.command == "import":
            with open_source(args.database, _streams(args.chunks), args.corpus, args.version) as source:
                result = import_source(engine, source)
        elif args.command == "verify":
            with engine.begin() as connection:
                result = verify_snapshot(connection, args.snapshot)
        elif args.command == "trace":
            result = trace_chunk(engine, args.snapshot, args.chunk)
        else:
            result = export_snapshot(engine, args.snapshot, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
