"""Parse the approved source corpus without requiring any existing database."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from src.ingestion.fetch_law_mock import LAWS, ENDPOINT, parse_articles, parse_law_header
from src.ingestion.fetch_minbeop import MINBEOP_ARTICLES
from src.ingestion.load_laws import LawArticleRecord
from src.ingestion.load_guides import read_guide_records
from src.retrieval.expanded import CIVIL_IDS

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "data/sources/server-v1"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_sources():
    manifest = json.loads((SOURCES / "manifest.json").read_text(encoding="utf-8"))
    expected = {f"{name.replace(' ', '')}-{date}.txt" for name, _, date, _, _ in LAWS}
    expected |= {"민법-20260317.txt", "guide-records.jsonl"}
    if manifest.get("version") != 1 or set(manifest["files"]) != expected:
        raise ValueError("필수 원천 자료 manifest 불일치")
    for name, spec in manifest["files"].items():
        path = (SOURCES / name).resolve()
        if not path.is_relative_to(SOURCES.resolve()) or digest(path) != spec["sha256"]:
            raise ValueError(f"원천 자료 검증 실패: {name}")
    return manifest


def source_records():
    checked_sources()
    records = []
    specs = [*LAWS, ("민법", "284415", "20260317", "법률", None)]
    for name, seq, date, kind, _ in specs:
        path = SOURCES / f"{name.replace(' ', '')}-{date}.txt"
        text = path.read_text(encoding="utf-8")
        header = parse_law_header(text)
        expected_date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        if header["effective_from"] != expected_date or not header["proclamation_number"]:
            raise ValueError(f"원천 법령 판본 불일치: {name}")
        articles = parse_articles(text)
        if name == "민법":
            articles = [row for row in articles if row[0] in MINBEOP_ARTICLES]
            if len(articles) != len(MINBEOP_ARTICLES):
                raise ValueError("기존 민법 선택 조문 누락")
        for index, (number, title, content) in enumerate(articles):
            records.append(LawArticleRecord(
                law_name=name, law_type=kind, ministry=header["ministry"] or "미상",
                law_code=seq, proclamation_number=header["proclamation_number"],
                proclaimed_at=header["proclaimed_at"], effective_from=expected_date,
                document_type="law" if kind == "법률" else "decree",
                article_number=number, article_title=title, content=content,
                source_url=f"https://www.law.go.kr/법령/{name.replace(' ', '')}/{number}",
                collected_at="", file_path=path.relative_to(ROOT).as_posix(),
                source_text=text if index == 0 else "",
                source_document_url=ENDPOINT.format(seq=seq, eff=date), source_version_id=seq))
    # Reuse the reviewed HTML parsers; these functions do not open any DB or index.
    from scripts.patch027_sources import SPECS, parse_page
    from scripts.patch027_full_sources import compile_records
    bundle = ROOT / "data/eval/patch027-expansion"
    sources = json.loads((bundle / "source-manifest.json").read_text(encoding="utf-8"))
    for spec, source in zip(SPECS, sources, strict=True):
        path = bundle / "sources" / f"{spec[0]}.html"
        if digest(path) != source["sha256"]:
            raise ValueError("추가 법령 원문 해시 불일치")
        records.append(parse_page(path.read_text(encoding="utf-8"), spec, source["url"], path.relative_to(ROOT).as_posix()))
    records.extend(compile_records())
    anchors = [r.law_name + "-" + r.article_number for r in records]
    if len(anchors) != len(set(anchors)) or len(records) != 204:
        raise ValueError("승인 조문 수 또는 중복 검사 실패")
    if {a for a in anchors if a.startswith("민법-")} != set(CIVIL_IDS):
        raise ValueError("민법 선택 범위 불일치")
    for record in records:
        if record.validate():
            raise ValueError(record.validate())
    from scripts.load_case_only_demo_corpus import records_from_sources
    return records, records_from_sources(), read_guide_records(SOURCES / "guide-records.jsonl")


def fingerprint(records):
    text = json.dumps([[asdict(row) for row in group] for group in records], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
