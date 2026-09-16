"""Official links derived only from retrieved citation metadata."""
import re
from urllib.parse import quote, urlencode
from src.generation.citation import _law_key_from_label, citation_scan_text


def citation_url(citation, doc_type):
    if doc_type in {"law", "decree", "rule"}:
        # Use the same title/identity boundary as grounding, including nested
        # titles, while preserving the original law spelling for the URL.
        if _law_key_from_label(citation) is None:
            return ""
        head = citation_scan_text(citation.partition("(")[0]).strip()
        match = re.fullmatch(r"(.+?)\s+(제\s*\d+\s*조(?:\s*의\s*\d+)?)", head)
        if match:
            return "https://www.law.go.kr/법령/" + "/".join(quote(re.sub(r"\s+", "", part), safe="") for part in match.groups())
    if doc_type == "case":
        match = re.search(r"\d{2,4}[가-힣]{1,4}\d+", citation)
        if match:
            return "https://glaw.scourt.go.kr/wsjo/intesrch/sjo022.do?" + urlencode({"q": match.group(), "w": "panre", "section": "panre_tot"})
    return ""
