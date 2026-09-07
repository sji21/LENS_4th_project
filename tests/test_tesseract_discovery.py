"""PATCH-005 macOS·Windows Tesseract 실행 파일 탐색 테스트."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.document_check import extraction


def test_uses_explicit_tesseract_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "tesseract.exe"
    executable.touch()
    monkeypatch.setenv("TESSERACT_CMD", str(executable))

    assert extraction.find_tesseract() == str(executable)


def test_finds_standard_windows_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    program_files = tmp_path / "Program Files"
    executable = program_files / "Tesseract-OCR" / "tesseract.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setenv("PROGRAMFILES", str(program_files))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)

    assert extraction.find_tesseract() == str(executable)
