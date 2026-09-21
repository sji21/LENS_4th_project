from types import SimpleNamespace

from scripts import patch058_regression_eval as evaluation


def test_form_scoring_preserves_guide_rank_and_rejects_cross_reference(monkeypatch):
    form = {'chunk_id': 'official-form:example#0', 'article_id': 'official-form:규칙-별지제4호서식'}
    guide = {'chunk_id': 'guide#0', 'article_id': 'guide', 'text': '별지 제4호서식을 제출한다'}
    monkeypatch.setattr(evaluation, '_serialize', lambda *_: {'guides': [guide, form]})
    owner = SimpleNamespace(_chunks={form['chunk_id']: {'metadata': {'guide_type': 'official_form'}}})
    service = SimpleNamespace(base_service=owner)
    projected = evaluation.serialize(service, None)['forms']
    assert [e['article_id'] for e in projected] == ['', '규칙-별지제4호서식']


def test_form_availability_requires_actual_form_identity():
    source = {'title': '공공주택 특별법 시행규칙 별지 제4호서식'}
    assert evaluation.source_identity('Iform4', source, {'공공주택특별법시행규칙-제28조'}, [])[2] is False
    assert evaluation.source_identity('Iform4', source, {
        'official-form:공공주택특별법시행규칙-별지제4호서식'}, [])[2] is True
