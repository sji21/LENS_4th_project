import json
import shutil
from urllib.parse import parse_qs, urlparse

import pytest

from src.ingestion import coverage_sources as coverage


def test_approved_supplements_are_exact_versioned_articles():
    records = coverage.supplement_records()
    assert tuple(f"{row.law_name}-{row.article_number}" for row in records) == coverage.APPROVED_ANCHORS
    assert len({(row.law_name, row.article_number) for row in records}) == 12
    for row in records:
        fields = parse_qs(urlparse(row.source_url).query)
        assert fields["lsiSeq"] == [row.source_version_id]
        assert fields["efYd"] == [row.effective_from.replace("-", "")]
        assert row.collected_at == "2026-09-19"
        assert not row.validate()


def test_supplement_bodies_cover_the_reviewed_legal_rules():
    records = {f"{row.law_name}-{row.article_number}": row.content
               for row in coverage.supplement_records()}
    expected_phrases = {
        "민법-제131조": "상당한 기간을 정하여",
        "민법-제134조": "철회할 수 있다",
        "민법-제618조": "목적물을 사용, 수익하게 할 것을 약정",
        "민법-제624조": "보존에 필요한 행위",
        "민법-제633조": "매월말에",
        "민사집행법-제148조": "배당요구를 한 채권자",
        "민간임대주택에 관한 특별법-제48조": "선순위 담보권",
        "민간임대주택에 관한 특별법 시행령-제40조": "75퍼센트는 임대사업자",
        "공공주택 특별법-제48조의4": "동의서면을 국토교통부장관에게 제출",
        "공공주택 특별법-제49조의4": "다른 사람에게 전대",
        "공공주택 특별법 시행령-제42조": "같은 세대별 주민등록표",
        "공공주택 특별법 시행령-제48조": "전대에 대한 동의를 받으려는",
    }
    assert set(records) == set(expected_phrases)
    for anchor, phrase in expected_phrases.items():
        assert phrase in records[anchor]


def test_manifest_cannot_drop_an_official_source(tmp_path, monkeypatch):
    copied = tmp_path / "supplement-v1"
    shutil.copytree(coverage.SOURCES, copied)
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].pop("민사집행법-20260201.html")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(coverage, "SOURCES", copied)
    with pytest.raises(ValueError, match="manifest"):
        coverage.checked_specs()


def test_spec_digest_is_fixed_even_if_manifest_is_rewritten(tmp_path, monkeypatch):
    copied = tmp_path / "supplement-v1"
    shutil.copytree(coverage.SOURCES, copied)
    specs_path = copied / "specs.json"
    specs = json.loads(specs_path.read_text(encoding="utf-8"))
    specs.pop()
    specs_path.write_text(json.dumps(specs, ensure_ascii=False), encoding="utf-8")
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["specs_sha256"] = coverage._digest(specs_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(coverage, "SOURCES", copied)
    with pytest.raises(ValueError, match="manifest"):
        coverage.checked_specs()
