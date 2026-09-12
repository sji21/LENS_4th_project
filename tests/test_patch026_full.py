from copy import deepcopy

import pytest

from scripts.patch015_baseline import ROOT, read
from scripts.patch026_full_sources import OUT, compile_records, parse_verified
from scripts.patch026_full_eval import rank_changes


def test_all_reviewed_current_sources_compile_with_exact_versions():
    records=compile_records()
    assert len(records)==56
    assert sum(r.law_name=='민법' for r in records)==16
    assert all(r.effective_from<='2026-09-08' for r in records)
    proc=[r for r in records if r.law_name=='민사소송법']
    assert {r.proclamation_number for r in proc}=={'법률 제19516호'}
    date=[r.article_number for r in records if r.document_type=='rule' and r.law_name.startswith('주택임대차계약증서')]
    assert set(date)=={'제2조','제3조','제6조','제7조','제9조','제10조','제11조','제12조'}


@pytest.mark.parametrize('field,value',[
    ('law_name','형법'),('article_number','제1110조'),
    ('effective_from','2028-03-01'),('proclamation_number','법률 제1호'),
    ('source_version_id','999999')])
def test_wrong_identity_or_version_rejected(field,value):
    spec=next(s for s in read(OUT/'specs.json') if s['source_id']=='C111')
    raw=(ROOT/spec['path']).read_text(encoding='utf-8')
    spec=deepcopy(spec);spec[field]=value
    with pytest.raises(ValueError): parse_verified(raw,spec)


def test_future_original_procedure_page_not_accepted_as_current():
    spec=next(s for s in read(OUT/'specs.json') if s['source_id']=='PROC194')
    raw=(OUT/'sources/PROC194.html').read_text(encoding='utf-8')
    with pytest.raises(ValueError,match='Version mismatch'):parse_verified(raw,spec)


def test_multiple_article_page_selects_only_exact_article():
    specs=[s for s in read(OUT/'specs.json') if s['source_id'] in ('PUBLIC492','PUBLIC493')]
    records=[parse_verified((ROOT/s['path']).read_text(encoding='utf-8'),s) for s in specs]
    assert {r.article_number for r in records}=={'제49조의2','제49조의3'}
    assert all('제49조의4(' not in r.content for r in records)


def test_top3_loss_not_hidden_by_unchanged_top5():
    old={'qid':'Q','mode':'question_only','laws':['a','b','gold','c','d'],'civil_laws':['civil']}
    new={**old,'laws':['a','b','c','gold','d']}
    details=[{'qid':'Q','mode':'question_only','targets':['gold'],'category':'all_required_returned'}]
    result=rank_changes([old],[new],details)
    assert result['laws']['3'][0]['lost']==['gold']
    assert result['laws']['5']==[]


def test_partial_version_addition_preserves_existing_article_and_source(tmp_path):
    from src.database.relational import initialize_relational_database,connect_database
    from src.ingestion.load_laws import load_records
    from scripts.patch026_full_eval import retain_affected_records
    records=[r for r in compile_records() if r.law_name=='민법'][:2]
    path=tmp_path/'law.sqlite3';initialize_relational_database(path)
    with connect_database(path) as db:
        load_records(records[:1],db)
        old=[tuple(r) for r in db.execute('SELECT * FROM law_articles')]
        oldsources=[tuple(r) for r in db.execute('SELECT * FROM law_article_sources')]
        load_records(retain_affected_records(db,records[1:]),db)
        assert len(db.execute('SELECT * FROM law_articles').fetchall())==2
        assert old[0] in [tuple(r) for r in db.execute('SELECT * FROM law_articles')]
        assert oldsources[0] in [tuple(r) for r in db.execute('SELECT * FROM law_article_sources')]
        with pytest.raises(ValueError,match='replace an existing'):
            retain_affected_records(db,records[:1])
