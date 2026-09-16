"""Resolve pre-renumbering data paths without rewriting frozen captures.

Only the three renamed data prefixes are translated. Script hashes and execution
commits retain their original identities and are verified against Git history.
"""
import json
from dataclasses import replace
from pathlib import Path

from scripts.patch015_baseline import ROOT, norm, sha, write
from src.ingestion.load_laws import read_records as _read_records

DATA_PATHS = {
    f"data/eval/patch026-{name}": f"data/eval/patch027-{name}"
    for name in ("expansion", "scope", "full")
}


def current_data_path(value):
    for old, new in DATA_PATHS.items():
        if value == old or value.startswith(old + "/"):
            return new + value[len(old):]
    return value


def current_data_paths(value):
    if isinstance(value, str):
        return current_data_path(value)
    if isinstance(value, list):
        return [current_data_paths(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            current = current_data_path(key)
            if current in result:
                raise ValueError("Duplicate data path after PATCH-027 renumbering")
            result[current] = current_data_paths(item)
        return result
    return value


def read(path):
    """Expose current data paths in memory; never modify files or their hashes."""
    return current_data_paths(json.loads(Path(path).read_text(encoding="utf-8")))


def read_records(path):
    return [replace(record, file_path=current_data_path(record.file_path))
            for record in _read_records(path)]
