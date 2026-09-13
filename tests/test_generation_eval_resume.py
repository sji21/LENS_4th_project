"""--resume가 코드 변경 전 checkpoint 결과를 그대로 재사용하지 않는지 확인한다.

배경: 평가를 중단했다가 --resume로 이어서 실행하면, 이미 끝난 문항은 재평가
없이 checkpoint의 예전 결과를 그대로 썼다. 그 사이 생성·검색·보안 코드가
바뀌었어도 오래된 결과가 새 실행 결과와 섞여 status_accuracy 같은 지표를
왜곡할 수 있었다. compute_code_version()이 만드는 해시를 각 checkpoint 행에
남기고, load_completed()가 현재 해시와 다른 행은 재사용하지 않도록 고쳤다.
"""
import json
from unittest import mock

from src.evaluation import run_generation_eval as eval_mod


def _row(qid, **overrides):
    base = dict(
        qid=qid, question="q", expected_type="answerable", expected_status="answered",
        actual_status="answered", status_match=True, category="c", difficulty="d",
        gold_articles=[], retrieved_articles=[], retrieved_gold_articles=[], gold_recall=None,
        retrieval_mode="hybrid", final_answer="a", source_labels=[], source_chunk_ids=[],
        elapsed_seconds=1.0, failure_stage="", manual_correct="", manual_note="",
        validation_mode="deterministic",
    )
    base.update(overrides)
    return base


def test_compute_code_version_is_deterministic():
    assert eval_mod.compute_code_version() == eval_mod.compute_code_version()


def test_compute_code_version_changes_when_tracked_source_changes(tmp_path):
    tracked = tmp_path / "tracked.py"
    tracked.write_text("A = 1\n", encoding="utf-8")

    with mock.patch.object(eval_mod, "_VERSION_SOURCE_FILES", (tracked,)):
        before = eval_mod.compute_code_version()
        tracked.write_text("A = 2\n", encoding="utf-8")
        after = eval_mod.compute_code_version()

    assert before != after


def test_load_completed_reuses_only_matching_code_version(tmp_path):
    checkpoint = tmp_path / "run.jsonl"
    current_version = "current-version"
    rows = [
        _row("dev-same", code_version=current_version),
        _row("dev-stale", code_version="old-version"),
        _row("dev-legacy"),  # code_version 필드 자체가 없던 과거 checkpoint
    ]
    with checkpoint.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    completed = eval_mod.load_completed(checkpoint, current_version)

    assert set(completed) == {"dev-same"}


def test_load_completed_without_resume_state_stays_empty(tmp_path):
    missing_checkpoint = tmp_path / "missing.jsonl"
    assert eval_mod.load_completed(missing_checkpoint, "any-version") == {}
