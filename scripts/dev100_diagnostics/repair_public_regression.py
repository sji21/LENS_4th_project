"""Diagnose public regression without equating legacy and split-channel ranks."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.evaluation.baseline import evaluate, load_dataset, prepare_questions, settings
from src.retrieval.retriever import load_chunks
from src.retrieval.service import RetrievalService


def compare_reports(current, previous):
    def keyed(rows):
        result = {r['qid']: r for r in rows}
        if len(result) != len(rows):
            raise ValueError('Duplicate comparison qid')
        return result
    new, old = keyed(current['questions']), keyed(previous['questions'])
    if new.keys() != old.keys():
        raise ValueError('Comparison question IDs differ')
    for qid in new:
        if set(new[qid]['gold']) != set(old[qid]['gold']):
            raise ValueError('Comparison gold differs: ' + qid)
    comparable = (current.get('scope') == previous.get('scope')
                  and current.get('ranking') == previous.get('ranking'))
    changes = []
    for qid, row in new.items():
        before, after = set(old[qid]['retrieved_ids']), set(row['retrieved_ids'])
        gold = set(row['gold'])
        changes.append({'qid': qid, 'lost_gold': sorted((before - after) & gold),
                        'gained_gold': sorted((after - before) & gold)})
    result = {'metrics_comparable': comparable, 'required_evidence_changes': changes,
              'has_required_evidence_loss': any(r['lost_gold'] for r in changes)}
    if comparable:
        result.update(metrics_equal=current['metrics'] == previous['metrics'],
                      rankings_equal=all(
                          new[q]['retrieved_ids'] == old[q]['retrieved_ids']
                          and new[q].get('channel_retrieved_ids') == old[q].get('channel_retrieved_ids')
                          for q in new))
    else:
        result['reason'] = 'Different or unspecified scope/ranking; legacy metrics are not an expected value'
    return result


def diagnose(service, chunks, baseline, datasets):
    results = {}
    for split, rows in datasets.items():
        rows = [{**r, 'answer_type': 'unanswerable'} if r.get('answer_type') == 'abstain'
                else r for r in rows]
        selected, excluded = prepare_questions(rows, chunks, 'law')
        scope = 'combined' if split == 'civil_published_regression' else 'general'
        result = evaluate(service, selected, chunks, 'law', law_scope=scope)
        old = baseline[split] if split == 'civil_published_regression' else baseline[split]['after']
        result.update(excluded=excluded, comparison=compare_reports(result, old))
        results[split] = result
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--baseline-report', required=True, type=Path)
    args = parser.parse_args(argv)
    target = args.run_dir / 'public-regression.json'
    if target.exists():
        parser.error('Output already exists; use a new run directory')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      ANONYMIZED_TELEMETRY='False', LANGSMITH_TRACING='false')
    snapshot = args.run_dir / 'snapshot'
    paths = tuple(snapshot / 'data/chunks' / f'{n}.jsonl' for n in ('chunks', 'cases', 'guides'))
    service = RetrievalService.from_index(chunk_paths=paths,
        index_path=snapshot / 'data/index/chroma_kurev1_1024',
        civil_index_path=snapshot / 'data/index/chroma_civil_kurev1_1024')
    chunks = [c for p in paths for c in load_chunks(p)]
    baseline = json.loads(args.baseline_report.read_text(encoding='utf-8'))['results']
    datasets = {split: load_dataset(Path('data/eval') / filename, 'law') for split, filename in (
        ('dev', 'dev.jsonl'), ('holdout', 'holdout.jsonl'),
        ('civil_published_regression', 'minbeop_review_holdout_20260901.jsonl'))}
    results = diagnose(service, chunks, baseline, datasets)
    with target.open('x', encoding='utf-8') as output:
        json.dump({'purpose': 'public regression diagnostics; not a legacy-metric equality gate',
                   'settings': settings(service), 'results': results}, output, ensure_ascii=False, indent=2)
        output.write('\n')
    print(json.dumps({split: r['comparison'] for split, r in results.items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
