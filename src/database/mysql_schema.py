"""Versioned MySQL schema for the existing knowledge corpus, including its history.

All source IDs remain intact. A snapshot key scopes every PK and FK so the base
and case corpora (and old releases) can coexist without merging their IDs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

SCHEMA_VERSION = 1
PREFIX = "knowledge_"
KEY_LENGTH = 180  # Four utf8mb4 keys + the ASCII snapshot fit a 3072-byte index.

CASE_EXTENSION = """
ALTER TABLE cases ADD COLUMN canonical_case_key TEXT;
ALTER TABLE cases ADD COLUMN source_name TEXT;
ALTER TABLE cases ADD COLUMN scope_tier TEXT;
ALTER TABLE cases ADD COLUMN court_level INTEGER;
ALTER TABLE cases ADD COLUMN corpus_active INTEGER CHECK(corpus_active IN (0,1));
ALTER TABLE cases ADD COLUMN current_version_checksum TEXT;
ALTER TABLE cases ADD COLUMN observed_at TEXT;
CREATE TABLE case_versions (
    case_id TEXT NOT NULL REFERENCES cases(case_id), checksum TEXT NOT NULL,
    record_json TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    PRIMARY KEY(case_id, checksum));
CREATE TABLE case_observations (
    observation_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL,
    case_id TEXT NOT NULL, checksum TEXT NOT NULL, official_id TEXT NOT NULL,
    source_name TEXT NOT NULL, observed_at TEXT NOT NULL, raw_response TEXT NOT NULL,
    FOREIGN KEY(case_id, checksum) REFERENCES case_versions(case_id, checksum),
    UNIQUE(run_id, official_id, observed_at, checksum));
CREATE TABLE case_aliases (
    official_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id));
"""


def reference_schema() -> sa.MetaData:
    """Reflect only checked-in DDL, never SQL from an imported database."""
    engine = sa.create_engine("sqlite://")
    raw = engine.raw_connection()
    try:
        raw.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        raw.executescript(CASE_EXTENSION)
        metadata = sa.MetaData()
        metadata.reflect(engine)
        return metadata
    finally:
        raw.close()
        engine.dispose()


def _name(*parts):
    return "k_" + hashlib.sha256("/".join(parts).encode()).hexdigest()[:24]


def _scope():
    return sa.Column("_snapshot_id", mysql.CHAR(64, charset="ascii", collation="ascii_bin"),
                     nullable=False, primary_key=True)


def _constraint_key(constraint):
    detail = (str(constraint.sqltext) if isinstance(constraint, sa.CheckConstraint)
              else ",".join(c.name for c in constraint.columns))
    return type(constraint).__name__, detail


def build_schema():
    reference = reference_schema()
    metadata = sa.MetaData()
    options = dict(mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_bin")
    snapshots = sa.Table(
        PREFIX + "snapshots", metadata, _scope(),
        sa.Column("corpus_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(96), nullable=False),
        sa.Column("manifest", sa.JSON, nullable=False),
        sa.UniqueConstraint("corpus_id", "version", name="k_corpus_version"), **options)
    tables = {}
    for source in reference.sorted_tables:
        indexed = {c.name for c in source.primary_key.columns}
        for constraint in sorted(source.constraints, key=_constraint_key):
            if isinstance(constraint, (sa.UniqueConstraint, sa.ForeignKeyConstraint)):
                indexed.update(c.name for c in constraint.columns)
        for index in source.indexes:
            indexed.update(c.name for c in index.columns)
        # Referenced FK keys must have the same length/collation as their child.
        for other in reference.tables.values():
            for fk in other.foreign_keys:
                if fk.column.table is source:
                    indexed.add(fk.column.name)
        columns = [_scope()]
        if not list(source.primary_key.columns):
            columns.append(sa.Column("_row_number", sa.BigInteger, primary_key=True, autoincrement=False))
        for col in source.columns:
            kind = (sa.BigInteger() if isinstance(col.type, sa.Integer) else
                    sa.String(KEY_LENGTH) if col.name in indexed and col.name != "source_url" else mysql.LONGTEXT())
            # Import supplies original date strings, including empty/unknown dates.
            # No implicit MySQL timestamp conversion or truncation is allowed.
            columns.append(sa.Column(col.name, kind, nullable=col.nullable and not col.primary_key,
                                     primary_key=col.primary_key, autoincrement=False))
        if source.name == "documents":
            columns.append(sa.Column("_source_url_sha256", mysql.CHAR(64, charset="ascii", collation="ascii_bin"),
                                     sa.Computed("sha2(source_url, 256)", persisted=True)))
        table = sa.Table(PREFIX + source.name, metadata, *columns, **options)
        table.append_constraint(sa.ForeignKeyConstraint(
            ["_snapshot_id"], [snapshots.c._snapshot_id], name=_name(source.name, "snapshot")))
        for constraint in sorted(source.constraints, key=_constraint_key):
            if isinstance(constraint, sa.UniqueConstraint):
                keys = [c.name for c in constraint.columns]
                if source.name == "documents":
                    keys = ["_source_url_sha256" if k == "source_url" else k for k in keys]
                table.append_constraint(sa.UniqueConstraint("_snapshot_id", *keys,
                                        name=_name(source.name, "unique", *keys)))
            elif isinstance(constraint, sa.CheckConstraint):
                table.append_constraint(sa.CheckConstraint(str(constraint.sqltext),
                                        name=_name(source.name, "check", str(constraint.sqltext))))
            elif isinstance(constraint, sa.ForeignKeyConstraint):
                local = ["_snapshot_id"] + [e.parent.name for e in constraint.elements]
                remote_table = constraint.elements[0].column.table.name
                remote = [PREFIX + remote_table + "._snapshot_id"] + [
                    PREFIX + e.column.table.name + "." + e.column.name for e in constraint.elements]
                table.append_constraint(sa.ForeignKeyConstraint(local, remote,
                    name=_name(source.name, "fk", *local), ondelete=constraint.ondelete))
        for index in sorted(source.indexes, key=lambda i: i.name):
            keys = [c.name for c in index.columns]
            sa.Index(_name(source.name, "index", *keys), table.c._snapshot_id, *(table.c[k] for k in keys))
        tables[source.name] = table
    exports = sa.Table(
        PREFIX + "chunk_exports", metadata, _scope(),
        sa.Column("stream", sa.String(32), primary_key=True),
        sa.Column("position", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("chunk_id", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("envelope", sa.JSON, nullable=False),
        sa.ForeignKeyConstraint(["_snapshot_id", "chunk_id"],
            [tables["chunks"].c._snapshot_id, tables["chunks"].c.chunk_id], name="k_export_chunk"),
        sa.UniqueConstraint("_snapshot_id", "stream", "chunk_id", name="k_export_stream_id"), **options)
    versions = sa.Table(PREFIX + "store_versions", metadata,
        sa.Column("version", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("ddl_sha256", sa.String(64), nullable=False), **options)
    return metadata, tables, snapshots, exports, versions


METADATA, TABLES, SNAPSHOTS, EXPORTS, VERSIONS = build_schema()


def schema_sql():
    dialect = mysql.dialect()
    statements = []
    for table in METADATA.sorted_tables:
        statements.append(str(sa.schema.CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in sorted(table.indexes, key=lambda i: i.name):
            statements.append(str(sa.schema.CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    return "\n\n".join(statements) + "\n"
