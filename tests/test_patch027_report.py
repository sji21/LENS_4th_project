import shutil

import pytest

from scripts.patch026_expand import read, write, sha
from scripts.patch027_partition import report
from scripts.patch027_report import BUNDLE, summarize


def test_replay_distinguishes_top3_loss_from_top5_preservation():
    result = summarize()
    assert result['product_matches'] == 235
    assert result['top5_preservation_passed']
    assert not result['top3_preservation_passed']
    assert not result['operating_data_adopted']
    chosen = result['variants']['pool_equal']
    assert chosen['prior_required_rank_losses']['3'] == [{
        'qid': 'DEV-044', 'mode': 'question_only', 'lost': ['주택임대차보호법-제3조']}]
    assert chosen['groups']['question_only']['union_all_required']['hits'] == 29
    assert chosen['groups']['context_diagnostic']['union_all_required']['hits'] == 31


def copied(tmp_path):
    target = tmp_path/'bundle'
    shutil.copytree(BUNDLE, target)
    return target


def test_missing_trace_and_manifest_entry_rejected(tmp_path):
    target = copied(tmp_path)
    manifest = read(target/'manifest.json')
    del manifest['traces.json']
    (target/'traces.json').unlink()
    write(target/'manifest.json', manifest)
    with pytest.raises(ValueError, match='Incomplete bundle'):
        summarize(target)


def test_missing_dependency_rejected_even_without_outer_manifest(tmp_path):
    target = copied(tmp_path)
    audit = read(target/'audit.json')
    audit['dependencies'].pop('data/eval/patch024-expansion/report.json')
    write(target/'audit.json', audit)
    with pytest.raises(ValueError, match='Incomplete dependencies'):
        report(target)


def test_duplicate_query_with_recomputed_hash_rejected(tmp_path):
    target = copied(tmp_path)
    traces = read(target/'traces.json')
    traces[-1] = traces[0]
    write(target/'traces.json', traces)
    audit = read(target/'audit.json')
    audit['traces_sha256'] = sha(target/'traces.json')
    write(target/'audit.json', audit)
    with pytest.raises(ValueError, match='Incomplete traces'):
        report(target)


def test_changed_law_identity_rejected(tmp_path):
    target = copied(tmp_path)
    audit = read(target/'audit.json')
    key = next(k for k, v in audit['candidate_anchors'].items() if v == '국세징수법-제109조')
    audit['candidate_anchors'][key] = '형법-제109조'
    write(target/'audit.json', audit)
    with pytest.raises(ValueError, match='Candidate inventory'):
        report(target)


def test_other_channel_change_rejected_even_with_new_hash(tmp_path):
    target = copied(tmp_path)
    live = read(target/'live.json')
    live['rows'][0]['cases'] = []
    write(target/'live.json', live)
    manifest = read(target/'manifest.json')
    manifest['live.json'] = sha(target/'live.json')
    write(target/'manifest.json', manifest)
    with pytest.raises(ValueError, match='another channel'):
        summarize(target)
