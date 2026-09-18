"""Work around native HNSW readers rejecting non-ASCII Windows paths.

Only the native index path changes. The Git-distributed source stays untouched;
the usual logical hash/ID/text checks still validate the opened collection.
"""
import atexit
import os
from pathlib import Path
import shutil
import tempfile

_COPIES = {}


def native_index_path(path, *, immutable=False):
    source = Path(path).resolve()
    if not immutable and (os.name != "nt" or str(source).isascii()):
        return source
    files = sorted(p for p in source.rglob("*") if p.is_file())
    if not files or any(p.is_symlink() or not p.resolve().is_relative_to(source) for p in files):
        raise ValueError("Chroma 원본 인덱스가 없거나 안전하지 않습니다.")
    signature = (str(source), tuple((p.relative_to(source).as_posix(), p.stat().st_size,
                                    p.stat().st_mtime_ns) for p in files))
    if signature in _COPIES:
        return _COPIES[signature]
    candidates = [tempfile.gettempdir(), os.getenv("LOCALAPPDATA", "")]
    base = next((Path(p) for p in candidates if p and (os.name != "nt" or str(Path(p).resolve()).isascii())), None)
    if base is None:
        raise ValueError("Windows HNSW용 영어 임시 경로가 필요합니다. 영어 경로에 clone하거나 TEMP를 지정하세요.")
    target = Path(tempfile.mkdtemp(prefix="lens-chroma-", dir=base))
    try:
        shutil.copytree(source, target, dirs_exist_ok=True)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    _COPIES[signature] = target
    # Windows can retain native handles until process exit; cleanup is best effort.
    atexit.register(shutil.rmtree, target, ignore_errors=True)
    return target
