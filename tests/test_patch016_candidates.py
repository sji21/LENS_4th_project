"""Candidate coverage is distinct from final legal-answer correctness."""
from scripts.patch016_candidates import coverage


def test_all_required_civil_targets_must_be_present():
    ranked=['민법-제626조','민법-제623조','민법-제627조']
    targets={'민법-제626조','민법-제627조'}
    assert coverage(targets,ranked,2)==0
    assert coverage(targets,ranked,3)==1


def test_targetless_queries_are_observations_not_automatic_successes():
    assert coverage(set(),['민법-제626조'],7) is None


def test_normalization_does_not_merge_different_laws_or_subarticles():
    assert coverage({'민법 - 제626조'},['민법-제626조'],1)==1
    assert coverage({'민법-제114조'},['형법-제114조'],1)==0
    assert coverage({'상가건물임대차보호법-제10조의8'},['상가건물임대차보호법-제10조'],1)==0
