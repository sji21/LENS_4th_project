"""Fixed fusion is deterministic and must not hide lost evidence."""
import pytest

from scripts.patch017_selection import blend, measure


def test_equal_scores_preserve_existing_rank_and_alternate_lists():
    assert blend(['A','B','C','D','E'],['X','Y','Z'],1)==['A','X','B','Y','C']


def test_consensus_article_occupies_one_slot():
    ranked=blend(['A','B','C','D','E'],['B','X'],1)
    assert ranked[0]=='B'
    assert len(ranked)==len(set(ranked))==5


def test_query_specific_gold_is_not_needed_by_fusion():
    assert blend(['민법-제114조'],['형법-제114조'],1)==['민법-제114조','형법-제114조']
    with pytest.raises(ValueError,match='Duplicate'):
        blend(['A','A'],['B'],1)


def test_partial_evidence_loss_counts_even_when_whole_item_already_failed():
    result=measure({'A','missing'},['A','B','C'],['X','B','C'])
    assert result['3']['all']==0
    assert result['3']['lost_required']==['A']


def test_empty_targets_are_not_successes():
    result=measure(set(),['A'],['B'])
    assert result['3']['all'] is None
    assert result['5']['any'] is None
