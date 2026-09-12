import pytest

from scripts.patch024_expand import ADDED, records_from_source
from src.retrieval.service import CIVIL


def source():
    numbers = [a.split('-')[1] for a in CIVIL.include_ids] + list(ADDED)
    return ('민법\n[시행 2026. 3. 17.] [법률 제21454호, 2026. 3. 17., 일부개정]\n'
            + '\n'.join(f'{n}(검증 제목) ① 검증 본문' for n in numbers))


def test_expansion_preserves_seven_and_selects_only_three():
    records = records_from_source(source())
    assert len(records) == 10
    assert {r.article_number for r in records} == {a.split('-')[1] for a in CIVIL.include_ids} | set(ADDED)
    assert all(not r.validate() for r in records)
    assert sum(bool(r.source_text) for r in records) == 1
    assert all('lsiSeq=284415' in r.source_url for r in records)


def test_wrong_version_is_rejected():
    with pytest.raises(ValueError):
        records_from_source(source().replace('21454', '99999'))


def test_missing_selected_article_is_rejected():
    with pytest.raises(ValueError):
        records_from_source(source().replace('제357조', '제358조'))
