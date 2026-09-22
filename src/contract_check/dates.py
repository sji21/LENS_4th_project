"""Extract dates only when their clause explicitly identifies their role."""
import re
from dataclasses import dataclass, replace
from datetime import date


@dataclass(frozen=True)
class ContractDate:
    field_id: str
    value: str
    page_number: int
    evidence: str
    status: str = "confirmed"
    method: str = "clause_text"
    region: tuple[int, ...] = ()


DATE = r"(20\d{2})년(\d{1,2})월(\d{1,2})일"
PATTERNS = {
    "handover_date": DATE + r"(?:까지)?(?:임차인에게)?인도",
    "lease_end_date": r"임대차기간은인도일로부터" + DATE + r"(?:\(\d+개월\))?까지",
    "lease_start_date": r"임대차기간(?:은|:)?" + DATE + r"부터",
    "balance_date": r"잔금[^。\n]{0,40}?" + DATE + r"에(?:지불|지급)",
}


def extract_clause_dates(text, page_number=1, *, method="clause_text", region=()):
    compact = re.sub(r"[ \t\r\n]+", "", text)
    found = []
    for field, pattern in PATTERNS.items():
        for match in re.finditer(pattern, compact):
            try:
                value = date(*(int(v) for v in match.groups())).isoformat()
            except ValueError:
                continue
            # Store only the matched clause, not surrounding personal details.
            found.append(ContractDate(field, value, page_number, match[0], method=method, region=region))
    if "임대차기간은인도일로부터" in compact:
        found.extend(replace(item, field_id="lease_start_date", evidence=item.evidence + " / 임대차기간은인도일로부터")
                     for item in tuple(found) if item.field_id == "handover_date")
    return tuple(found)


def reconcile_dates(readings, *, require_methods=1):
    """Conflicting values remain review-only; never choose a majority date."""
    result = []
    for field in dict.fromkeys(item.field_id for item in readings):
        items = [item for item in readings if item.field_id == field]
        values = {item.value for item in items}
        confirmed = len(values) == 1 and len({item.method for item in items if item.status == "confirmed"}) >= require_methods
        for value in sorted(values):
            item = next(item for item in items if item.value == value)
            result.append(replace(item, status="confirmed" if confirmed else "review"))
    return tuple(result)
