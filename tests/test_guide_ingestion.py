"""공식 안내 수집·적재 테스트.

네트워크를 타지 않는다. 수집 단계는 저장한 HTML 조각으로 파싱만 확인한다.
확인하려는 것은 **본문 경계를 제대로 자르는지**다. 실제로 국세청 페이지에서
끝 표시를 못 잡아 청크 하나가 통째로 유튜브·SNS 링크 목록이 된 적이 있다.
"""

from __future__ import annotations

import gc
import json
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pytest

from src.database.relational import connect_database, initialize_relational_database
from src.ingestion.fetch_guides import (
    GuideSource,
    extract_body,
    html_to_lines,
)
from src.ingestion.load_guides import (
    GuideRecord,
    export_guide_chunks,
    load_guide_records,
    split_content,
)

# 본문 뒤에 다음글·만족도조사·기관 링크가 이어지는 실제 구조를 줄인 것이다.
NOTICE_HTML = """
<html><head><title>x</title></head><body>
<div id="menu"><p>세무조사 개요</p><p>사업자등록 안내</p></div>
<div class="view">
<h2>｢미납국세등 열람신청(주택임차, 상가임차)｣ 홈택스 화면 안내</h2>
<p>작성자</p><p>관리자</p><p>작성일자</p><p>2023.06.30.</p><p>조회수 106448</p>
<p>개요</p>
<p>임대차개시일까지 세무서에 방문해 신청하면 임대인 동의 없이 미납국세를 열람할 수 있습니다.</p>
<p>신청 방법</p>
<p>홈택스 로그인 후 신청/제출 메뉴에서 미납국세등 열람신청을 선택합니다.</p>
<p>다음글</p><p>2023년 9급 공무원 채용시험 합격자 안내</p>
<p>이전글</p><p>2023 국세청 웹툰 공모전</p>
<p>콘텐츠 만족도 조사</p><p>국세청 유튜브</p><p>국세청 인스타그램</p>
</div></body></html>
"""

NOTICE = GuideSource(
    guide_id="guide-테스트-미납국세",
    title="테스트 안내",
    agency="국세청",
    guide_type="민원안내",
    topic="미납국세 열람",
    url="https://example.invalid/notice",
    start_marker="열람신청(주택임차",
    end_markers=("다음글", "이전글", "콘텐츠 만족도"),
    date_marker="작성일자",
)


class TestBodyExtraction:
    def test_body_stops_before_the_footer(self):
        body, _ = extract_body(html_to_lines(NOTICE_HTML), NOTICE)
        assert "미납국세를 열람할 수 있습니다" in body
        for noise in ("공무원 채용시험", "웹툰 공모전", "유튜브", "인스타그램", "만족도"):
            assert noise not in body, f"푸터가 본문에 남았다: {noise}"

    def test_menu_above_the_start_marker_is_dropped(self):
        body, _ = extract_body(html_to_lines(NOTICE_HTML), NOTICE)
        assert "세무조사 개요" not in body
        assert "사업자등록 안내" not in body

    def test_published_date_comes_from_the_page(self):
        _, published_at = extract_body(html_to_lines(NOTICE_HTML), NOTICE)
        assert published_at == "2023-06-30"

    def test_bookkeeping_lines_are_dropped(self):
        """조회수는 요청마다 바뀐다. 남기면 checksum 이 매번 달라져 멱등성이 깨진다."""
        body, _ = extract_body(html_to_lines(NOTICE_HTML), NOTICE)
        for noise in ("작성자", "관리자", "조회수", "작성일자"):
            assert noise not in body

    def test_missing_start_marker_yields_nothing(self):
        """페이지 구조가 바뀌면 빈 본문이 나와야 한다. 엉뚱한 데서 시작하면 안 된다."""
        other = GuideSource(
            guide_id="g", title="t", agency="a", guide_type="t", topic="t",
            url="u", start_marker="존재하지 않는 표시",
        )
        body, published_at = extract_body(html_to_lines(NOTICE_HTML), other)
        assert body == ""
        assert published_at == ""

    def test_end_marker_on_the_first_line_does_not_empty_the_body(self):
        """시작 줄이 목차라 끝 표시를 함께 담는 경우가 있다(HUG 가 그렇다)."""
        lines = ["개요: 보증한도, 위탁금융기관으로 구성됨", "보증한도는 90% 입니다", "위탁금융기관", "은행 목록"]
        source = GuideSource(
            guide_id="g", title="t", agency="a", guide_type="t", topic="t",
            url="u", start_marker="개요", end_markers=("위탁금융기관",),
        )
        body, _ = extract_body(lines, source)
        assert "보증한도는 90% 입니다" in body
        assert "은행 목록" not in body


class TestSplitContent:
    def test_short_content_stays_one_chunk(self):
        assert len(split_content("짧은 안내입니다.")) == 1

    def test_long_content_is_split_on_line_boundaries(self):
        content = "\n".join(f"{i}번 줄입니다 " + "가" * 80 for i in range(10))
        chunks = split_content(content, target=300)
        assert len(chunks) > 1
        # 줄 중간에서 끊기지 않아야 한다
        for chunk in chunks:
            for line in chunk.splitlines():
                assert line in content

    def test_no_content_is_lost(self):
        content = "\n".join(f"줄 {i}" for i in range(20))
        assert "\n".join(split_content(content, target=30)).count("줄 ") == 20


def record(guide_id: str = "guide-테스트-1", content: str = "안내 본문입니다.") -> GuideRecord:
    # documents 에 (source_url, checksum) UNIQUE 가 걸려 있다. 안내마다 출처가
    # 다르므로 URL 도 함께 달리한다.
    return GuideRecord(
        guide_id=guide_id, title="테스트 안내", agency="국세청", guide_type="민원안내",
        topic="미납국세 열람", source_url=f"https://example.invalid/{guide_id}",
        published_at="2023-06-30", content=content, collected_at="2026-08-29",
        published_at_source="page",
    )


class TestLoading:
    @pytest.fixture()
    def database(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "t.sqlite3"
            initialize_relational_database(path)
            yield path
            gc.collect()

    def counts(self, connection):
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("documents", "guides", "chunks")
        }

    def test_reloading_the_same_input_does_not_duplicate(self, database):
        with closing(connect_database(database)) as connection:
            load_guide_records([record()], connection)
            connection.commit()
            first = self.counts(connection)
            load_guide_records([record()], connection)
            assert self.counts(connection) == first

    def test_guide_missing_from_the_input_is_removed(self, database):
        """원천에서 뺀 안내가 남으면 계속 검색된다."""
        with closing(connect_database(database)) as connection:
            load_guide_records([record("guide-a"), record("guide-b")], connection)
            assert self.counts(connection)["guides"] == 2
            summary = load_guide_records([record("guide-a")], connection)
            assert summary.removed == 1
            assert self.counts(connection)["guides"] == 1

    def test_shrinking_a_guide_removes_its_stale_chunks(self, database):
        long_text = "\n".join(f"문단 {i} " + "나" * 200 for i in range(6))
        with closing(connect_database(database)) as connection:
            load_guide_records([record(content=long_text)], connection)
            many = self.counts(connection)["chunks"]
            load_guide_records([record(content="짧아졌습니다.")], connection)
            assert self.counts(connection)["chunks"] == 1 < many

    def test_empty_content_is_skipped_and_reported(self, database):
        with closing(connect_database(database)) as connection:
            summary = load_guide_records([record(content="   ")], connection)
            assert summary.guides == 0
            assert summary.skipped and "본문이 비어 있음" in summary.skipped[0]

    def test_export_matches_the_chunk_schema(self, database):
        with TemporaryDirectory() as temp:
            out = Path(temp) / "guides.jsonl"
            with closing(connect_database(database)) as connection:
                load_guide_records([record()], connection)
                assert export_guide_chunks(connection, out) == 1
            chunk = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
            assert chunk["metadata"]["doc_type"] == "guide"
            assert chunk["metadata"]["article_id"] == "guide-테스트-1"
            assert chunk["metadata"]["effective_date"] == "2023-06-30"
            assert chunk["metadata"]["collected_at"] == "2026-08-29"
            assert chunk["metadata"]["published_at_source"] == "page"
            assert chunk["text"].startswith("[테스트 안내]")

    def test_unknown_publication_date_is_not_replaced_with_collection_date(self, database):
        """수집일은 게시일이나 시행일이 아니다."""
        source = record()
        source = GuideRecord(
            **{
                **source.__dict__,
                "published_at": "2026-08-29",
                "published_at_source": "unknown",
            }
        )
        with TemporaryDirectory() as temp:
            out = Path(temp) / "guides.jsonl"
            with closing(connect_database(database)) as connection:
                load_guide_records([source], connection)
                assert export_guide_chunks(connection, out) == 1
            metadata = json.loads(out.read_text(encoding="utf-8"))["metadata"]
            assert metadata["effective_date"] == ""
            assert metadata["collected_at"] == "2026-08-29"
            assert metadata["published_at_source"] == "unknown"


class TestCliGuards:
    def test_empty_records_file_is_refused(self, tmp_path, capsys):
        """빈 입력으로 진행하면 stale 정리가 안내를 통째로 지운다."""
        from src.ingestion.load_guides import main

        records = tmp_path / "empty.jsonl"
        records.write_text("", encoding="utf-8")
        argv = ["load_guides", "--records", str(records),
                "--database", str(tmp_path / "t.sqlite3"),
                "--export", str(tmp_path / "out.jsonl")]
        with mock.patch.object(sys, "argv", argv):
            assert main() == 1
        assert "비어 있습니다" in capsys.readouterr().out
        assert not (tmp_path / "out.jsonl").exists()


class TestAtomicity:
    """실패로 보고하면서 DB 를 절반만 바꿔 놓으면 안 된다."""

    def test_load_does_not_commit_on_its_own(self, tmp_path):
        """호출한 쪽이 rollback 할 수 있어야 한다."""
        database = tmp_path / "t.sqlite3"
        initialize_relational_database(database)
        with closing(connect_database(database)) as connection:
            load_guide_records([record()], connection)
            connection.rollback()
            assert connection.execute("SELECT COUNT(*) FROM guides").fetchone()[0] == 0
        gc.collect()

    def test_skipped_record_rolls_back_the_whole_run(self, tmp_path, capsys):
        """빈 본문 하나가 섞이면 그 실행 전체를 되돌린다."""
        from src.ingestion.load_guides import main

        database = tmp_path / "t.sqlite3"
        good, bad = record("guide-a"), record("guide-b", content="   ")
        records = tmp_path / "records.jsonl"
        records.write_text(
            "\n".join(
                json.dumps(r.__dict__, ensure_ascii=False) for r in (good, bad)
            ),
            encoding="utf-8",
        )
        export = tmp_path / "out.jsonl"
        argv = ["load_guides", "--records", str(records),
                "--database", str(database), "--export", str(export)]
        with mock.patch.object(sys, "argv", argv):
            assert main() == 1

        out = capsys.readouterr().out
        assert "건너뛴 레코드 1건" in out
        assert "되돌렸" in out
        assert not export.exists()
        with closing(connect_database(database)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM guides").fetchone()[0] == 0
        gc.collect()
