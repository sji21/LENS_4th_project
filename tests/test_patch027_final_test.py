from copy import deepcopy
import json
import shutil

import pytest

from scripts.patch027_final_policy import (
    EFFECT, RECORDS, final_law_concepts, requests_date_record,
)
from scripts.patch027_context_tuning import law_concepts
from scripts.patch027_final_test import quantitative_checks, transitions
from scripts import patch027_final_test as trial


@pytest.mark.parametrize("query", [
    "확정일자 기록을 지금 조회할 수 있나요?",
    "확정일자의 부여 내역을 확인하고 싶어요",
    "확정일자를 받았는지 기억이 안 나요. 예전 기록부터 어떻게 확인하죠?",
    "확정일자 유무를 모르겠습니다. 당시의 내역을 찾을 수 있나요?",
])
def test_record_lookup_uses_current_bound_record_target(query):
    assert requests_date_record(query)
    assert RECORDS in final_law_concepts(query)
    assert EFFECT not in final_law_concepts(query)


@pytest.mark.parametrize("query", [
    "확정일자를 받으면 어떤 보호를 받나요?",
    "확정일자 기록을 확인하면 우선변제를 받을 수 있나요?",
    "확정일자 유무는 묻지 않아요. 예전 기록을 확인해 주세요",
    "확정일자 기록이 아니라 수리 기록을 확인하려고요",
    "확정일자를 받았는지 몰라요. 수리 기록을 확인해 주세요",
    "확정일자를 받았는지 몰라요. 보일러가 고장 났어요. 예전 기록을 확인하려고요",
    '이전 대화: 확정일자 기록 확인을 요청했습니다.\n사용자 질문: 수리 내역을 조회할 수 있나요?',
    '예시는 “확정일자 기록을 확인한다”입니다. 수리 기록을 조회하려고요',
])
def test_unrelated_effect_quoted_and_earlier_requests_do_not_override(query):
    assert not requests_date_record(query)
    assert final_law_concepts(query) == law_concepts(query)


def example(hits=43, consumed=32):
    groups = {mode: {"union_all_required": {"hits": hits, "n": 75}}
              for mode in ("question_only", "context_diagnostic")}
    groups["required_law"] = {"union_all_required": {"hits": 28, "n": 29},
        "channel_metrics": {"civil": {"all_target_items": {"n": 29, "hit@3": 1}}}}
    return {"groups": groups, "consumed_groups": {
        mode: {"union_all_required": {"hits": consumed, "n": 75}}
        for mode in ("question_only", "context_diagnostic")},
        "new_required_civil": list(range(9)), "losses": [{"qid": "existing-loss"}]}


def test_acceptance_allows_losses_but_not_net_or_consumed_regression():
    baseline, candidate = example(28, 20), example()
    assert all(quantitative_checks(candidate, baseline).values())
    for field in ("groups", "consumed_groups"):
        changed = deepcopy(candidate)
        changed[field]["context_diagnostic"]["union_all_required"]["hits"] = 19
        assert not all(quantitative_checks(changed, baseline).values())
    changed = deepcopy(candidate)
    changed["groups"]["question_only"]["union_all_required"]["n"] = 74
    assert not all(quantitative_checks(changed, baseline).values())


def test_transition_counts_keep_both_directions_and_ignore_unscored():
    def row(qid, hit):
        return {"qid": qid, "mode": "question_only", "all_required_law5_civil3": hit}
    before = [row("a", False), row("b", True), row("c", None)]
    after = [row("c", None), row("b", False), row("a", True)]
    assert transitions(before, after)["question_only"] == {"gained": ["a"], "lost": ["b"], "net": 0}


@pytest.fixture(scope="module")
def final_result():
    return trial.check()


def test_final_capture_replays_gains_losses_and_consumed_budget(final_result):
    assert final_result["selected"] == "record_lookup"
    candidate = final_result["results"]["record_lookup"]
    assert all(candidate["checks"].values())
    assert len(candidate["losses"]) == 7
    assert candidate["groups"]["question_only"]["union_all_required"] == {"hits": 43, "n": 75}
    assert candidate["groups"]["context_diagnostic"]["union_all_required"] == {"hits": 43, "n": 75}
    assert candidate["consumed_groups"]["context_diagnostic"]["union_all_required"] == {"hits": 38, "n": 75}
    assert {r["qid"] for r in final_result["record_lookup_changes"]} == {"DEV-099"}


@pytest.mark.parametrize("changed_file", ["report.json", "rows.json"])
def test_rehashed_report_or_evidence_tampering_is_rejected(tmp_path, changed_file, final_result):
    dest = tmp_path / "final"
    shutil.copytree(trial.BUNDLE, dest)
    content = trial.read(dest / changed_file)
    if changed_file == "report.json":
        content["results"]["record_lookup"]["losses"] = []
    else:
        content[0]["record_lookup"]["laws"][0]["source_url"] = "https://example.invalid/wrong-source"
    trial.write(dest / changed_file, content)
    manifest = trial.read(dest / "manifest.json")
    manifest[changed_file] = trial.sha(dest / changed_file)
    trial.write(dest / "manifest.json", manifest)
    with pytest.raises(ValueError):
        trial.check(dest)


def test_missing_file_and_manifest_entry_are_both_required(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        trial.check(tmp_path)
