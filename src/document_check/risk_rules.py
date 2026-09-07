"""등기 텍스트에서 계약 전 확인이 필요한 권리 신호를 찾는 결정론적 규칙."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from .extraction_models import PageExtraction
from .models import RiskSignal, SourceReference
from .privacy import mask_sensitive_text


HUG_REGISTRY = SourceReference(
    title="HUG 전세사기예방센터 - 등기부등본 확인",
    url="https://www.khug.or.kr/jeonse/web/s03/s030105.jsp",
)
HUG_OWNER = SourceReference(
    title="HUG 전세사기예방센터 - 주택 소유자 확인",
    url="https://www.khug.or.kr/jeonse/web/s03/s030202.jsp",
)
HUG_GUARANTEE = SourceReference(
    title="HUG 전세보증금반환보증 - 등기부등본상 확인사항",
    url="https://www.khug.or.kr/hug/web/ig/dr/igdr000001.jsp",
)


@dataclass(frozen=True)
class Rule:
    rule_id: str
    title: str
    severity: str
    section: str
    keywords: tuple[str, ...]
    guidance: str
    checks: tuple[str, ...]
    sources: tuple[SourceReference, ...] = (HUG_REGISTRY,)


RULES = (
    Rule(
        rule_id="auction",
        title="경매 절차 관련 등기",
        severity="high",
        section="갑구",
        keywords=("경매개시결정", "강제경매개시결정", "임의경매개시결정"),
        guidance="이미 경매 절차와 관련된 등기가 표시된 경우 계약 진행 전에 권리관계를 전문적으로 확인해야 합니다.",
        checks=("경매 사건 진행 상태", "배당 순위와 선순위 권리", "공인중개사·법률 전문가 확인"),
        sources=(HUG_GUARANTEE, HUG_REGISTRY),
    ),
    Rule(
        rule_id="seizure",
        title="압류·가압류 표시",
        severity="high",
        section="갑구",
        keywords=("가압류", "압류"),
        guidance="소유권에 대한 권리침해 표시가 확인됩니다. 원인과 해제·말소 여부를 확인하기 전에는 단정적으로 계약 가능 여부를 판단할 수 없습니다.",
        checks=("압류·가압류의 현재 효력", "채권자와 청구 금액", "계약·잔금 전 말소 조건"),
        sources=(HUG_GUARANTEE,),
    ),
    Rule(
        rule_id="injunction",
        title="가처분 표시",
        severity="high",
        section="갑구",
        keywords=("가처분",),
        guidance="처분 제한과 관련된 표시가 확인됩니다. 계약 상대방이 유효하게 처분할 수 있는지 추가 확인이 필요합니다.",
        checks=("가처분의 내용과 채권자", "현재 효력과 말소 가능 여부", "계약 상대방의 처분 권한"),
        sources=(HUG_GUARANTEE,),
    ),
    Rule(
        rule_id="trust",
        title="신탁 관련 표시",
        severity="high",
        section="갑구",
        keywords=("신탁", "수탁자"),
        guidance="신탁 관련 표시가 있으면 등기 명의와 실제 계약 권한이 다를 수 있으므로 신탁원부와 적법한 임대 권한을 확인해야 합니다.",
        checks=("신탁원부 발급·확인", "수탁자 또는 위탁자의 임대 권한", "보증금 수령 계좌와 계약 당사자"),
        sources=(HUG_OWNER, HUG_REGISTRY),
    ),
    Rule(
        rule_id="provisional_registration",
        title="가등기 표시",
        severity="high",
        section="갑구",
        keywords=("소유권이전청구권가등기", "가등기"),
        guidance="가등기는 향후 소유권 변동과 연결될 수 있으므로 권리 내용과 순위를 확인해야 합니다.",
        checks=("가등기 권리자와 원인", "본등기 가능성과 순위", "계약 전 말소 여부"),
        sources=(HUG_GUARANTEE,),
    ),
    Rule(
        rule_id="mortgage",
        title="근저당권·저당권 표시",
        severity="caution",
        section="을구",
        keywords=("근저당권설정", "저당권설정", "근저당권"),
        guidance="담보권이 확인됩니다. 등기의 채권최고액만으로 실제 채무액이나 보증금 회수 가능성을 판단할 수 없습니다.",
        checks=("채권최고액과 실제 채무액", "주택가격과 선순위채권 합계", "잔금 전 추가 설정 여부", "말소 조건이 있다면 특약 반영"),
        sources=(HUG_REGISTRY, HUG_GUARANTEE),
    ),
    Rule(
        rule_id="leasehold",
        title="기존 전세권 표시",
        severity="caution",
        section="을구",
        keywords=("전세권설정", "전세권"),
        guidance="기존 전세권의 순위·금액·말소 여부가 새 임차인의 권리와 충돌하지 않는지 확인해야 합니다.",
        checks=("전세권자와 전세금", "설정 순위", "계약·잔금 전 말소 여부"),
    ),
    Rule(
        rule_id="tenant_registration",
        title="임차권등기 표시",
        severity="high",
        section="을구",
        keywords=("임차권등기명령", "임차권설정", "임차권등기"),
        guidance="기존 임차인의 보증금 반환 문제와 관련될 수 있는 임차권 표시가 확인됩니다. 접수번호보다 존재·금액·말소 여부를 우선 확인합니다.",
        checks=("임차권 존재 건수와 보증금", "현재 말소 여부", "기존 임차인의 보증금 반환 상태"),
        sources=(HUG_REGISTRY,),
    ),
    Rule(
        rule_id="pledge",
        title="근질권 표시",
        severity="caution",
        section="을구",
        keywords=("근질권", "질권설정"),
        guidance="담보권에 추가로 질권이 연결된 표시가 확인됩니다. 권리자와 담보 범위를 확인해야 합니다.",
        checks=("근질권자", "피담보채권 범위", "근저당권과의 관계 및 말소 여부"),
    ),
    Rule(
        rule_id="joint_collateral",
        title="공동담보 표시",
        severity="caution",
        section="을구",
        keywords=("공동담보", "공동담보목록"),
        guidance="여러 부동산이 하나의 담보에 연결된 표시일 수 있어 공동담보 목록과 배분 관계를 추가로 확인해야 합니다.",
        checks=("공동담보목록의 다른 부동산", "담보 채무 전체 규모", "해당 주택에 미치는 영향"),
    ),
)


DATE_PATTERNS = (
    re.compile(r"(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?"),
    re.compile(r"(20\d{2})(\d{2})(\d{2})"),
)

_OCR_INTERNAL_SPACE_RE = re.compile(r"(?<=[가-힣0-9])\s+(?=[가-힣0-9])")


def _flexible_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(r"\s*".join(re.escape(character) for character in keyword))


def _evidence(text: str, match: re.Match[str], radius: int = 55) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    keyword = re.sub(r"\s+", "", match.group(0))
    compact_match = re.search(_flexible_pattern(keyword), compact)
    if not compact_match:
        return mask_sensitive_text(keyword)
    start = max(0, compact_match.start() - radius)
    end = min(len(compact), compact_match.end() + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(compact) else ""
    return prefix + mask_sensitive_text(compact[start:end]) + suffix


def _detect_rule(page: PageExtraction, rule: Rule) -> RiskSignal | None:
    for keyword in rule.keywords:
        match = _flexible_pattern(keyword).search(page.text)
        if match:
            return RiskSignal(
                rule_id=rule.rule_id,
                title=rule.title,
                severity=rule.severity,  # type: ignore[arg-type]
                section=rule.section,
                matched_keyword=keyword,
                page_number=page.page_number,
                evidence=_evidence(page.text, match),
                guidance=rule.guidance,
                checks=rule.checks,
                sources=rule.sources,
            )
    return None


def _find_issue_date(pages: tuple[PageExtraction, ...]) -> tuple[date, int] | None:
    candidates: list[tuple[date, int]] = []
    for page in pages:
        lines = re.split(r"[\r\n]+", page.text)
        # Tesseract가 한글과 날짜 숫자 사이에 넣은 공백만 판독용 사본에서 제거한다.
        # 원본 PageExtraction 텍스트와 화면에 표시할 근거는 변경하지 않는다.
        normalized_lines = [_OCR_INTERNAL_SPACE_RE.sub("", line) for line in lines]
        issue_lines = [
            line
            for line in normalized_lines
            if re.search(r"발급일|열람일시", line)
        ]
        for line in issue_lines:
            for pattern in DATE_PATTERNS:
                matches = pattern.finditer(line)
                for match in matches:
                    try:
                        parsed = datetime.strptime("-".join(match.groups()), "%Y-%m-%d").date()
                    except ValueError:
                        continue
                    candidates.append((parsed, page.page_number))
    return max(candidates, default=None)


def detect_risk_signals(
    pages: tuple[PageExtraction, ...], *, today: date | None = None
) -> tuple[RiskSignal, ...]:
    """같은 규칙은 문서 전체에서 최초 한 번만 보고한다."""

    detected = []
    for rule in RULES:
        for page in pages:
            if not page.text:
                continue
            signal = _detect_rule(page, rule)
            if signal:
                detected.append(signal)
                break

    issued = _find_issue_date(pages)
    reference_day = today or date.today()
    if issued and (reference_day - issued[0]).days > 30:
        issued_date, page_number = issued
        detected.append(
            RiskSignal(
                rule_id="stale_document",
                title="발급 후 30일 초과",
                severity="caution",
                section="문서 상태",
                matched_keyword=issued_date.isoformat(),
                page_number=page_number,
                evidence=f"확인된 최신 날짜: {issued_date.isoformat()}",
                guidance="등기사항은 계약 전에도 변동될 수 있으므로 계약과 잔금 직전에 최신 문서를 다시 확인하세요.",
                checks=("계약 직전 최신 등기 재발급", "잔금 지급 직전 권리관계 재확인"),
                sources=(HUG_REGISTRY,),
            )
        )

    order = {"high": 0, "caution": 1, "info": 2}
    return tuple(sorted(detected, key=lambda signal: (order[signal.severity], signal.page_number)))
