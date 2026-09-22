"""Load the exact PATCH-058 statute supplements from hash-pinned official pages."""
from __future__ import annotations

import json
import re
from pathlib import Path

from lxml import html

from src.ingestion.fetch_law_mock import html_to_text, parse_articles, parse_law_header
from src.ingestion.load_laws import LawArticleRecord


ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "data/sources/supplement-v1"
SPEC_SHA256 = "d6442fe2f7de1f51606a2047d3c580fabdb65211a723bb4a44f7f1ccfaebc08f"
SOURCE_FILES = {
    "민사집행법-20260201.html",
    "민간임대주택에관한특별법시행령-20260226.html",
    "공공주택특별법-20260908.html",
    "공공주택특별법시행령-20260908.html",
}
REUSED_FILES = {
    "data/sources/server-v1/민법-20260317.txt",
    "data/eval/patch027-full/sources/PRIVATE_FULL.html",
    "data/eval/patch027-full/sources/PUBLIC492.html",
}
APPROVED_ANCHORS = (
    "민법-제131조",
    "민법-제134조",
    "민법-제618조",
    "민법-제624조",
    "민법-제633조",
    "민사집행법-제148조",
    "민간임대주택에 관한 특별법-제48조",
    "민간임대주택에 관한 특별법 시행령-제40조",
    "공공주택 특별법-제48조의4",
    "공공주택 특별법-제49조의4",
    "공공주택 특별법 시행령-제42조",
    "공공주택 특별법 시행령-제48조",
)


def _digest(path: Path) -> str:
    import hashlib

    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value)


def checked_specs() -> tuple[list[dict], str]:
    """Verify the immutable source inventory before any article is parsed."""
    manifest_path = SOURCES / "manifest.json"
    specs_path = SOURCES / "specs.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("version") != 1 or manifest.get("specs_sha256") != SPEC_SHA256
            or _digest(specs_path) != SPEC_SHA256
            or set(manifest.get("files", {})) != SOURCE_FILES
            or set(manifest.get("reused_files", {})) != REUSED_FILES):
        raise ValueError("PATCH-058 원천 manifest 불일치")
    actual = {path.name for path in SOURCES.iterdir() if path.is_file()}
    if actual != SOURCE_FILES | {"manifest.json", "specs.json"}:
        raise ValueError("PATCH-058 원천 파일 목록 불일치")

    hashes = {f"data/sources/supplement-v1/{name}": row["sha256"]
              for name, row in manifest["files"].items()}
    hashes.update(manifest["reused_files"])
    for relative, expected in hashes.items():
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT.resolve()) or _digest(path) != expected:
            raise ValueError(f"PATCH-058 원천 해시 불일치: {relative}")

    specs = json.loads(specs_path.read_text(encoding="utf-8"))
    anchors = tuple(f'{row["law_name"]}-{row["article_number"]}' for row in specs)
    if anchors != APPROVED_ANCHORS or len({row["source_id"] for row in specs}) != len(specs):
        raise ValueError("PATCH-058 승인 조문 목록 불일치")
    for row in specs:
        if row.get("sha256") != hashes.get(row.get("path")):
            raise ValueError(f'PATCH-058 조문 원천 연결 불일치: {row.get("source_id", "?")}')
    return specs, manifest["collected_at"]


def _law_name(raw: str, path: Path) -> str:
    if path.suffix.lower() != ".html":
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return next((lines[index - 1] for index, line in enumerate(lines)
                     if index and line.startswith("[시행 ")), "")
    page = html.fromstring(raw)
    names = page.xpath('//input[@id="lsNm"]/@value') + page.xpath("//h2/text()")
    return next((name.strip() for name in names if name.strip()), "")


def supplement_records() -> list[LawArticleRecord]:
    specs, collected_at = checked_specs()
    records = []
    parsed_sources: dict[str, tuple[dict, str, list[tuple[str, str, str]]]] = {}
    for spec in specs:
        relative = spec["path"]
        if relative not in parsed_sources:
            path = ROOT / relative
            raw = path.read_text(encoding="utf-8")
            text = html_to_text(raw) if path.suffix.lower() == ".html" else raw
            parsed_sources[relative] = (parse_law_header(text), _law_name(raw, path), parse_articles(text))
        header, law_name, articles = parsed_sources[relative]
        if _normalized(law_name) != _normalized(spec["law_name"]):
            raise ValueError(f'PATCH-058 법령명 불일치: {spec["source_id"]}')
        for field in ("effective_from", "proclamation_number", "proclaimed_at"):
            if header[field] != spec[field]:
                raise ValueError(f'PATCH-058 판본 불일치: {spec["source_id"]} {field}')
        matches = [article for article in articles if article[0] == spec["article_number"]]
        if len(matches) != 1:
            raise ValueError(f'PATCH-058 조문 누락 또는 중복: {spec["source_id"]}')
        number, title, content = matches[0]
        record = LawArticleRecord(
            law_name=spec["law_name"],
            law_type="법률" if spec["document_type"] == "law" else "시행령",
            ministry=header["ministry"] or "국토교통부",
            law_code=spec["source_version_id"],
            proclamation_number=header["proclamation_number"],
            proclaimed_at=header["proclaimed_at"],
            effective_from=header["effective_from"],
            content=content,
            source_url=spec["url"],
            collected_at=collected_at,
            article_number=number,
            article_title=title,
            document_type=spec["document_type"],
            file_path=relative,
            source_version_id=spec["source_version_id"],
        )
        if problems := record.validate():
            raise ValueError(f'PATCH-058 레코드 오류: {spec["source_id"]}: {problems}')
        records.append(record)
    return records
