"""연습용 코퍼스 확장 수집기.

팀원이 만드는 정식 수집 파이프라인(법제처 OPEN API 기반)을 대체하는 것이 아니다.
Vector DB가 준비되기 전까지 검색기를 현실적인 규모로 측정하려고, 국가법령정보센터
본문 페이지에서 조문을 긁어 같은 청크 스키마로 떨어뜨린다.
정식 파이프라인이 들어오면 이 파일은 버린다.

실행:
    python -m src.ingestion.fetch_law_mock            # 수집 + 청크 생성
    python -m src.ingestion.fetch_law_mock --no-fetch # 이미 받은 원문으로 청크만 재생성
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from html import unescape
from pathlib import Path

RAW_DIR = Path("data/raw/law")
OUT_PATH = Path("data/sample/chunks_expanded.jsonl")

ENDPOINT = (
    "https://www.law.go.kr/LSW//lsInfoR.do"
    "?lsiSeq={seq}&efYd={eff}&efYn=Y&chrClsCd=010202"
    "&nwJoYnInfo=Y&ancYnChk=0&netPrivateYn=N"
)

# (법령명, lsiSeq, 시행일, 종류, 모법)
LAWS: list[tuple[str, str, str, str, str | None]] = [
    ("주택임대차보호법", "276291", "20260102", "법률", None),
    ("주택임대차보호법 시행령", "287183", "20260701", "시행령", "주택임대차보호법"),
    ("상가건물 임대차보호법", "279651", "20260512", "법률", None),
    ("상가건물 임대차보호법 시행령", "287139", "20260701", "시행령", "상가건물 임대차보호법"),
]

# 조문 시작 지점. "제3조", "제3조의2", "제12조의3" 등
_ARTICLE_HEAD = re.compile(r"^\s*(제\d+조(?:의\d+)?)\s*(?:\(([^)]*)\))?")
# 부칙부터는 버린다. 경과규정은 이번 범위 밖이고 노이즈가 크다.
# onclick 자바스크립트가 텍스트로 새어나와 줄 앞을 가리므로 줄머리로 잡지 않는다.
_ADDENDA = re.compile(r"부\s*칙\s*<\s*(?:법률|대통령령)")
# 태그를 걷어내도 남는 스크립트·속성 잔해
_JUNK = re.compile(r"href=|src=|return false|value=\"|onclick|javascript:|조문목록")
# "[제9조로 이동]" 처럼 본문이 없는 이동·삭제 표기
_MOVED = re.compile(r"^\[제\d+조(?:의\d+)?로 이동")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t ]+")


def fetch(seq: str, eff: str) -> str:
    url = ENDPOINT.format(seq=seq, eff=eff)
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.law.go.kr/"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def html_to_text(raw: str) -> str:
    """본문 HTML을 줄 단위 평문으로 만든다."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|td|tr|li|h\d)>", "\n", text)
    text = _TAG.sub("", text)
    text = unescape(text)
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def parse_articles(text: str) -> list[tuple[str, str, str]]:
    """평문에서 (조문번호, 조문제목, 본문) 목록을 뽑는다."""
    # 부칙은 줄 중간에서 시작할 수 있으므로 전체 텍스트에서 위치를 찾아 잘라낸다.
    cut = _ADDENDA.search(text)
    if cut:
        text = text[: cut.start()]

    articles: list[tuple[str, str, list[str]]] = []
    seen: set[str] = set()
    current: tuple[str, str, list[str]] | None = None

    for line in text.split("\n"):
        if _JUNK.search(line):
            continue
        head = _ARTICLE_HEAD.match(line)
        # 목차 링크 등으로 같은 조문이 두 번 나오면 첫 번째만 취한다.
        if head and head.group(1) not in seen:
            if current:
                articles.append(current)
            no, title = head.group(1), (head.group(2) or "").strip()
            seen.add(no)
            body = line[head.end():].strip()
            current = (no, title, [body] if body else [])
        elif current:
            current[2].append(line)

    if current:
        articles.append(current)

    out = []
    for no, title, body_lines in articles:
        body = "\n".join(body_lines).strip()
        # 개정 이력 표기는 검색 노이즈라 떼어낸다.
        body = re.sub(r"<개정[^>]*>|\[전문개정[^\]]*\]|\[본조신설[^\]]*\]", "", body)
        body = re.sub(r"\n{2,}", "\n", body).strip()
        if not body or body.startswith("삭제") or _MOVED.match(body):
            continue
        out.append((no, title, body))
    return out


def build_chunks() -> list[dict]:
    chunks: list[dict] = []
    for name, seq, eff, hierarchy, parent in LAWS:
        path = RAW_DIR / f"{name.replace(' ', '')}-{eff}.txt"
        if not path.exists():
            print(f"  [건너뜀] 원문 없음: {path}")
            continue
        articles = parse_articles(path.read_text(encoding="utf-8"))
        doc_id = f"law-{name.replace(' ', '')}-{eff}"
        print(f"  {name}: 조문 {len(articles)}개")
        for idx, (no, title, body) in enumerate(articles):
            header = f"[{name} {no}({title})]" if title else f"[{name} {no}]"
            chunks.append({
                "chunk_id": f"{doc_id}#{no}#0",
                "doc_id": doc_id,
                "chunk_index": idx,
                "text": f"{header}\n{body}",
                "metadata": {
                    "title": name,
                    "doc_type": "law" if hierarchy == "법률" else "decree",
                    "hierarchy": hierarchy,
                    "parent_title": parent or "",
                    "article_id": f"{name}-{no}",
                    "article_no": no,
                    "article_title": title,
                    "source_url": f"https://www.law.go.kr/법령/{name.replace(' ', '')}/{no}",
                    "version": "",
                    "effective_date": f"{eff[:4]}-{eff[4:6]}-{eff[6:]}",
                    "expiry_date": "",
                    "status": "current",
                    "refs": "",
                    "collected_at": time.strftime("%Y-%m-%d"),
                },
            })
    return chunks


# "[시행 2026. 1. 2.] [법률 제21065호, 2025. 10. 1., 타법개정]"
_HEADER_META = re.compile(
    r"\[시행\s*(?P<ey>\d{4})\.\s*(?P<em>\d{1,2})\.\s*(?P<ed>\d{1,2})\.?\]\s*"
    r"\[(?P<kind>\S+)\s*(?P<no>제[\d호]+),\s*"
    r"(?P<py>\d{4})\.\s*(?P<pm>\d{1,2})\.\s*(?P<pd>\d{1,2})\.?"
)
_MINISTRY = re.compile(r"^([가-힣]+부)\s*\(")


def _date(y: str, m: str, d: str) -> str:
    return f"{y}-{int(m):02d}-{int(d):02d}"


def parse_law_header(text: str) -> dict:
    """법령 페이지 머리말에서 공포번호·공포일·시행일·소관부처를 뽑는다."""
    head = "\n".join(text.split("\n")[:20])
    meta = {"proclamation_number": "", "proclaimed_at": "",
            "effective_from": "", "ministry": ""}

    m = _HEADER_META.search(head)
    if m:
        meta["effective_from"] = _date(m["ey"], m["em"], m["ed"])
        meta["proclamation_number"] = f"{m['kind']} {m['no']}"
        meta["proclaimed_at"] = _date(m["py"], m["pm"], m["pd"])

    for line in head.split("\n"):
        found = _MINISTRY.match(line.strip())
        if found:
            meta["ministry"] = found.group(1)
            break

    return meta


def build_records() -> list:
    """적재용 원천 레코드(LawArticleRecord)를 만든다.

    수집 경로가 공식 API로 바뀌어도 이 형식만 맞추면 load_laws 는 그대로 쓴다.
    """
    from src.ingestion.load_laws import LawArticleRecord

    records = []
    for name, seq, eff, hierarchy, _parent in LAWS:
        path = RAW_DIR / f"{name.replace(' ', '')}-{eff}.txt"
        if not path.exists():
            print(f"  [건너뜀] 원문 없음: {path}")
            continue

        text = path.read_text(encoding="utf-8")
        header = parse_law_header(text)
        effective_from = header["effective_from"] or f"{eff[:4]}-{eff[4:6]}-{eff[6:]}"

        for no, title, body in parse_articles(text):
            records.append(LawArticleRecord(
                law_name=name,
                law_type=hierarchy,
                ministry=header["ministry"] or "미상",
                law_code=seq,                      # 국가법령정보센터 lsiSeq
                proclamation_number=header["proclamation_number"] or f"seq-{seq}",
                proclaimed_at=header["proclaimed_at"] or effective_from,
                effective_from=effective_from,
                effective_to=None,
                status="current",
                document_type="law" if hierarchy == "법률" else "decree",
                article_number=no,
                article_title=title,
                content=body,
                source_url=f"https://www.law.go.kr/법령/{name.replace(' ', '')}/{no}",
                collected_at=time.strftime("%Y-%m-%d"),
                file_path=str(path),
            ))
    return records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="원문 재수집 없이 청크만 다시 만든다")
    ap.add_argument("--records", default=None,
                    help="적재용 원천 레코드 jsonl 경로 (지정 시 함께 생성)")
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if not args.no_fetch:
        for name, seq, eff, _, _ in LAWS:
            path = RAW_DIR / f"{name.replace(' ', '')}-{eff}.txt"
            text = html_to_text(fetch(seq, eff))
            path.write_text(text, encoding="utf-8")
            print(f"  받음: {name}  ({len(text):,}자)")
            time.sleep(0.5)

    chunks = build_chunks()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\n총 {len(chunks)}개 청크 -> {OUT_PATH}")

    if args.records:
        from src.ingestion.load_laws import write_records

        records = build_records()
        write_records(records, Path(args.records))
        print(f"원천 레코드 {len(records)}건 -> {args.records}")


if __name__ == "__main__":
    main()
