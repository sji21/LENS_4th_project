"""로그와 화면에 노출되는 OCR 텍스트의 최소 마스킹."""

from __future__ import annotations

import re


RESIDENT_ID = re.compile(r"(?<!\d)(\d{6})[-\s]?([1-8])\d{6}(?!\d)")
PHONE = re.compile(r"(?<!\d)(01[016789]|0\d{1,2})[-\s]?(\d{3,4})[-\s]?(\d{4})(?!\d)")
ACCOUNT = re.compile(r"(?<!\d)(\d{2,6})[-\s](\d{2,6})[-\s](\d{4,8})(?!\d)")


def mask_sensitive_text(text: str) -> str:
    """주민등록번호·전화번호·계좌번호 형태를 화면 표시 전에 가린다."""

    masked = RESIDENT_ID.sub(lambda match: f"{match.group(1)}-{match.group(2)}******", text)
    masked = PHONE.sub(lambda match: f"{match.group(1)}-****-{match.group(3)}", masked)
    return ACCOUNT.sub("****-****-****", masked)
