"""주택 임대차계약서의 핵심 작성 항목과 특약을 점검하는 규칙."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.document_check.extraction_models import PageExtraction
from src.document_check.models import SourceReference
from src.document_check.privacy import mask_sensitive_text

from .models import ContractClauseCheck, ContractFieldCheck


LAW_STANDARD_CONTRACT = SourceReference(
    title="국가법령정보센터 - 주택임대차보호법 제30조",
    url="https://www.law.go.kr/법령/주택임대차보호법/제30조",
)
HUG_CONTRACT_CONTENT = SourceReference(
    title="HUG 전세사기예방센터 - 계약내용 확인",
    url="https://www.khug.or.kr/jeonse/web/s03/s030205.jsp",
)
HUG_SPECIAL_TERMS = SourceReference(
    title="HUG 전세사기예방센터 - 특약사항 작성",
    url="https://www.khug.or.kr/jeonse/web/s03/s030206.jsp",
)
HUG_REGISTRY = SourceReference(
    title="HUG 전세사기예방센터 - 등기부등본 확인",
    url="https://www.khug.or.kr/jeonse/web/s03/s030105.jsp",
)
RTMS_REPORTING = SourceReference(
    title="국토교통부 부동산거래관리시스템 - 주택 임대차계약 신고",
    url="https://rtms.molit.go.kr/",
)


MONEY_PATTERN = r"(?:금\s*)?(?:[0-9][0-9,\s]*|[일이삼사오육칠팔구십백천만억]+)\s*원"
DATE_PATTERN = r"(?:20)?\d{2}\s*[년.\-/]\s*\d{1,2}\s*[월.\-/]\s*\d{1,2}\s*일?"
NAME_PATTERN = r"(?:성명|이름)\s*[:：]?\s*[가-힣]{2,5}"
ADDRESS_PATTERN = r"[가-힣0-9]+(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|동|읍|면|로|길)"
AREA_PATTERN = r"\d+(?:\.\d+)?\s*(?:㎡|m²|m2|제곱미터|평)"


@dataclass(frozen=True)
class FieldRule:
    field_id: str
    title: str
    importance: str
    labels: tuple[str, ...]
    values: tuple[str, ...]
    guidance: str
    sources: tuple[SourceReference, ...] = (HUG_CONTRACT_CONTENT,)


@dataclass(frozen=True)
class ClauseRule:
    clause_id: str
    title: str
    patterns: tuple[str, ...]
    reason: str
    recommendation: str
    registry_signal_ids: tuple[str, ...] = ()
    sources: tuple[SourceReference, ...] = (HUG_SPECIAL_TERMS,)


FIELD_RULES = (
    FieldRule(
        "property_address",
        "임대 목적물 주소",
        "core",
        ("소재지", "임차주택의 표시", "목적물 주소"),
        (ADDRESS_PATTERN,),
        "등기사항증명서·건축물대장과 같은 주소와 동·호가 적혔는지 원본에서 확인하세요.",
    ),
    FieldRule(
        "leased_area",
        "임차 부분과 면적",
        "core",
        ("임대할 부분", "임차부분", "임대면적", "전용면적"),
        (AREA_PATTERN, r"(?:동|층|호)\s*[:：]?\s*[0-9가-힣-]+"),
        "건물 전체가 아닌 일부를 임차한다면 동·층·호와 면적을 명확히 적었는지 확인하세요.",
    ),
    FieldRule(
        "landlord",
        "임대인 인적사항",
        "core",
        ("임대인",),
        (NAME_PATTERN, r"임대인\s*[:：]?\s*[가-힣]{2,5}"),
        "임대인 정보가 신분증 및 등기상 소유자와 일치하는지는 OCR 결과가 아니라 원본 서류로 대조하세요.",
    ),
    FieldRule(
        "tenant",
        "임차인 인적사항",
        "core",
        ("임차인",),
        (NAME_PATTERN, r"임차인\s*[:：]?\s*[가-힣]{2,5}"),
        "임차인 성명과 연락처 등 당사자 정보가 정확한지 원본에서 확인하세요.",
    ),
    FieldRule(
        "deposit",
        "보증금",
        "core",
        ("보증금", "전세금"),
        (MONEY_PATTERN,),
        "숫자와 한글 금액이 일치하고 지급 계좌의 예금주가 계약 상대방인지 확인하세요.",
    ),
    FieldRule(
        "monthly_rent",
        "월 차임",
        "conditional",
        ("월차임", "월세", "차임"),
        (MONEY_PATTERN,),
        "월세 계약이라면 금액, 지급일과 지급 방법을 확인하세요. 전세 계약이면 해당하지 않을 수 있습니다.",
    ),
    FieldRule(
        "payment_schedule",
        "계약금·중도금·잔금 지급 일정",
        "core",
        ("계약금", "중도금", "잔금"),
        (MONEY_PATTERN, DATE_PATTERN),
        "각 지급 금액과 날짜, 지급 계좌를 사전에 합의한 내용과 대조하세요.",
    ),
    FieldRule(
        "lease_period",
        "임대차 기간",
        "core",
        ("임대차기간", "계약기간", "임대차 기간"),
        (DATE_PATTERN, r"\d+\s*(?:년|개월)"),
        "입주일과 종료일이 합의 내용과 일치하는지 확인하세요.",
        (HUG_CONTRACT_CONTENT, RTMS_REPORTING),
    ),
    FieldRule(
        "contract_date",
        "계약 체결일",
        "core",
        ("계약체결일", "계약일", "작성일"),
        (DATE_PATTERN,),
        "실제로 계약을 체결한 날짜가 기재됐는지 확인하세요.",
        (HUG_CONTRACT_CONTENT, RTMS_REPORTING),
    ),
    FieldRule(
        "signatures",
        "임대인·임차인 서명 또는 날인",
        "visual",
        ("서명", "날인", "(인)", "인)"),
        (),
        "OCR은 실제 자필 서명이나 도장의 존재를 확정할 수 없습니다. 계약 당사자 모두의 서명·날인을 눈으로 확인하세요.",
        (LAW_STANDARD_CONTRACT, RTMS_REPORTING),
    ),
    FieldRule(
        "broker",
        "공인중개사 정보",
        "conditional",
        ("공인중개사", "중개사무소", "중개업등록번호"),
        (r"(?:등록번호|대표자|사무소)\s*[:：]?\s*[가-힣0-9-]+",),
        "중개 거래라면 사무소, 대표자, 등록번호와 날인을 확인하세요. 직거래라면 해당하지 않을 수 있습니다.",
        (HUG_CONTRACT_CONTENT, RTMS_REPORTING),
    ),
)


CLAUSE_RULES = (
    ClauseRule(
        "rights_freeze",
        "잔금·대항력 취득 전 권리관계 유지",
        (
            r"(?:새로운|추가)\s*(?:근저당|담보권|권리)",
            r"권리관계\s*(?:변동|유지)",
            r"잔금.*(?:근저당|담보권|소유권)",
        ),
        "계약 후 입주·전입신고 전에 새로운 담보권이나 소유권 변동이 생기면 보증금 회수 순위에 영향을 줄 수 있습니다.",
        "계약일부터 주택 인도와 전입신고 효력 발생 시점까지 새로운 담보권 설정이나 소유권 이전 등 권리변동을 제한하고, 위반 시 해제·지급금 반환 방식을 당사자와 구체적으로 협의하세요.",
        sources=(HUG_REGISTRY, HUG_SPECIAL_TERMS),
    ),
    ClauseRule(
        "lien_cancellation",
        "기존 근저당·신탁·전세권 말소 조건",
        (r"(?:근저당|신탁|전세권).{0,30}말소", r"말소.{0,30}(?:근저당|신탁|전세권)"),
        "기존 권리를 정리하기로 했다면 대상 권리와 이행 시점, 불이행 결과를 계약서에 남겨야 확인할 수 있습니다.",
        "말소하기로 합의한 권리의 등기 종류, 접수정보, 완료 기한과 불이행 시 계약 처리·지급금 반환 방식을 구체적으로 협의하세요.",
        registry_signal_ids=("mortgage", "trust", "leasehold", "tenant_registration"),
    ),
    ClauseRule(
        "guarantee_eligibility",
        "전세보증금반환보증 가입 불가 시 처리",
        (r"(?:반환)?보증.{0,25}(?:가입|불가|해지|취소)",),
        "임차인의 책임이 아닌 사유로 반환보증에 가입하지 못할 경우 계약과 지급금을 어떻게 처리할지 분쟁이 생길 수 있습니다.",
        "임차인의 귀책 없이 전세보증금반환보증 가입이 불가능한 경우 계약 해제와 지급한 보증금 반환 조건을 협의해 명시하세요.",
    ),
    ClauseRule(
        "debt_disclosure",
        "선순위보증금·체납·권리 정보의 사실 고지",
        (
            r"선순위.{0,20}(?:보증금|채권)",
            r"(?:미납|체납).{0,15}(?:국세|지방세|세금)",
            r"확정일자.{0,20}(?:현황|열람)",
        ),
        "다가구 선순위보증금이나 체납 정보가 사실과 다르면 계약 판단의 전제가 달라질 수 있습니다.",
        "임대인이 제공한 선순위보증금·세금 체납·권리관계 정보가 사실과 다를 때의 계약 해제와 지급금 반환 방식을 당사자와 협의하세요.",
        sources=(HUG_REGISTRY, HUG_SPECIAL_TERMS),
    ),
    ClauseRule(
        "repair_and_options",
        "수리·시설·옵션 상태와 비용 부담",
        (r"(?:수리|도배|곰팡이|누수|옵션|에어컨|냉장고).{0,35}(?:부담|수리|교체|원상복구)",),
        "구두로 합의한 수리, 옵션과 비용 부담은 계약서에 없으면 이후 확인하기 어렵습니다.",
        "입주 전 수리 항목, 옵션 목록과 상태, 완료 기한, 고장 시 비용 부담 주체를 사진 등과 함께 구체적으로 적으세요.",
    ),
)


def _flexible_keyword(keyword: str) -> re.Pattern[str]:
    return re.compile(r"\s*".join(re.escape(character) for character in keyword))


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _evidence(text: str, start: int, end: int, radius: int = 65) -> str:
    compact = _compact(text)
    left = max(0, start - radius)
    right = min(len(compact), end + radius)
    return ("…" if left else "") + mask_sensitive_text(compact[left:right]) + ("…" if right < len(compact) else "")


def _find_label(page: PageExtraction, labels: tuple[str, ...]):
    compact = _compact(page.text)
    for label in labels:
        match = _flexible_keyword(label).search(compact)
        if match:
            return compact, match
    return None


def looks_like_contract(pages: tuple[PageExtraction, ...]) -> bool:
    text = _compact(" ".join(page.text for page in pages))
    indicators = ("임대차", "임대인", "임차인", "보증금", "임차주택", "특약사항")
    return sum(bool(_flexible_keyword(keyword).search(text)) for keyword in indicators) >= 3


def check_contract_fields(pages: tuple[PageExtraction, ...]) -> tuple[ContractFieldCheck, ...]:
    checks = []
    for rule in FIELD_RULES:
        label_page = None
        label_context = None
        label_match = None
        for page in pages:
            found = _find_label(page, rule.labels)
            if found:
                label_page, (label_context, label_match) = page, found
                break

        if label_page is None or label_context is None or label_match is None:
            status = "not_found"
            page_number = None
            evidence = ""
        elif rule.importance == "visual":
            status = "review"
            page_number = label_page.page_number
            evidence = _evidence(label_context, label_match.start(), label_match.end())
        else:
            window = label_context[label_match.start() : label_match.end() + 140]
            value_found = any(re.search(pattern, window) for pattern in rule.values)
            status = "confirmed" if value_found else "review"
            page_number = label_page.page_number
            evidence = _evidence(label_context, label_match.start(), label_match.end() + min(90, len(window)))

        checks.append(
            ContractFieldCheck(
                field_id=rule.field_id,
                title=rule.title,
                importance=rule.importance,  # type: ignore[arg-type]
                status=status,  # type: ignore[arg-type]
                guidance=rule.guidance,
                page_number=page_number,
                evidence=evidence,
                sources=rule.sources,
            )
        )
    return tuple(checks)


def check_contract_clauses(
    pages: tuple[PageExtraction, ...],
    *,
    registry_signal_ids: tuple[str, ...] = (),
) -> tuple[ContractClauseCheck, ...]:
    checks = []
    for rule in CLAUSE_RULES:
        match_page = None
        match_context = None
        match = None
        for page in pages:
            compact = _compact(page.text)
            match = next((re.search(pattern, compact) for pattern in rule.patterns if re.search(pattern, compact)), None)
            if match:
                match_page, match_context = page, compact
                break

        included = match_page is not None and match_context is not None and match is not None
        related = bool(set(rule.registry_signal_ids) & set(registry_signal_ids))
        checks.append(
            ContractClauseCheck(
                clause_id=rule.clause_id,
                title=rule.title,
                status="included" if included else "recommended",
                reason=rule.reason,
                recommendation=rule.recommendation,
                page_number=match_page.page_number if included else None,
                evidence=(
                    _evidence(match_context, match.start(), match.end())
                    if included and match_context is not None and match is not None
                    else ""
                ),
                related_registry_signal=related,
                sources=rule.sources,
            )
        )
    return tuple(checks)
