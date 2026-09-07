"""LLM 호출·로그·화면 출력 전에 비밀정보를 탐지하고 가린다.

API key, access token, password처럼 외부에 노출되면 안 되는 값은 LLM에게 보내
판단시키지 않는다. 비밀값 자체가 모델 입력으로 들어가는 순간 필터 목적이
무너지므로 이 모듈은 결정론적 패턴만 사용한다.

주민등록번호·전화번호·계좌번호 같은 개인정보는
``src.document_check.privacy``의 책임이고, 여기서는 인증정보·비밀값만 다룬다.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


SecretKind = Literal[
    "named_secret",
    "bearer_token",
    "openai_key",
    "github_token",
    "huggingface_token",
    "slack_token",
]

REDACTION = "[REDACTED_SECRET]"


@dataclass(frozen=True)
class SecretFinding:
    """탐지한 비밀정보의 위치와 종류.

    실제 비밀값은 결과 객체에 보관하지 않는다. 로그에 이 객체를 그대로 남겨도
    원문 비밀값이 노출되지 않게 하기 위한 설계다.
    """

    kind: SecretKind
    start: int
    end: int
    label: str = ""


@dataclass(frozen=True)
class SecretFilterResult:
    """비밀정보 마스킹 결과."""

    text: str
    findings: tuple[SecretFinding, ...]

    @property
    def contains_secret(self) -> bool:
        return bool(self.findings)


# 키 이름이 명시된 assignment는 값 모양이 일반 문자열이어도 비밀값으로 본다.
# 빈 값은 .env.example처럼 안전한 템플릿일 수 있으므로 탐지하지 않는다.
_SECRET_LABEL = (
    r"(?:OPENAI_API_KEY|LAW_GO_KR_API_KEY|HF_TOKEN|HUGGINGFACE_TOKEN|"
    r"GITHUB_TOKEN|GITHUB_PAT|SLACK_TOKEN|API_KEY|ACCESS_TOKEN|"
    r"AUTH_TOKEN|CLIENT_SECRET|SECRET_KEY|PASSWORD|PASSWD)"
)

# PASSWORD="hunter22" 같은 일반적인 따옴표 assignment도 값만 가린다. 따옴표는
# 남겨 두어 설정 파일 문법을 깨뜨리지 않는다.
_NAMED_QUOTED_SECRET_RE = re.compile(
    r"(?P<prefix>"
    rf"(?P<label>{_SECRET_LABEL})"
    r"[ \t]*[:=][ \t]*"
    r")"
    r"(?P<quote>[\"'])"
    r"(?P<value>[^\r\n]{4,}?)"
    r"(?P=quote)",
    re.IGNORECASE,
)

_NAMED_SECRET_RE = re.compile(
    r"(?P<prefix>"
    rf"(?P<label>{_SECRET_LABEL})"
    r"[ \t]*[:=][ \t]*"
    r")"
    r"(?P<value>[^\s,;\"']{4,})",
    re.IGNORECASE,
)

_BEARER_RE = re.compile(
    r"(?P<prefix>\b(?:Authorization\s*:\s*)?Bearer\s+)"
    r"(?P<value>[A-Za-z0-9._~+/=-]{12,})",
    re.IGNORECASE,
)

# 알려진 토큰 prefix는 label이 없어도 식별한다. 최소 길이를 둬서 법령 번호나
# 일반 하이픈 문자열을 오탐하지 않게 한다.
_OPENAI_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<value>sk-(?:proj-)?[A-Za-z0-9_-]{20,})"
    r"(?![A-Za-z0-9])"
)

_GITHUB_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<value>(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,})"
    r"(?![A-Za-z0-9])"
)

_HUGGINGFACE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<value>hf_[A-Za-z0-9]{20,})"
    r"(?![A-Za-z0-9])"
)

_SLACK_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<value>xox[baprs]-[A-Za-z0-9-]{20,})"
    r"(?![A-Za-z0-9])"
)


_PATTERN_SPECS: tuple[
    tuple[SecretKind, re.Pattern[str], bool],
    ...,
] = (
    ("named_secret", _NAMED_QUOTED_SECRET_RE, True),
    ("named_secret", _NAMED_SECRET_RE, True),
    ("bearer_token", _BEARER_RE, True),
    ("openai_key", _OPENAI_KEY_RE, False),
    ("github_token", _GITHUB_TOKEN_RE, False),
    ("huggingface_token", _HUGGINGFACE_TOKEN_RE, False),
    ("slack_token", _SLACK_TOKEN_RE, False),
)


def _candidate_findings(text: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []

    for kind, pattern, has_prefix in _PATTERN_SPECS:
        for match in pattern.finditer(text or ""):
            start, end = match.span("value")
            label = ""

            if kind == "named_secret":
                label = match.group("label")

            # prefix가 있는 패턴도 실제 값 영역만 마스킹한다.
            # 예: Authorization: Bearer [REDACTED_SECRET]
            findings.append(
                SecretFinding(
                    kind=kind,
                    start=start,
                    end=end,
                    label=label,
                )
            )

    return findings


def _deduplicate_overlaps(
    findings: list[SecretFinding],
) -> tuple[SecretFinding, ...]:
    """같은 비밀값을 여러 패턴이 잡았을 때 한 번만 남긴다."""

    if not findings:
        return ()

    # 시작점이 같으면 더 긴 범위를 먼저 두고, 알려진 prefix 탐지를 named assignment
    # 탐지보다 우선한다. 실제 마스킹 결과에는 차이가 없지만 kind가 더 구체적이다.
    priority = {
        "openai_key": 0,
        "github_token": 0,
        "huggingface_token": 0,
        "slack_token": 0,
        "bearer_token": 1,
        "named_secret": 2,
    }

    ordered = sorted(
        findings,
        key=lambda item: (
            item.start,
            priority[item.kind],
            -(item.end - item.start),
        ),
    )

    kept: list[SecretFinding] = []

    for finding in ordered:
        overlaps = any(
            finding.start < existing.end
            and existing.start < finding.end
            for existing in kept
        )
        if not overlaps:
            kept.append(finding)

    return tuple(sorted(kept, key=lambda item: item.start))


def find_secrets(text: str) -> tuple[SecretFinding, ...]:
    """비밀정보 위치를 찾되 실제 비밀값은 반환하지 않는다."""

    return _deduplicate_overlaps(
        _candidate_findings(text)
    )


def redact_secrets(
    text: str,
    replacement: str = REDACTION,
) -> SecretFilterResult:
    """탐지한 비밀값만 replacement로 바꾼다."""

    findings = find_secrets(text)

    if not findings:
        return SecretFilterResult(
            text=text,
            findings=(),
        )

    parts: list[str] = []
    cursor = 0

    for finding in findings:
        parts.append(text[cursor:finding.start])
        parts.append(replacement)
        cursor = finding.end

    parts.append(text[cursor:])

    return SecretFilterResult(
        text="".join(parts),
        findings=findings,
    )


def contains_secret(text: str) -> bool:
    """런타임 연결용 빠른 불리언 진입점."""

    return bool(find_secrets(text))
