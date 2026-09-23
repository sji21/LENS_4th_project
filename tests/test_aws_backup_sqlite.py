"""Backup correctness for the Linux deployment; no production database used."""
import importlib.util
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux deployment utility")
spec = importlib.util.spec_from_file_location(
    "lens_sqlite_backup", Path(__file__).resolve().parents[1] / "deploy/aws/backup-sqlite.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_backup_includes_committed_wal_and_excludes_uncommitted_write(tmp_path):
    source, target = tmp_path / "live.sqlite3", tmp_path / "backup.sqlite3"
    with closing(sqlite3.connect(source)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE sample(value TEXT)")
        writer.execute("INSERT INTO sample VALUES ('committed')")
        writer.commit()
        writer.execute("INSERT INTO sample VALUES ('pending')")
        module.backup(source, target)
        with closing(sqlite3.connect(target)) as recovered:
            assert recovered.execute("SELECT value FROM sample").fetchall() == [("committed",)]
        writer.rollback()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_invalid_source_preserves_previous_backup(tmp_path):
    source, target = tmp_path / "broken.sqlite3", tmp_path / "backup.sqlite3"
    source.write_bytes(b"not a sqlite database")
    target.write_bytes(b"previous recovery copy")
    with pytest.raises(sqlite3.DatabaseError):
        module.backup(source, target)
    assert target.read_bytes() == b"previous recovery copy"
    assert not list(tmp_path.glob(".web-backup-*"))


def test_refuses_to_overwrite_live_database(tmp_path):
    source = tmp_path / "live.sqlite3"
    source.touch()
    with pytest.raises(ValueError):
        module.backup(source, source)
