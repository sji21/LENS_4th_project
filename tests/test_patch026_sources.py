import pytest
from scripts.patch026_sources import ROOT, SPECS, parse_page
from scripts.patch015_baseline import read


@pytest.mark.parametrize('spec',SPECS)
def test_official_sources_are_single_versioned_articles(spec):
    directory=ROOT/'data/eval/patch026-expansion'
    source=next(s for s in read(directory/'source-manifest.json') if s['source_id']==spec[0])
    path=directory/'sources'/f'{spec[0]}.html'
    record=parse_page(path.read_text(encoding='utf-8'),spec,source['url'],path.relative_to(ROOT).as_posix())
    assert not record.validate()
    assert record.article_number==spec[2]
    assert '①' in record.content
    assert record.source_text==''  # Article pages must not claim complete law/addenda snapshots.


def test_wrong_version_and_law_are_rejected():
    spec=SPECS[0]; source=(ROOT/'data/eval/patch026-expansion/sources/RR16.html').read_text(encoding='utf-8')
    with pytest.raises(ValueError,match='version'):
        parse_page(source.replace('20677','99999'),spec,'https://www.law.go.kr','test')
    with pytest.raises(ValueError,match='title'):
        parse_page(source.replace('주민등록법','가상법'),spec,'https://www.law.go.kr','test')
