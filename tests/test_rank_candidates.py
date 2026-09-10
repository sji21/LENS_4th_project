import pytest
from scripts.retrieval_experiments.rank_candidates import restrict_ranking, fused_ids


def test_common_ranking_overrides_source_scores_and_keeps_membership():
    pool = [{'id': 'a', 'score': 9999}, {'id': 'b', 'score': 0}]
    assert restrict_ranking(pool, ['outside', 'b', 'a']) == pool[::-1]


@pytest.mark.parametrize('pool,order', [([{'id': 'a'}], ['b']),
    ([{'id': 'a'}, {'id': 'a'}], ['a']), ([{'id': 'a'}], ['a', 'a'])])
def test_missing_or_duplicate_ranks_fail_closed(pool, order):
    with pytest.raises(ValueError):
        restrict_ranking(pool, order)


def test_fusion_rewards_agreement_without_using_raw_scores():
    hits = [[('a', 999), ('b', 0)], [('b', -999), ('c', -1000)]]
    assert fused_ids(hits, [1, 1], 5)[0] == 'b'
    assert fused_ids([[(cid, 0) for cid, _ in h] for h in hits], [1, 1], 5) == fused_ids(hits, [1, 1], 5)
