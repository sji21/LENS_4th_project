"""Channel separation preserves evidence without treating missing gold as rejection."""
import pytest
import hashlib

from scripts.patch015_baseline import write
from scripts.patch018_separate import item_score, join_channels, validate_capture, same_text


@pytest.mark.parametrize('captured_eol', [b'\n', b'\r\n', b'\r\n\n'])
@pytest.mark.parametrize('checkout_eol', [b'\n', b'\r\n'])
def test_text_hash_accepts_only_line_endings(tmp_path, captured_eol, checkout_eol):
    content = b'{"value": 1}\n'
    captured = tmp_path/'original.bin'
    captured.write_bytes(content.replace(b'\n', captured_eol))
    if captured_eol == b'\r\n\n':
        content += b'\n'
    path = tmp_path/'manifest.json'
    path.write_bytes(content.replace(b'\n', checkout_eol))
    assert same_text(path, captured)
    path.write_bytes(b'{"value": 2}' + checkout_eol)
    assert not same_text(path, captured)


def test_capture_validates_archived_runner_and_current_dependencies(tmp_path, monkeypatch):
    import scripts.patch018_separate as experiment
    monkeypatch.setattr(experiment, 'ROOT', tmp_path)
    capture = tmp_path/'capture'
    capture.mkdir()
    original = b'original runner\n'
    (capture/'runner.py').write_bytes(original)
    (tmp_path/'dependency.json').write_bytes(b'{}\n')
    originals = capture/'source-bytes'
    originals.mkdir()
    (originals/'dependency.json.bin').write_bytes(b'{}\r\n')
    write(capture/'manifest.json', {'operating_before':{}, 'source_hashes':{
        'scripts/patch018_separate.py':hashlib.sha256(original).hexdigest(),
        'dependency.json':hashlib.sha256(b'{}\r\n').hexdigest()}})
    write(capture/'audit.json', {'queries':235,'operating_unchanged':True,
        'sources_unchanged':True,'general_prefix_preserved':True,'gate_preserved':True,
        'operating_after':{}})
    validate_capture(capture, local=True)
    (capture/'runner.py').write_bytes(b'changed runner\n')
    with pytest.raises(ValueError, match='Capture source changed'):
        validate_capture(capture, local=True)
    (capture/'runner.py').write_bytes(original)
    (tmp_path/'dependency.json').write_bytes(b'{"changed": true}\n')
    with pytest.raises(ValueError, match='Capture source changed'):
        validate_capture(capture, local=True)


def test_separate_channel_keeps_all_five_general_articles():
    general = ['주택임대차보호법-제'+str(i)+'조' for i in range(1,6)]
    civil = ['민법-제623조','민법-제626조']
    result = item_score({general[-1],civil[-1]}, general, general, civil)
    assert result['all_required'] == 1
    assert result['lost_required'] == []
    assert result['total_articles'] == 7


def test_missing_gold_is_not_automatic_success_or_irrelevance():
    result = item_score([], ['법-제1조'], ['법-제1조'], ['민법-제623조'])
    assert result['all_required'] is None
    assert result['civil_outside_required_gold'] == ['민법-제623조']
    assert 'irrelevant' not in result


def test_civil_selection_can_lose_previously_found_civil():
    result = item_score(['민법-제626조'], ['민법-제626조'], ['법-제1조'], ['민법-제623조'])
    assert result['all_required'] == 0
    assert result['lost_required'] == ['민법-제626조']


@pytest.mark.parametrize('general,civil', [
    (['민법-제623조'], []), ([], ['법-제1조']),
    (['법-제1조']*2, []), ([], ['민법-제1조']*3),
])
def test_bad_channels_rejected(general, civil):
    with pytest.raises(ValueError):
        join_channels(general, civil)


def test_published_capture_needs_manifest(tmp_path):
    capture = tmp_path/'capture'
    capture.mkdir()
    with pytest.raises(ValueError, match='requires bundle'):
        validate_capture(capture)


def test_removing_required_file_and_manifest_entry_is_rejected(tmp_path):
    capture = tmp_path/'capture'
    capture.mkdir()
    write(tmp_path/'bundle-manifest.json', {'schema':'patch018-bundle-v1',
          'files':{'capture/manifest.json':'fake'}})
    with pytest.raises(ValueError, match='Invalid bundle'):
        validate_capture(capture, local=True)


def test_local_capture_does_not_bypass_failed_audit(tmp_path):
    capture = tmp_path/'capture'
    capture.mkdir()
    write(capture/'manifest.json', {'operating_before':{}})
    write(capture/'audit.json', {'queries':235,'operating_unchanged':False})
    with pytest.raises(ValueError, match='Invalid capture audit'):
        validate_capture(capture, local=True)
