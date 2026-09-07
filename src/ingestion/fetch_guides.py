"""공식 안내(가이드) 원문을 수집해 적재용 레코드로 만든다.

법령·판례만으로는 답할 수 없는 질문이 있다. "전세보증금반환보증이 뭔가요?"는
HUG 상품 안내이지 조문이 아니고, "집주인이 세금을 안 냈는지 어떻게 확인하나요?"는
주택임대차보호법 제3조의7 이 열람 권리를 정할 뿐 **어떻게** 열람하는지는 국세청
안내에 있다. 이런 문서가 없으면 검색기가 엉뚱한 조문을 자신 있게 내놓는다.

**원문을 그대로 가져온다.** 요약해서 넣지 않는다. 평가 질문을 보고 쓴 요약문을
코퍼스에 넣으면 그 질문에만 맞는 문서가 되고 측정이 부풀려진다. 이 프로젝트에서
이미 두 번 겪었다(청크 보강 실험, 판례 요약본).

실행:
    python -m src.ingestion.fetch_guides --records data/parsed/guide_records.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass, field
from html import unescape
from pathlib import Path

import requests

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")

# "2023.06.30." / "2023-06-30"
_DATE = re.compile(r"(20\d{2})[.\-/\s]+(\d{1,2})[.\-/\s]+(\d{1,2})")
# 조회수처럼 요청할 때마다 바뀌는 값은 본문에서 뺀다. 남겨두면 checksum 이 매번
# 달라져 멱등 적재가 깨지고, 검색에도 쓸모가 없다.
_BOOKKEEPING = ("작성자", "관리자", "작성일자", "조회수", "등록일", "담당부서")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class GuideSource:
    """수집할 안내 문서 하나.

    warmup_url 이 있는 페이지가 있다. 국세청은 목록 페이지를 먼저 열어 세션을
    만들지 않으면 본문 없이 빈 껍데기를 돌려준다.
    """

    guide_id: str
    title: str
    agency: str
    guide_type: str
    topic: str
    url: str
    start_marker: str                       # 본문이 시작되는 줄에 들어 있는 말
    end_markers: tuple[str, ...] = ()       # 이 중 하나가 나오면 본문 끝
    warmup_url: str = ""
    max_lines: int = 60
    date_marker: str = ""                   # 이 줄 다음에 발행일이 온다


SOURCES: tuple[GuideSource, ...] = (
    GuideSource(
        guide_id="guide-HUG-전세보증금반환보증",
        title="HUG 전세보증금반환보증 상품안내",
        agency="주택도시보증공사",
        guide_type="상품안내",
        topic="전세보증금 반환보증",
        url="https://www.khug.or.kr/hug/web/ig/dr/igdr000001.jsp",
        # 상단 메뉴가 600줄 넘게 이어진다. 표 요약문이 본문 시작 지점이다.
        start_marker="전세보증금반환보증 상품 개요",
        end_markers=("위탁금융기관",),
        max_lines=80,
    ),
    GuideSource(
        guide_id="guide-국세청-미납국세열람",
        title="국세청 미납국세 등 열람신청 안내",
        agency="국세청",
        guide_type="민원안내",
        topic="임대인 미납국세 열람",
        url="https://www.nts.go.kr/nts/na/ntt/selectNttInfo.do?nttSn=1325154&mi=2207",
        # "개요"만 찾으면 좌측 메뉴의 "세무조사 개요"가 먼저 걸린다.
        start_marker="열람신청(주택임차",
        # 본문이 끝나면 다음글·이전글·만족도조사·기관 링크가 이어진다. 이것을 안
        # 끊으면 청크 하나가 통째로 유튜브·SNS 링크 목록이 된다(실제로 그랬다).
        end_markers=("다음글", "이전글", "목록", "콘텐츠 만족도"),
        date_marker="작성일자",
        warmup_url="https://www.nts.go.kr/nts/na/ntt/selectNttList.do?mi=2207",
    ),
)


@dataclass
class GuideRecord:
    """적재용 원천 레코드. load_guides 가 이 형식을 읽는다."""

    guide_id: str
    title: str
    agency: str
    guide_type: str
    topic: str
    source_url: str
    published_at: str
    content: str
    collected_at: str
    status: str = "current"
    published_at_source: str = "unknown"   # page | unknown

    def validate(self) -> list[str]:
        problems: list[str] = []
        for name in ("guide_id", "title", "agency", "guide_type", "topic",
                     "source_url", "content", "collected_at"):
            if not str(getattr(self, name)).strip():
                problems.append(f"{name} 이 비어 있음")
        if len(self.content) < 100:
            # 페이지 구조가 바뀌어 본문을 못 잡으면 짧은 조각만 남는다.
            problems.append(f"본문이 {len(self.content)}자뿐이다 (100자 미만)")
        return problems


def html_to_lines(raw: str) -> list[str]:
    """본문 HTML 을 줄 단위 평문으로 만든다.

    `fetch_law_mock.html_to_text` 와 같은 방식이다. 이 저장소에는 bs4·lxml 이
    없으므로 표준 라이브러리만 쓴다.
    """
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|td|tr|li|h\d)>", "\n", text)
    text = unescape(_TAG.sub(" ", text))
    return [ln for ln in (_WS.sub(" ", l).strip() for l in text.split("\n")) if ln]


def extract_body(lines: list[str], source: GuideSource) -> tuple[str, str]:
    """머리말·메뉴를 걷어내고 안내 본문만 남긴다. (본문, 발행일) 을 돌려준다.

    공공기관 페이지는 상단 메뉴가 수백 줄이고, 본문 뒤에는 다음글·만족도조사·SNS
    링크가 이어진다. 시작 표시부터 끝 표시까지만 남기지 않으면 청크 하나가 통째로
    링크 목록이 된다(국세청에서 실제로 그랬다).
    """
    start = next((i for i, l in enumerate(lines) if source.start_marker in l), -1)
    if start < 0:
        return "", ""

    body = lines[start : start + source.max_lines]

    # 첫 줄부터 찾으면 안 된다. 시작 줄이 목차 성격이라 끝 표시를 함께 담고 있는
    # 경우가 있고(HUG 가 그렇다), 그러면 본문이 통째로 잘려 나간다.
    ends = [
        i
        for i, line in enumerate(body)
        if i > 0 and any(marker in line for marker in source.end_markers)
    ]
    if ends:
        body = body[: min(ends)]

    published_at = ""
    if source.date_marker:
        for i, line in enumerate(body):
            if source.date_marker in line:
                # 표시와 같은 줄에 있을 수도, 다음 줄에 있을 수도 있다.
                found = _DATE.search(line) or (
                    _DATE.search(body[i + 1]) if i + 1 < len(body) else None
                )
                if found:
                    year, month, day = found.groups()
                    published_at = f"{year}-{int(month):02d}-{int(day):02d}"
                break

    kept = [
        line
        for line in body
        if len(line) > 3 and not any(word in line for word in _BOOKKEEPING)
    ]
    return "\n".join(kept), published_at


def fetch(source: GuideSource, session: requests.Session) -> str:
    if source.warmup_url:
        session.get(source.warmup_url, timeout=20)
        time.sleep(0.5)
    response = session.get(source.url, timeout=20)
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding
    return response.text


def collect(sources: tuple[GuideSource, ...] = SOURCES,
            collected_at: str = "") -> tuple[list[GuideRecord], list[str]]:
    collected_at = collected_at or time.strftime("%Y-%m-%d")
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://www.google.com/"})

    records: list[GuideRecord] = []
    problems: list[str] = []
    for source in sources:
        try:
            body, published_at = extract_body(
                html_to_lines(fetch(source, session)), source
            )
        except Exception as error:                      # 네트워크·파싱 모두
            problems.append(f"{source.guide_id}: {type(error).__name__} {error}")
            continue

        record = GuideRecord(
            guide_id=source.guide_id,
            title=source.title,
            agency=source.agency,
            guide_type=source.guide_type,
            topic=source.topic,
            source_url=source.url,
            # 게시일을 찾지 못했다고 수집일을 게시일로 쓰지 않는다. 검색 청크의
            # effective_date 는 실제 게시일일 때만 채우고 수집일은 별도로 보존한다.
            published_at=published_at,
            published_at_source="page" if published_at else "unknown",
            content=body,
            collected_at=collected_at,
        )
        found = record.validate()
        if found:
            problems.extend(f"{source.guide_id}: {p}" for p in found)
            continue
        records.append(record)
    return records, problems


def main() -> int:
    parser = argparse.ArgumentParser(description="공식 안내 원문을 수집한다")
    parser.add_argument("--records", default="data/parsed/guide_records.jsonl")
    args = parser.parse_args()

    records, problems = collect()
    print(f"\n  수집 {len(records)}/{len(SOURCES)}건")
    for record in records:
        print(f"    {record.guide_id:<34}{len(record.content):>6}자  {record.title}")

    if problems:
        # 페이지 구조가 바뀌면 조용히 빈 문서가 들어간다. 반드시 보이게 한다.
        print(f"\n  실패 {len(problems)}건:")
        for problem in problems:
            print(f"    {problem}")

    if problems:
        # 일부만 성공한 결과로 덮어쓰면, 다음 적재가 빠진 문서를 지우거나 옛
        # 문서를 남긴다. 전부 성공했을 때만 쓴다.
        print("\n  실패가 있어 파일을 쓰지 않았습니다. 기존 파일은 그대로입니다.\n")
        return 1

    out = Path(args.records)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    print(f"\n  원천 레코드 -> {out}\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
