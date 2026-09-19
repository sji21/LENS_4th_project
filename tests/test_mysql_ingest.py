"""Real MySQL document edits preserve old snapshots and unrelated documents."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest
import sqlalchemy as sa

from test_mysql_transfer import mysql_engine, source_files
from src.database.mysql_schema import SNAPSHOTS, TABLES
from src.ingestion.load_cases import CaseRecord
from src.ingestion.load_guides import GuideRecord
from src.ingestion.load_laws import LawArticleRecord
from src.ingestion.mysql_ingest import prepare, read_plan, SCHEMA
from src.ingestion.mysql_transfer import import_source, open_source, trace_chunk, verify_snapshot, export_snapshot


@pytest.fixture
def parent(mysql_engine, source_files):
    database, streams = source_files
    with open_source(database, streams, "fixture", "v1") as source:
        snapshot = import_source(mysql_engine, source)["snapshot_id"]
    # All subsequent edits must load their parent from MySQL alone.
    database.rename(database.with_suffix(".unavailable"))
    for path in streams.values():
        path.rename(path.with_suffix(".unavailable"))
    return snapshot


def plan(parent, changes, version="v2"):
    return {"schema": SCHEMA, "parent_snapshot": parent, "corpus": "fixture", "version": version,
            "observed_at": "2026-09-18T03:00:00+00:00", "documents": changes}


def guide_change(content, guide_id="guide-1"):
    record = GuideRecord(guide_id=guide_id, title="기관 안내", agency="기관", guide_type="공식",
        topic="보증금", published_at="", collected_at="2026-09-18", content=content,
        source_url="https://example.test/" + guide_id)
    return {"operation": "replace", "document_id": "guide-document:" + guide_id,
            "doc_type": "guide", "records": [asdict(record)]}


def store(engine, request):
    with prepare(engine, request) as source:
        result = import_source(engine, source)
        return result, source.manifest


def test_new_and_shortened_guide_chunks_preserve_other_documents(mysql_engine, parent, tmp_path):
    old_case = trace_chunk(mysql_engine, parent, "case:Case-A#0")
    request = plan(parent, [guide_change("A" * 400 + "\n" + "B" * 400), guide_change("새 안내", "new-guide")])
    first, manifest = store(mysql_engine, request)
    repeated, again = store(mysql_engine, request)
    assert first["snapshot_id"] == repeated["snapshot_id"] and not repeated["created"]
    assert manifest == again
    snapshot = first["snapshot_id"]
    assert trace_chunk(mysql_engine, snapshot, "guide-1#1")["chunk"]["content"].endswith("B" * 400)
    assert trace_chunk(mysql_engine, snapshot, "case:Case-A#0")["exports"] == old_case["exports"]
    assert trace_chunk(mysql_engine, snapshot, "case:Case-A#0")["source"] == old_case["source"]
    short, _ = store(mysql_engine, plan(snapshot, [guide_change("짧은 안내")], "v3"))
    with pytest.raises(ValueError, match="청크"):
        trace_chunk(mysql_engine, short["snapshot_id"], "guide-1#1")
    assert trace_chunk(mysql_engine, short["snapshot_id"], "new-guide#0")
    # The old version remains queryable, including the now-removed long chunk.
    assert trace_chunk(mysql_engine, snapshot, "guide-1#1")
    export_snapshot(mysql_engine, short["snapshot_id"], tmp_path / "export")
    with mysql_engine.begin() as c:
        for sid in (parent, snapshot, short["snapshot_id"]):
            verify_snapshot(c, sid)


def case_change():
    record = CaseRecord(case_id="Case-A", case_number="2026다1234", court_name="대법원",
        decision_date="2026-01-02", case_type="민사", case_name="보증금",
        holding="수정된 공식 판결요지", summary="공식 요약", full_text="수정된 공식 전문",
        source_url="https://example.test/case-new", collected_at="2026-09-18")
    return {"operation": "replace", "document_id": "case-document:Case-A", "doc_type": "case",
            "records": [asdict(record)],
            "case_provenance": {"canonical_case_key": "court|2026다1234|2026-01-02", "source_name": "공식",
                "scope_tier": "core", "court_level": 0, "corpus_active": True,
                "official_id": "official-2", "raw_response": "새 원수집 응답"},
            "metadata": {"case:Case-A#0": {"source_page_sha256": "b" * 64, "missing_date": None, "active": False}},
            "fields": {"case:Case-A#0": {"extra_provenance": {"section": "판결요지", "pages": [2]}}}}


def test_case_replacement_appends_history_and_rejects_silent_metadata_loss(mysql_engine, parent):
    change = case_change()
    incomplete = deepcopy(change)
    incomplete.pop("metadata")
    with pytest.raises(ValueError, match="메타데이터"):
        store(mysql_engine, plan(parent, [incomplete]))
    with mysql_engine.begin() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(SNAPSHOTS)).scalar_one() == 1
    created, _ = store(mysql_engine, plan(parent, [change]))
    again, _ = store(mysql_engine, plan(parent, [change]))
    assert not again["created"]
    sid = created["snapshot_id"]
    traced = trace_chunk(mysql_engine, sid, "case:Case-A#0")
    assert traced["source"]["cases"]["full_text"] == "수정된 공식 전문"
    assert traced["exports"][0]["envelope"]["extra_provenance"]["pages"] == [2]
    assert "수정된 공식 판결요지" in traced["chunk"]["content"]
    with mysql_engine.begin() as c:
        for name, count in (("case_versions", 2), ("case_observations", 2), ("case_aliases", 2)):
            table = TABLES[name]
            assert c.execute(sa.select(sa.func.count()).select_from(table).where(table.c._snapshot_id == sid)).scalar_one() == count
        verify_snapshot(c, parent)
        verify_snapshot(c, sid)


def test_delete_is_scoped_and_invalid_late_edit_does_not_publish(mysql_engine, parent):
    delete = {"operation": "delete", "document_id": "guide-document:guide-1", "doc_type": "guide"}
    invalid = guide_change("", "zzz-invalid")
    with pytest.raises(ValueError):
        store(mysql_engine, plan(parent, [delete, invalid]))
    assert trace_chunk(mysql_engine, parent, "guide-1#0")
    with mysql_engine.begin() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(SNAPSHOTS)).scalar_one() == 1
    created, _ = store(mysql_engine, plan(parent, [delete]))
    with pytest.raises(ValueError, match="청크"):
        trace_chunk(mysql_engine, created["snapshot_id"], "guide-1#0")
    assert trace_chunk(mysql_engine, created["snapshot_id"], "case:Case-A#0")
    assert trace_chunk(mysql_engine, parent, "guide-1#0")


def test_change_plan_rejects_ambiguous_deletion_and_duplicate_targets(tmp_path):
    path = tmp_path / "changes.json"
    change = guide_change("안내")
    request = plan("0" * 64, [change, change])
    path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(ValueError, match="중복"):
        read_plan(path)
    request["documents"] = [dict(change, operation="delete")]
    path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(ValueError, match="삭제"):
        read_plan(path)


def test_law_rechunking_updates_article_structure_and_removes_stale_chunks(mysql_engine, parent):
    record = LawArticleRecord(law_name="테스트법", law_type="법률", ministry="테스트", law_code="TEST",
        proclamation_number="제1호", proclaimed_at="2026-01-01", effective_from="2026-01-01",
        content="① 새로운 조건이다.\n1. 새 예외이다.", source_url="https://example.test/law/1",
        collected_at="2026-09-18", article_number="제1조", source_text="새 수집 원문과 부칙",
        source_document_url="https://example.test/law")
    second = dict(asdict(record), article_number="제2조", content="② 두 번째 조문", source_text="",
                  source_url="https://example.test/law/2")
    change = {"operation": "replace", "document_id": "law-테스트법-20260101", "doc_type": "law",
              "records": [asdict(record), second]}
    first, _ = store(mysql_engine, plan(parent, [change]))
    one = "law-테스트법-20260101#제1조#0"
    two = "law-테스트법-20260101#제2조#0"
    traced = trace_chunk(mysql_engine, first["snapshot_id"], one)
    assert traced["source"]["law_source_snapshots"]["source_text"] == "새 수집 원문과 부칙"
    assert "새로운 조건" in traced["chunk"]["content"]
    change["records"] = [dict(second, source_text="제2조만 남은 새 수집 원문")]
    result, _ = store(mysql_engine, plan(first["snapshot_id"], [change], "v3"))
    with pytest.raises(ValueError, match="청크"):
        trace_chunk(mysql_engine, result["snapshot_id"], one)
    assert trace_chunk(mysql_engine, result["snapshot_id"], two)
    assert trace_chunk(mysql_engine, parent, one)
    assert trace_chunk(mysql_engine, result["snapshot_id"], "case:Case-A#0")


def test_case_deletion_keeps_prior_history_queryable(mysql_engine, parent):
    deleted = {"operation": "delete", "document_id": "case-document:Case-A", "doc_type": "case"}
    result, _ = store(mysql_engine, plan(parent, [deleted]))
    with mysql_engine.begin() as c:
        manifest = verify_snapshot(c, result["snapshot_id"])
        assert manifest["tables"]["case_versions"]["count"] == 0
        assert verify_snapshot(c, parent)["tables"]["case_versions"]["count"] == 1
    assert trace_chunk(mysql_engine, result["snapshot_id"], "guide-1#0")
