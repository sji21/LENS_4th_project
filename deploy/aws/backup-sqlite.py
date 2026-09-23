#!/usr/bin/env python3
"""Create and validate a consistent SQLite copy without stopping Django."""
import argparse
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def backup(database: Path, output: Path) -> dict:
    database = database.resolve(strict=True)
    output = output.resolve()
    if database == output:
        raise ValueError("Backup output must differ from the live database")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".web-backup-", dir=output.parent)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)) as source:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination, pages=256, sleep=0.1)
                checks = destination.execute("PRAGMA integrity_check").fetchall()
                if checks != [("ok",)]:
                    raise RuntimeError("SQLite backup failed integrity_check")
        with open(temporary, "rb") as stream:
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"backup": str(output), "bytes": output.stat().st_size, "integrity": "ok"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("/opt/lens/app/data/database/web.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("/opt/lens/app/data/backups/web-latest.sqlite3"))
    args = parser.parse_args()
    print(json.dumps(backup(args.database, args.output)))
