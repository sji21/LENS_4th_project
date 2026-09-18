from pathlib import Path

from src.retrieval.portable_index import native_index_path


def test_sealed_index_uses_isolated_cached_copy(tmp_path):
    source=tmp_path/"index"
    source.mkdir()
    (source/"chroma.sqlite3").write_bytes(b"sealed index")
    target=native_index_path(source,immutable=True)
    assert target!=source.resolve()
    assert (target/"chroma.sqlite3").read_bytes()==b"sealed index"
    (target/"chroma.sqlite3").write_bytes(b"mutable chroma state")
    assert (source/"chroma.sqlite3").read_bytes()==b"sealed index"
    assert native_index_path(source,immutable=True)==target


def test_changed_source_gets_a_new_native_copy(tmp_path):
    source=tmp_path/"index"
    source.mkdir()
    (source/"chroma.sqlite3").write_bytes(b"old")
    first=native_index_path(source,immutable=True)
    (source/"chroma.sqlite3").write_bytes(b"new version")
    second=native_index_path(source,immutable=True)
    assert first!=second
    assert (second/"chroma.sqlite3").read_bytes()==b"new version"
