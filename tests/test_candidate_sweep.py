"""Candidate-stage denominators and source budgets, independent of live models."""
import pytest

from scripts.retrieval_experiments.candidate_sweep import coverage, split_candidates, variants


def pool(prefix, count):
    return [{'id': f'{prefix}{i}', 'article': f'{prefix}{i}', 'text_chars': 10,
             'score_in_source': 1000 if prefix == 'civil' else 1} for i in range(count)]


def test_split_budgets_preserve_each_source_without_comparing_scores():
    general, civil = pool('law', 20), pool('civil', 7)
    selected = split_candidates(general, civil, 10, 3)
    assert selected == general[:10] + civil[:3]
    assert len(selected) == 13


def test_short_pool_is_not_padded_with_duplicates():
    assert len(split_candidates(pool('law', 2), pool('civil', 1), 10, 3)) == 3


def test_overlapping_source_pools_are_rejected():
    with pytest.raises(ValueError, match='Overlapping'):
        split_candidates(pool('same', 1), pool('same', 1), 1, 1)


def test_equal_budget_controls_are_present():
    result = variants(pool('law', 20), pool('civil', 7), pool('unified', 20))
    for split, unified in [('split_g8_c2', 'unified_10'), ('split_g10_c2', 'unified_12'),
                           ('split_g10_c3', 'unified_13'), ('split_g10_c5', 'unified_15'),
                           ('split_g15_c5', 'unified_20')]:
        assert len(result[split]) == len(result[unified])


@pytest.mark.parametrize('targets,available,historical,eligible,complete,held,found', [
    ({'a', 'b'}, {'a', 'b'}, False, True, False, 2, 1),
    ({'a'}, {'a'}, False, True, True, 1, 1),
    ({'a', 'b'}, {'a'}, False, False, False, 1, 1),
    (set(), {'a'}, False, False, False, 0, 0),
    ({'a'}, {'a'}, True, False, False, 0, 0),
])
def test_coverage_does_not_hide_partial_data_or_count_empty_targets_as_success(
    targets, available, historical, eligible, complete, held, found,
):
    result = coverage(targets, available, [{'article': 'a'}], historical)
    assert result['eligible'] is eligible
    assert result['all_required_found'] is complete
    assert result['held_targets'] == held
    assert result['held_targets_found'] == found
