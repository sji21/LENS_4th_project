"""Keep candidate strata aligned with the fixed question identity."""
from scripts import patch041_retrieval_eval as baseline
from scripts.patch046_improvement_eval import BASELINE, report


def test_shuffled_capture_preserves_strata_and_comparison():
    rows = baseline.read(BASELINE / "rows.json")
    available = baseline.read(BASELINE / "audit.json")["available_articles"]
    expected = report(rows, available)
    assert report(list(reversed(rows)), available) == expected
    for mode in baseline.DEV_MODES:
        strata = [value for key, value in expected["required_count_strata"].items()
                  if key.startswith(mode + ":")]
        assert sum(x["n"] for x in strata) == 75
        assert sum(x["all"] for x in strata) == 43
    assert not expected["comparison"]["lost_required_inputs"]
