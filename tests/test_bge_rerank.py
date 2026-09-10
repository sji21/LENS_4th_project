import pytest
from scripts.retrieval_experiments.bge_rerank import rank_scores, validate_lengths


def test_ranking_uses_model_score_and_deterministic_id_ties():
    pool = [{'id': 'z'}, {'id': 'b'}, {'id': 'a'}]
    assert [r['id'] for r in rank_scores(pool, [-5, 2, 2])] == ['a', 'b', 'z']


@pytest.mark.parametrize('scores', [[1], [float('nan'), 1], [1, float('inf')]])
def test_invalid_model_output_fails_closed(scores):
    with pytest.raises(ValueError):
        rank_scores([{'id': 'a'}, {'id': 'b'}], scores)


def test_long_legal_text_is_rejected_instead_of_truncated():
    validate_lengths([1131, 2048], 2048)
    with pytest.raises(ValueError):
        validate_lengths([2049], 2048)


@pytest.mark.parametrize('ids,completed_count', [(['a', 'a'], 2), (['a', 'b'], 1)])
def test_report_rejects_duplicate_or_incomplete_execution(tmp_path, ids, completed_count):
    import json
    from scripts.retrieval_experiments.report_bge import completed
    (tmp_path / 'results.jsonl').write_text('\n'.join(json.dumps({'id': i}) for i in ids), encoding='utf-8')
    (tmp_path / 'audit.json').write_text(json.dumps({'completed': completed_count}), encoding='utf-8')
    with pytest.raises(ValueError, match='Incomplete'):
        completed(tmp_path, 2)
