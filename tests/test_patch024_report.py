import json
import shutil

import pytest

from scripts.patch024_report import BUNDLE, verify


def test_published_results_replay():
    report = verify()
    assert report['losses'] == []
    assert report['groups']['question_only']['union_all_required'] == {'hits':28, 'n':75}
    assert report['groups']['context_diagnostic']['union_all_required'] == {'hits':29, 'n':75}


def test_missing_file_and_manifest_entry_are_rejected(tmp_path):
    bundle = tmp_path/'bundle'
    shutil.copytree(BUNDLE, bundle)
    (bundle/'audit.json').unlink()
    manifest = json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    del manifest['files']['audit.json']
    (bundle/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='Incomplete evidence'):
        verify(bundle)


def test_missing_baseline_manifest_entry_is_rejected(tmp_path):
    bundle = tmp_path/'bundle'
    shutil.copytree(BUNDLE, bundle)
    manifest = json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    manifest['baseline_files'].popitem()
    (bundle/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='Incomplete baseline'):
        verify(bundle)
