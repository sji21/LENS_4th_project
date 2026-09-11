import pytest

from scripts.patch023_report import diagnose, summarize


def test_data_missing_and_held_retrieval_miss_are_not_conflated():
    r = diagnose(['law-a','law-b'], ['law-a'], [], [])
    assert r['category'] == 'data_missing_some'
    assert r['absent'] == ['law-b']
    assert r['held_not_returned'] == ['law-a']
    assert r['all_required_law5_civil3'] is False


def test_civil_is_not_hidden_after_general_top5():
    laws = [f'law-{n}' for n in range(5)]
    civil = ['민법-제626조']
    r = diagnose([laws[-1], civil[0]], laws+civil, laws, civil)
    assert r['category'] == 'all_required_returned'
    assert r['channel_metrics']['civil']['hit@1'] == 1
    assert r['channel_metrics']['general']['hit@3'] == 0
    assert r['channel_metrics']['general']['hit@5'] == 1


@pytest.mark.parametrize('targets,historical,category', [([],False,'no_fixed_target'),
    (['law-a'],True,'historical_review')])
def test_unscored_items_are_not_successes(targets,historical,category):
    r=diagnose(targets,['law-a'],['law-a'],[],historical)
    assert r['category']==category
    assert summarize([r])['union_all_required']=={'hits':0,'n':0}


@pytest.mark.parametrize('laws,civil', [(['민법-제626조'],[]),
    ([],['law-a']), (['law-a']*6,[])])
def test_channel_contract_is_checked(laws,civil):
    with pytest.raises(ValueError):
        diagnose([],['law-a','민법-제626조'],laws,civil)
