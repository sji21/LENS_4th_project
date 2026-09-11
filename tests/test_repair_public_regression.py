import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.dev100_diagnostics import repair_public_regression as repair


def test_cli_writes_mixed_public_regression_without_legacy_metric_assert(tmp_path, monkeypatch):
    rows = repair.load_dataset(Path('data/eval/minbeop_review_holdout_20260901.jsonl'), 'law')
    answers = {r['question']: r['gold_articles'] for r in rows}
    ids = {a for values in answers.values() for a in values}
    chunks = [{'chunk_id': a, 'text': a, 'metadata': {'doc_type': 'law', 'article_id': a}} for a in ids]
    class Service:
        def search(self, query, **kwargs):
            return SimpleNamespace(
                laws=[SimpleNamespace(chunk_id=a) for a in answers[query] if not a.startswith('민법-')],
                civil_laws=[SimpleNamespace(chunk_id=a) for a in answers[query] if a.startswith('민법-')],
                guides=[])
    datasets = {'dev': rows[:1], 'holdout': rows[:1], 'civil_published_regression': rows}
    baseline = {}
    for split, dataset in datasets.items():
        old = {'metrics': {'hit@5': 0}, 'questions': [
            {'qid': r['qid'], 'gold': r['gold_articles'], 'retrieved_ids': r['gold_articles']}
            for r in dataset if r['gold_articles']]}
        baseline[split] = old if split == 'civil_published_regression' else {'after': old}
    source = tmp_path / 'old.json'
    source.write_text(json.dumps({'results': baseline}), encoding='utf-8')
    monkeypatch.setattr(repair.RetrievalService, 'from_index', lambda **kwargs: Service())
    monkeypatch.setattr(repair, 'load_chunks', lambda p: chunks if p.name == 'chunks.jsonl' else [])
    monkeypatch.setattr(repair, 'load_dataset', lambda p, kind: datasets[
        'civil_published_regression' if p.name.startswith('minbeop') else p.stem])
    monkeypatch.setattr(repair, 'settings', lambda service: {'search_k': {'k_civil': 3}})
    repair.main(['--run-dir', str(tmp_path), '--baseline-report', str(source)])
    result = json.loads((tmp_path / 'public-regression.json').read_text(encoding='utf-8'))['results']
    civil = result['civil_published_regression']
    assert civil['n'] == 15
    assert civil['metrics']['all@law5+civil3'] == 1
    assert len(civil['excluded']) == 5
    assert not civil['comparison']['metrics_comparable']
    assert 'metrics_equal' not in civil['comparison']
    assert not civil['comparison']['has_required_evidence_loss']
    assert result['dev']['scope'] == result['holdout']['scope'] == 'general'
    with pytest.raises(SystemExit):
        repair.main(['--run-dir', str(tmp_path), '--baseline-report', str(source)])


def report():
    return {'scope': 'combined', 'ranking': 'union', 'metrics': {'hit': 1},
            'questions': [{'qid': 'q', 'gold': ['a'], 'retrieved_ids': ['a'],
                           'channel_retrieved_ids': {'general': ['a'], 'civil': []}}]}


def test_same_protocol_detects_loss_and_metric_change():
    old, new = report(), report()
    new['questions'][0]['retrieved_ids'] = []
    new['metrics']['hit'] = 0
    result = repair.compare_reports(new, old)
    assert result['metrics_comparable']
    assert result['has_required_evidence_loss']
    assert not result['metrics_equal']
    assert not result['rankings_equal']


@pytest.mark.parametrize('field,value', [('qid', 'other'), ('gold', ['b'])])
def test_different_questions_or_gold_cannot_be_compared(field, value):
    old, new = report(), copy.deepcopy(report())
    new['questions'][0][field] = value
    with pytest.raises(ValueError):
        repair.compare_reports(new, old)
