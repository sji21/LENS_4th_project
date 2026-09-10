"""Protect denominators, article identity and failure-stage reporting."""
import pytest

from scripts.patch015_baseline import score
from scripts.patch015_report import check_shared_bundle, summarize


def test_multiple_required_articles_and_no_rank_deduplication():
    r=score({'민법-제626조','민법-제627조'},['민법-제626조']*3+['민법-제627조'],{'민법-제626조','민법-제627조'},set())
    assert r['metrics']=={'AnyHit@3':1,'AllRequired@3':0,'AnyHit@5':1,'AllRequired@5':1}


def test_empty_targets_are_not_successes():
    r=score(set(),[],set(),set())
    assert all(v is None for v in r['metrics'].values())
    with pytest.raises(ValueError): summarize([r])
    assert summarize([])['AllRequired@3']['rate'] is None


def test_missing_data_and_candidate_stages_can_coexist():
    r=score({'민법-제1조','민법-제2조','민법-제3조'},[],{'민법-제2조','민법-제3조'},{'민법-제3조'})
    assert r['absent']==['민법-제1조']
    assert r['candidate_not_observed']==['민법-제2조']
    assert r['candidate_present_not_top5']==['민법-제3조']
    assert summarize([r])['AllRequired@5']=={'hits':0,'n':1,'rate':0.0}


def test_full_law_identity_and_branch_number_preserved():
    r=score({'상가건물 임대차보호법-제10조의8'},['상가건물임대차보호법-제10조'],{'상가건물임대차보호법-제10조의8'},set())
    assert r['metrics']['AnyHit@5']==0
    assert not r['absent']
    r=score({'민법-제114조'},['형법-제114조'],{'민법-제114조'},set())
    assert r['metrics']['AnyHit@5']==0


def test_bundle_cannot_drop_both_capture_file_and_manifest_entry(tmp_path):
    from scripts.patch015_baseline import write
    run=tmp_path/'capture'
    run.mkdir()
    write(tmp_path/'bundle-manifest.json',{'files':{}})
    with pytest.raises(ValueError,match='omits'): check_shared_bundle(run)
