"""보안 판정에 쓰는 Unicode 정규화 도우미."""

from __future__ import annotations

import unicodedata


def remove_format_controls(text: str) -> str:
    """제로폭·양방향 제어 문자 등 Unicode ``Cf`` 문자를 제거한다."""

    return "".join(
        character
        for character in (text or "")
        if unicodedata.category(character) != "Cf"
    )


def normalize_security_text(text: str) -> str:
    """보안 패턴 매칭 전에 호환 문자와 대소문자·포맷 문자를 정규화한다."""

    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return remove_format_controls(normalized)
