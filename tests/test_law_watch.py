from unittest.mock import patch
import pytest
from chat.law_watch import check_law
from chat.models import LawWatch, LawAlert
from src.generation.source_links import citation_url

pytestmark = pytest.mark.django_db


def test_change_alert_is_recorded_once_and_failure_preserves_baseline():
    first = {"시행일자": "20260102", "공포일자": "20251001"}
    second = {"시행일자": "20270102", "공포일자": "20261001"}
    with patch("chat.law_watch.fetch_metadata", side_effect=[first, second, second, RuntimeError("secret")]):
        assert check_law("주택임대차보호법", "test") == "unchanged"
        assert check_law("주택임대차보호법", "test") == "changed"
        assert check_law("주택임대차보호법", "test") == "unchanged"
        assert check_law("주택임대차보호법", "test") == "failed"
    assert LawAlert.objects.count() == 1
    assert LawWatch.objects.get().metadata == second
    assert "secret" not in LawWatch.objects.get().last_error


def test_first_check_reports_corpus_date_mismatch():
    with patch("chat.law_watch.fetch_metadata", return_value={"시행일자": "20270102"}):
        assert check_law("민법", "test", {"2026-03-17"}) == "changed"
    assert LawAlert.objects.count() == 1


def test_anonymous_cannot_read_alerts(client):
    assert client.get("/admin/chat/lawalert/").status_code == 302


def test_source_url_construction():
    from urllib.parse import unquote
    assert unquote(citation_url("주택임대차보호법 제3조의2", "law")).endswith("/주택임대차보호법/제3조의2")
    assert "q=2020" in citation_url("대법원 2020다12345", "case")
    assert citation_url("알 수 없음", "law") == ""


def test_metadata_requires_exact_title_and_complete_dates():
    import json
    from io import BytesIO
    from chat.law_watch import fetch_metadata
    row = {"법령명한글": "민법 시행령", "법령ID": "1", "공포일자": "20260101", "시행일자": "20260102"}
    with patch("chat.law_watch.urlopen", return_value=BytesIO(json.dumps({"LawSearch": {"totalCnt": 1, "law": row}}).encode())):
        with pytest.raises(ValueError):
            fetch_metadata("민법", "test")
    row["법령명한글"] = "민법"
    row["시행일자"] = ""
    with patch("chat.law_watch.urlopen", return_value=BytesIO(json.dumps({"LawSearch": {"totalCnt": 1, "law": row}}).encode())):
        with pytest.raises(ValueError):
            fetch_metadata("민법", "test")
