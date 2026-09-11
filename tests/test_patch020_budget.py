import pytest
from scripts.patch020_budget import fill_candidates, evaluate
from scripts.patch015_baseline import write, sha


def test_existing_picks_survive_even_when_last_in_ranking():
    assert fill_candidates(['F','G'],list('ABCDEFG'),3)==['F','G','A']


def test_candidates_grow_without_duplicates():
    old=[]
    for budget in (2,3,4,5,6,7):
        result=fill_candidates(['B'],list('ABCDEFG'),budget)
        assert result[:len(old)]==old
        assert len(result)==len(set(result))==budget
        old=result


def test_missing_existing_candidate_rejected():
    with pytest.raises(ValueError):fill_candidates(['X'],list('ABCDEFG'),2)


def test_missing_corpus_target_stays_in_full_denominator():
    result=evaluate(['civil','missing'],['civil'],['general'],['civil'],{'civil'})
    assert result['all_required']==0
    assert result['held_civil_all']==1


def test_no_gold_does_not_mean_success_or_irrelevance():
    result=evaluate([],[],[],['civil'],{'civil'})
    assert result['held_civil_all'] is None
    assert result['all_required'] is None


def test_lost_civil_evidence_detected():
    result=evaluate(['civil'],['civil'],['general'],['other'],{'civil','other'})
    assert result['lost_required']==['civil']


def test_published_bundle_rejects_removed_file_and_entry(tmp_path, monkeypatch):
    import scripts.patch020_budget as module
    monkeypatch.setattr(module,'BASE',tmp_path)
    write(tmp_path/'bundle-manifest.json',{'schema':'patch020-bundle-v1','files':{}})
    with pytest.raises(ValueError,match='Incomplete'):module.check_bundle()


def test_published_bundle_rejects_changed_results(tmp_path, monkeypatch):
    import scripts.patch020_budget as module
    monkeypatch.setattr(module,'BASE',tmp_path)
    files={}
    for rel in ('plan.json','results/summary.json','results/details.json','results/manifest.json'):
        p=tmp_path/rel;p.parent.mkdir(exist_ok=True);write(p,{})
        files[rel]=sha(p)
    write(tmp_path/'bundle-manifest.json',{'schema':'patch020-bundle-v1','files':files})
    module.check_bundle()
    write(tmp_path/'results/summary.json',{'changed':True})
    with pytest.raises(ValueError,match='hash/path'):module.check_bundle()
