from src.evaluation.case_internal import capacity,score


def test_one_case_can_support_multiple_independent_groups():
    assert capacity([{'a','b'},{'a','c'},{'d'}])['max_supported_by_two']==3
    assert capacity([{'a'},{'b'},{'c'}])['max_supported_by_two']==2


def test_corpus_absence_errors_and_duplicates_are_not_dropped():
    items=[{'qid':'a','groups':[{'available_keys':['x']},{'available_keys':[]}]},
           {'qid':'b','groups':[{'available_keys':['y']}]}]
    results=[{'qid':'a','cases':[{'canonical_case_key':'x'},{'canonical_case_key':'x'}]},
             {'qid':'b','error':{'type':'TimeoutError'},'cases':[]}]
    rows,summary=score(items,results)
    assert summary['E']==2 and summary['G']==3 and summary['C']==2
    assert summary['hit_at_2']==.5 and summary['macro_group_recall_at_2']==.5
    assert summary['overall_micro_group_recall']==1/3
    assert summary['duplicates']==1 and summary['errors']==1


def test_third_result_cannot_count_as_top2():
    items=[{'qid':'q','groups':[{'available_keys':['gold']}]}]
    results=[{'qid':'q','cases':[{'canonical_case_key':k} for k in ('a','b','gold')]}]
    rows,summary=score(items,results)
    assert summary['hit_at_2']==0 and summary['overflows']==1
