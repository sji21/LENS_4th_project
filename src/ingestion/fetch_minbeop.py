"""검색에 사용하도록 검토한 민법 임대차 조문만 수집한다.

민법 전체를 법령 검색에 섞지 않는다. 검색 회귀 평가를 통과하고 조건부 라우팅에서
사용하는 7개 조문만 국가법령정보센터 원문에서 가져와 ``LawArticleRecord`` JSONL로
저장한다. 이후 기존 ``load_laws`` 명령으로 같은 SQLite와 법령 청크에 합친다.

실행:
    python -m src.ingestion.fetch_minbeop \
        --records data/parsed/minbeop_records.jsonl
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from src.ingestion.fetch_law_mock import (
    ENDPOINT,
    fetch,
    html_to_text,
    parse_articles,
    parse_law_header,
)
from src.ingestion.load_laws import LawArticleRecord, write_records

MINBEOP_SEQ = "284415"
MINBEOP_EFFECTIVE_DATE = "20260317"
MINBEOP_ARTICLES = ("제623조", "제626조", "제627조", "제629조", "제632조", "제634조", "제640조")


def collect_records(raw_html: str) -> list[LawArticleRecord]:
    """공식 민법 HTML에서 허용 목록의 조문만 적재 레코드로 만든다."""
    text = html_to_text(raw_html)
    header = parse_law_header(text)
    if header["effective_from"] != "2026-03-17":
        raise ValueError("검토한 민법 시행일(2026-03-17)과 원문이 다릅니다")
    parsed = {number: (title, body) for number, title, body in parse_articles(text)}
    missing = [number for number in MINBEOP_ARTICLES if number not in parsed]
    if missing:
        raise ValueError(f"민법 원문에서 찾지 못한 조문: {missing}")

    collected_at = time.strftime("%Y-%m-%d")
    return [
        LawArticleRecord(
            law_name="민법",
            law_type="법률",
            ministry=header["ministry"] or "법무부",
            law_code=MINBEOP_SEQ,
            proclamation_number=header["proclamation_number"],
            proclaimed_at=header["proclaimed_at"],
            effective_from=header["effective_from"],
            content=parsed[number][1],
            source_url=f"https://www.law.go.kr/법령/민법/{number}",
            collected_at=collected_at,
            article_number=number,
            article_title=parsed[number][0],
            document_type="law",
            file_path=f"data/raw/law/민법-{MINBEOP_EFFECTIVE_DATE}.txt",
            source_text=text if number == MINBEOP_ARTICLES[0] else "",
            source_document_url=ENDPOINT.format(seq=MINBEOP_SEQ, eff=MINBEOP_EFFECTIVE_DATE),
            source_version_id=MINBEOP_SEQ,
        )
        for number in MINBEOP_ARTICLES
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="조건부 검색용 민법 7개 조문 수집")
    parser.add_argument(
        "--records",
        type=Path,
        default=Path("data/parsed/minbeop_records.jsonl"),
        help="LawArticleRecord JSONL 출력 경로",
    )
    args = parser.parse_args()

    raw = fetch(MINBEOP_SEQ, MINBEOP_EFFECTIVE_DATE)
    records = collect_records(raw)
    raw_path = Path(records[0].file_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(html_to_text(raw), encoding="utf-8")
    write_records(records, args.records)
    print(f"민법 {len(records)}개 조문 수집: {args.records}")
    print("조문: " + ", ".join(record.article_number for record in records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
