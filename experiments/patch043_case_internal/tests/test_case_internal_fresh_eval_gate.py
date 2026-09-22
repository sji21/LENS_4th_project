import json

import pytest

from experiments.patch043_case_internal.scripts.case_internal_fresh_eval_gate import _write_exclusive, evaluate


def _write(path, rows):
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def test_gate_rejects_same_question_with_spacing_change(tmp_path):
    prior = tmp_path / "prior.json"
    candidate = tmp_path / "candidate.json"
    _write(prior, [{"qid": "OLD-1", "question": "임대인이 바뀌면 어떻게 되나요?"}])
    _write(candidate, [{"qid": "NEW-1", "question": "임대인이  바뀌면 어떻게 되나요?"}])

    report = evaluate(candidate, [prior])

    assert report["pass"] is False
    assert report["checks"]["exact_normalized_question_overlap_count"] == 1


def test_gate_rejects_reused_identifier(tmp_path):
    prior = tmp_path / "prior.jsonl"
    candidate = tmp_path / "candidate.jsonl"
    prior.write_text('{"qid":"Q-1","question":"기존 질문"}\n', encoding="utf-8")
    candidate.write_text('{"qid":"Q-1","question":"완전히 새 질문"}\n', encoding="utf-8")

    report = evaluate(candidate, [prior])

    assert report["pass"] is False
    assert report["qid_overlap"] == ["Q-1"]


def test_gate_accepts_new_exact_identities(tmp_path):
    prior = tmp_path / "prior.json"
    candidate = tmp_path / "candidate.json"
    _write(prior, [{"qid": "OLD-1", "question": "기존 질문"}])
    _write(candidate, [{"qid": "NEW-1", "question": "새 판례 질문"}])

    report = evaluate(candidate, [prior])

    assert report["pass"] is True
    assert report["checks"]["qid_overlap_count"] == 0
    assert report["checks"]["exact_normalized_question_overlap_count"] == 0


def test_gate_ignores_nested_gold_explanation_text(tmp_path):
    candidate = tmp_path / "candidate.json"
    _write(
        candidate,
        [
            {
                "qid": "NEW-1",
                "question": "새 질문",
                "groups": [{"group_id": "G1", "text": "근거 설명"}],
            }
        ],
    )
    prior = tmp_path / "prior.json"
    _write(prior, [{"qid": "OLD-1", "question": "기존 질문"}])

    report = evaluate(candidate, [prior])

    assert report["candidate"]["valid_question_count"] == 1


def test_gate_rejects_idless_prior_question(tmp_path):
    prior = tmp_path / "prior.json"
    candidate = tmp_path / "candidate.json"
    _write(prior, [{"question": "재사용 질문"}])
    _write(candidate, [{"qid": "NEW-1", "question": "재사용 질문"}])

    report = evaluate(candidate, [prior])

    assert report["pass"] is False
    assert report["checks"]["all_prior_formats_complete_and_nonempty"] is False


def test_gate_rejects_candidate_with_hidden_idless_record(tmp_path):
    prior = tmp_path / "prior.json"
    candidate = tmp_path / "candidate.json"
    _write(prior, [{"qid": "OLD-1", "question": "기존 질문"}])
    _write(candidate, [{"qid": "NEW-1", "question": "새 질문"}, {"question": "기존 질문"}])

    report = evaluate(candidate, [prior])

    assert report["pass"] is False
    assert report["candidate"]["invalid_record_indices"] == [1]


def test_gate_enforces_expected_candidate_count(tmp_path):
    prior = tmp_path / "prior.json"
    candidate = tmp_path / "candidate.json"
    _write(prior, [{"qid": "OLD-1", "question": "기존 질문"}])
    _write(candidate, [{"qid": "NEW-1", "question": "새 질문"}])

    report = evaluate(candidate, [prior], expected_candidate_count=100)

    assert report["pass"] is False
    assert report["checks"]["expected_candidate_count"] == 100


def test_gate_rejects_empty_prior_list(tmp_path):
    candidate = tmp_path / "candidate.json"
    _write(candidate, [{"qid": "NEW-1", "question": "새 질문"}])

    report = evaluate(candidate, [])

    assert report["pass"] is False
    assert report["checks"]["all_prior_formats_complete_and_nonempty"] is False


def test_gate_rejects_candidate_internal_duplicates(tmp_path):
    prior = tmp_path / "prior.json"
    candidate = tmp_path / "candidate.json"
    _write(prior, [{"qid": "OLD-1", "question": "기존 질문"}])
    _write(
        candidate,
        [
            {"qid": "NEW-1", "question": "새 질문"},
            {"qid": "NEW-1", "question": "새 질문"},
        ],
    )

    report = evaluate(candidate, [prior])

    assert report["pass"] is False
    assert report["candidate"]["duplicate_qids"] == ["NEW-1"]


def test_exclusive_output_does_not_overwrite(tmp_path):
    output = tmp_path / "result.json"
    _write_exclusive(output, b"first")

    with pytest.raises(FileExistsError):
        _write_exclusive(output, b"second")

    assert output.read_bytes() == b"first"
