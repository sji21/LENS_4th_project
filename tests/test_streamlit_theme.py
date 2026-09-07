"""PATCH-007 Streamlit 화면 대비 회귀 테스트."""

from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME_PATH = ROOT / ".streamlit" / "config.toml"


def test_app_uses_explicit_light_theme_with_contrasting_text() -> None:
    with THEME_PATH.open("rb") as theme_file:
        theme = tomllib.load(theme_file)["theme"]

    assert theme["base"] == "light"
    assert theme["backgroundColor"].lower() == "#f6f8fb"
    assert theme["textColor"].lower() == "#172b3a"
    assert theme["backgroundColor"].lower() != theme["textColor"].lower()
