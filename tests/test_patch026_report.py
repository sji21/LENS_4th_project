import json
import shutil
import pytest
from scripts.patch026_report import BUNDLE, summarize


def test_all_three_candidates_replay_without_operating_adoption():
    result=summarize()
    assert not result['operating_adopted']
    assert {k:len(v['losses']) for k,v in result['variants'].items()}=={'full':6,'tax':4,'residence':1}
    assert result['variants']['full']['groups']['question_only']['union_all_required']['hits']==34


def test_missing_source_and_manifest_entry_are_rejected(tmp_path):
    target=tmp_path/'bundle'; shutil.copytree(BUNDLE,target)
    manifest=json.loads((target/'bundle-manifest.json').read_text(encoding='utf-8'))
    del manifest['sources/RR16.html']
    (target/'sources/RR16.html').unlink()
    (target/'bundle-manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(ValueError,match='Incomplete bundle'): summarize(target)
