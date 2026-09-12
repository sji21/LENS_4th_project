import pytest

from scripts.patch027_full_ranking import GENERAL,CIVIL_POLICIES,general_select,civil_select


def row():
    return {'core':[('a',.31),('b',.29),('c',.27)],'extra':[('x',.32),('y',.30),('z',.28)],
            'core_bm25':[('a',10.),('b',8.),('c',6.)], 'extra_bm25':[('x',9.),('y',7.),('z',5.)],
            'global_dense':[('x',.9),('y',.8),('a',.7),('b',.6),('z',.5),('c',.4)],
            'current_laws':['a','b','c'], 'current_civil':['old','one','tail'],
            'civil_seed':['old'],'civil_bm25':['one','old','three','tail'],
            'civil_dense':['three','one','tail','old']}


@pytest.mark.parametrize('policy',GENERAL)
def test_general_policies_are_bounded_unique_and_preserve_requested_prefix(policy):
    r=row();result=general_select(r,policy)
    assert len(result)<=5 and len(set(result))==len(result)
    if policy.startswith('guard1'):assert result[0]=='a'
    if policy.startswith('guard2'):assert result[:2]==['a','b']
    if policy=='full_current':assert result==r['current_laws']


@pytest.mark.parametrize('policy',CIVIL_POLICIES)
def test_civil_policies_have_no_extra_slot_or_duplicate(policy):
    r=row();result=civil_select(r,policy)
    assert len(result)==3 and len(set(result))==3
    if policy.startswith('seed_'):assert result[0]=='old'
    if policy=='current':assert result==r['current_civil']


def test_unknown_policy_is_not_silently_accepted():
    with pytest.raises(ValueError):general_select(row(),'typo')
    with pytest.raises(ValueError):civil_select(row(),'typo')
