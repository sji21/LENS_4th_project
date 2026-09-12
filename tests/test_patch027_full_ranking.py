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


def test_frozen_comparison_replays_and_no_policy_passes_the_fixed_gate():
    from scripts.patch027_full_ranking import check_bundle
    result=check_bundle()
    assert not any(r['adoption']['passed'] for policies in result.values() for r in policies.values())
    assert min(r['top3_general_loss_inputs'] for r in result['general'].values())==8
    assert result['general']['guard1_pool']['groups']['question_only']['union_all_required']=={'hits':37,'n':75}


@pytest.fixture
def bundle(tmp_path):
    from shutil import copytree
    from scripts.patch027_full_ranking import BUNDLE
    copytree(BUNDLE,tmp_path/'bundle')
    return tmp_path/'bundle'


def refresh(bundle,trace=False):
    from scripts.patch015_baseline import read,write,sha
    if trace:
        audit=read(bundle/'audit.json');audit['traces_sha256']=sha(bundle/'traces.json')
        write(bundle/'audit.json',audit)
    manifest=read(bundle/'manifest.json')
    write(bundle/'manifest.json',{n:sha(bundle/n) for n in manifest})


def test_removed_file_and_manifest_entry_rejected(bundle):
    from scripts.patch015_baseline import read,write
    from scripts.patch027_full_ranking import check_bundle
    (bundle/'traces.json').unlink()
    manifest=read(bundle/'manifest.json');del manifest['traces.json'];write(bundle/'manifest.json',manifest)
    with pytest.raises(ValueError,match='Incomplete'):check_bundle(bundle)


@pytest.mark.parametrize('mutation',['missing_input','duplicate_input','wrong_partition','too_many_civil'])
def test_trace_semantics_rejected_even_when_hashes_are_updated(bundle,mutation):
    from scripts.patch015_baseline import read,write
    from scripts.patch027_full_ranking import check_bundle
    rows=read(bundle/'traces.json');audit=read(bundle/'audit.json')
    if mutation=='missing_input':rows.pop()
    if mutation=='duplicate_input':rows[-1]=rows[0]
    if mutation=='wrong_partition':rows[0]['extra_bm25']=[[audit['core_ids'][0],1.0]]
    if mutation=='too_many_civil':rows[0]['civil_seed']=audit['civil_ids'][:3]
    write(bundle/'traces.json',rows);refresh(bundle,trace=True)
    with pytest.raises(ValueError):check_bundle(bundle)


def test_false_capture_contract_rejected(bundle):
    from scripts.patch015_baseline import read,write
    from scripts.patch027_full_ranking import check_bundle
    audit=read(bundle/'audit.json');audit['candidate_unchanged']=False
    write(bundle/'audit.json',audit);refresh(bundle)
    with pytest.raises(ValueError,match='contract'):check_bundle(bundle)


def test_relabelled_success_result_cannot_pass_with_new_hash(bundle):
    from scripts.patch015_baseline import read,write
    from scripts.patch027_full_ranking import check_bundle
    result=read(bundle/'comparison.json');result['general']['stats_pool']['adoption']['passed']=True
    write(bundle/'comparison.json',result);refresh(bundle)
    with pytest.raises(ValueError,match='replay'):check_bundle(bundle)
