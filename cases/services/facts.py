from __future__ import annotations

from datetime import datetime
import json
import re

from django.db import transaction
from django.utils import timezone

from cases.models import CaseFact

DATE_KEYS = {
    "잔금": "balance_date", "입주": "move_in_date", "계약일": "contract_date",
    "계약 날짜": "contract_date", "만료": "contract_end_date",
}


def normalize_value(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:300]


@transaction.atomic
def record_fact(case, *, key, value, source_type, source_ref, source_label="", confidence=1.0, metadata=None):
    normalized = normalize_value(value)
    fact, _ = CaseFact.objects.get_or_create(
        case=case, key=key, normalized_value=normalized, source_type=source_type, source_ref=str(source_ref),
        defaults={"value_json": value, "source_label": source_label, "confidence": confidence, "metadata": metadata or {}},
    )
    active = CaseFact.objects.select_for_update().filter(case=case, key=key).exclude(status=CaseFact.Status.INVALIDATED)
    values = set(active.values_list("normalized_value", flat=True))
    status = CaseFact.Status.CONFLICT if len(values) > 1 else CaseFact.Status.ACTIVE
    active.update(status=status)
    return fact


def invalidate_source(case, source_type, source_ref):
    CaseFact.objects.filter(case=case, source_type=source_type, source_ref=str(source_ref)).update(status=CaseFact.Status.INVALIDATED)
    for key in CaseFact.objects.filter(case=case, status=CaseFact.Status.CONFLICT).values_list("key", flat=True).distinct():
        remaining = CaseFact.objects.filter(case=case, key=key).exclude(status=CaseFact.Status.INVALIDATED)
        if remaining.values("normalized_value").distinct().count() <= 1:
            remaining.update(status=CaseFact.Status.ACTIVE)


def _parse_amount(raw):
    compact = raw.replace(",", "").replace(" ", "")
    if compact.isdigit():
        return int(compact)
    total = 0
    for unit, multiplier in (("억", 100_000_000), ("천만", 10_000_000), ("백만", 1_000_000), ("만", 10_000)):
        match = re.search(rf"(\d+(?:\.\d+)?)\s*{unit}", compact)
        if match:
            total += int(float(match.group(1)) * multiplier)
    return total or raw.strip()


def extract_rule_facts(text):
    facts = []
    amount = re.search(r"보증금(?:은|이|\s|:)*([0-9,\.]+\s*(?:억|천만|백만|만|원)(?:\s*[0-9,]+\s*만)?(?:\s*원)?)", text)
    if amount:
        facts.append(("deposit_amount", _parse_amount(amount.group(1)), 0.92, amount.group(0)))
    for label, key in DATE_KEYS.items():
        match = re.search(rf"{re.escape(label)}(?:일|은|이|\s|:)*(20\d{{2}}[./-]\d{{1,2}}[./-]\d{{1,2}}|\d{{1,2}}월\s*\d{{1,2}}일)", text)
        if match:
            raw = match.group(1)
            year = timezone.localdate().year
            normalized = re.sub(r"[./]", "-", raw)
            if "월" in normalized:
                month, day = map(int, re.findall(r"\d+", normalized))
                value = f"{year:04d}-{month:02d}-{day:02d}"
            else:
                parts = list(map(int, normalized.split("-")))
                value = f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
            facts.append((key, value, 0.9, match.group(0)))
    if re.search(r"반환보증.{0,12}(가입|들었|완료)", text):
        facts.append(("guarantee_enrolled", True, 0.85, "반환보증 가입 진술"))
    mortgage_sentence = next((part for part in re.split(r"[.!?\n]", text) if re.search(r"근저당|저당권", part)), "")
    if mortgage_sentence:
        if re.search(r"(있는지|없는지|여부|모르|확인.{0,6}(필요|못)|불명)", mortgage_sentence):
            pass
        elif re.search(r"(말소.{0,8}(예정|약속|조건)|없애|해지.{0,8}(예정|약속))", mortgage_sentence):
            facts.append(("mortgage_removal_promise", "말소 예정", 0.8, mortgage_sentence.strip()))
            facts.append(("mortgage_present", True, 0.72, mortgage_sentence.strip()))
        elif re.search(
            r"(말소.{0,10}(완료|처리|됨|되었|됐)|해지.{0,10}(완료|처리|됨|되었|됐)|"
            r"없(?:음|습니다|어요|다|는|고|으며)|있지\s*않|존재하지\s*않|"
            r"(?:설정|등기|등록|걸려|잡혀).{0,10}(?:않|안\s*되|안\s*되어)|"
            r"안\s*(?:설정|등기|등록|걸려|잡혀))",
            mortgage_sentence,
        ):
            facts.append(("mortgage_present", False, 0.88, mortgage_sentence.strip()))
        else:
            facts.append(("mortgage_present", True, 0.72, mortgage_sentence.strip()))
    return facts


def record_text_facts(case, text, *, source_type, source_ref, source_label="", metadata=None):
    recorded = []
    for key, value, confidence, evidence in extract_rule_facts(text):
        recorded.append(record_fact(
            case, key=key, value=value, source_type=source_type, source_ref=source_ref,
            source_label=source_label, confidence=confidence,
            metadata={**(metadata or {}), "evidence": evidence[:240]},
        ))
    return recorded


def active_facts(case):
    return list(case.facts.exclude(status=CaseFact.Status.INVALIDATED))
