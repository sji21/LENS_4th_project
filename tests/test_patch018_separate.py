"""Channel separation preserves evidence without treating missing gold as rejection."""
import pytest

from scripts.patch015_baseline import write
from scripts.patch018_separate import item_score, join_channels, validate_capture


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
