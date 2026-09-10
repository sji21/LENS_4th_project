"""Candidate coverage is distinct from final legal-answer correctness."""
import shutil

import pytest

from scripts.patch015_baseline import ROOT, read, sha, write
from scripts.patch016_candidates import coverage, replay, validate_capture


@pytest.fixture
def bundle(tmp_path):
    target = tmp_path/'bundle'
    shutil.copytree(ROOT/'data/eval/patch016-candidates', target)
    return target


def test_shared_results_reproduce_without_model(tmp_path):
    bundle = ROOT/'data/eval/patch016-candidates'
    out = tmp_path/'report'
    replay(bundle/'capture', out)
    for name in ('summary.json', 'details.json'):
        assert read(out/name) == read(bundle/'report'/name)


def test_valid_identifier_reordering_is_rejected(bundle, tmp_path):
    path = bundle/'capture/results.json'
    rows = read(path)
    ranked = rows[0]['rankings']['rrf']
    rows[0]['rankings']['rrf'] = ranked[1:] + ranked[:1]
    write(path, rows)
    out = tmp_path/'report'
    with pytest.raises(ValueError, match='hash/path'):
        replay(bundle/'capture', out)
    assert not out.exists()


@pytest.mark.parametrize('name', ['capture/audit.json', 'capture/results.json', 'capture/manifest.json', 'report/summary.json', 'report/details.json'])
def test_file_and_manifest_entry_cannot_both_be_removed(bundle, name):
    (bundle/name).unlink()
    manifest = read(bundle/'bundle-manifest.json')
    del manifest['files'][name]
    write(bundle/'bundle-manifest.json', manifest)
    with pytest.raises(ValueError, match='omits'):
        validate_capture(bundle/'capture')


@pytest.mark.parametrize('field,value', [('queries',234), ('operating_hashes_unchanged',False), ('policies_unchanged',False), ('policies_unchanged','true')])
def test_failed_audit_rejected_even_with_matching_hash(bundle, field, value):
    path = bundle/'capture/audit.json'
    audit = read(path)
    audit[field] = value
    write(path,audit)
    manifest = read(bundle/'bundle-manifest.json')
    manifest['files']['capture/audit.json'] = sha(path)
    write(bundle/'bundle-manifest.json',manifest)
    with pytest.raises(ValueError,match='audit'):
        validate_capture(bundle/'capture')


def test_missing_manifest_requires_explicit_local_mode_and_audit(bundle):
    (bundle/'bundle-manifest.json').unlink()
    with pytest.raises(ValueError,match='Missing bundle'):
        validate_capture(bundle/'capture')
    validate_capture(bundle/'capture',local_capture=True)
    (bundle/'capture/audit.json').unlink()
    with pytest.raises(ValueError,match='Missing capture file'):
        validate_capture(bundle/'capture',local_capture=True)


def test_local_mode_does_not_bypass_existing_bundle(bundle):
    (bundle/'capture/audit.json').unlink()
    with pytest.raises(ValueError,match='hash/path'):
        validate_capture(bundle/'capture',local_capture=True)


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
