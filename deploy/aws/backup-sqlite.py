#!/usr/bin/env python3
"""Make a consistent SQLite backup without stopping Django."""
import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def backup(database: Path, output: Path) -> None:
    database = database.resolve(strict=True)
    output = output.resolve()
    if database == output:
        raise ValueError("backup output must differ from the live database")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".web-backup-", dir=output.parent)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)) as source:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination, pages=256, sleep=0.1)
                if destination.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise RuntimeError("SQLite backup failed integrity check")
        with open(temporary, "rb") as stream:
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("/opt/lens/app/data/database/web.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("/opt/lens/app/data/backups/web-latest.sqlite3"))
    arguments = parser.parse_args()
    backup(arguments.database, arguments.output)
