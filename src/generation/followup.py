"""검색은 됐지만 답변에 쓰이지 않은 근거를 후속 질문으로 제안한다.

새 검색을 하지 않는다. ``Answer.sources()``는 답변이 실제로 인용한 것이 아니라
**검색이 돌려준 근거 전체**이므로, 그중 본문에 인용되지 않은 조문만 골라내면
이미 색인된 자료를 한 번 더 보여 주는 것으로 끝난다.
"""

from __future__ import annotations

import re


# 판례·기관 안내는 제안하지 않는다. "제3조의2에 대해 설명해줘"처럼 조문 하나를
# 다시 묻는 형태가 자연스러운 것은 법령류뿐이다.
_LAW_DOC_TYPES = frozenset({"law", "decree", "rule"})

# "주택임대차보호법 제3조의2(보증금의 회수)" → 뒤쪽 괄호 제목을 떼어 낸다.
_TRAILING_TITLE_RE = re.compile(r"\([^()]*\)\s*$")


def clean_citation_for_question(citation: str) -> str:
    """출처명을 질문 문장에 넣기 좋게 다듬는다.

    괄호가 중첩된 경우에는 손대지 않는다. 잘못 자르면 조문 번호가 사라져
    엉뚱한 질문이 만들어지므로, 확실한 형태만 정리한다.
    """

    text = (citation or "").strip()
    if not text or text.count("(") != 1 or text.count(")") != 1:
        return text
    cleaned = _TRAILING_TITLE_RE.sub("", text).strip()
    return cleaned or text


def suggest_followups(
    sources: list[dict],
    cited_labels: frozenset[str] | set[str],
    *,
    limit: int = 3,
) -> tuple[str, ...]:
    """인용되지 않은 법령 근거를 후속 질문 문장으로 만든다.

    검색 순위를 그대로 따르고, 같은 출처가 두 번 나오면 한 번만 남긴다.
    """

    out: list[str] = []
    seen: set[str] = set()
    for source in sources:
        if source.get("doc_type") not in _LAW_DOC_TYPES:
            continue
        label = (source.get("label") or "").strip()
        if not label or label in cited_labels or label in seen:
            continue
        seen.add(label)
        out.append(clean_citation_for_question(label))
        if len(out) >= limit:
            break
    return tuple(out)
