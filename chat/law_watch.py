"""Compare official metadata; never replace corpus text automatically."""
import json
import re
from urllib.parse import urlencode
from urllib.request import urlopen

from django.db import transaction
from django.utils import timezone
from .models import LawWatch, LawAlert


def fetch_metadata(title, credential):
    if not credential:
        raise ValueError("LAW_OPEN_API_OC 설정이 필요합니다.")
    query = urlencode(dict(OC=credential, target="law", type="JSON", search=1,
                           query=title, display=100, sort="ddes"))
    with urlopen("https://www.law.go.kr/DRF/lawSearch.do?" + query, timeout=20) as response:
        root = json.loads(response.read(2_000_000))["LawSearch"]
    if int(root.get("totalCnt", 0)) > 100:
        raise ValueError("검색 결과가 너무 많아 법령을 확정하지 못했습니다.")
    rows = root.get("law", [])
    if isinstance(rows, dict):
        rows = [rows]
    rows = [row for row in rows if re.sub(r"\s+", "", row.get("법령명한글", "")) == re.sub(r"\s+", "", title)]
    if not rows:
        raise ValueError("동일한 법령명을 찾지 못했습니다.")
    row = max(rows, key=lambda r: (str(r.get("공포일자", "")), str(r.get("시행일자", ""))))
    fields = ("법령ID", "법령일련번호", "공포일자", "공포번호", "시행일자", "제개정구분명")
    result = {key: str(row.get(key, "")) for key in fields}
    if not result["법령ID"] or not all(re.fullmatch(r"\d{8}", result[key]) for key in ("공포일자", "시행일자")):
        raise ValueError("법령 메타데이터가 불완전합니다.")
    return result


def check_law(title, credential, corpus_dates=()):
    watch, _ = LawWatch.objects.get_or_create(title=title)
    try:
        metadata = fetch_metadata(title, credential)
    except Exception as error:
        # URLs in network exceptions may include credentials.
        LawWatch.objects.filter(pk=watch.pk).update(last_error=type(error).__name__, checked_at=timezone.now())
        return "failed"
    with transaction.atomic():
        watch = LawWatch.objects.select_for_update().get(pk=watch.pk)
        changed = bool(watch.metadata and watch.metadata != metadata)
        mismatch = not watch.metadata and corpus_dates and metadata["시행일자"] not in {d.replace("-", "") for d in corpus_dates}
        if changed or mismatch:
            LawAlert.objects.create(watch=watch, before=watch.metadata or {"corpus_dates": sorted(corpus_dates)}, after=metadata)
        watch.metadata = metadata
        watch.checked_at = timezone.now()
        watch.last_error = ""
        watch.save()
    return "changed" if changed or mismatch else "unchanged"
