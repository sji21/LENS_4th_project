"""Validate review drafts and optionally observe existing topic rules, without scoring."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DEFAULT = Path('data/eval/civil-selection-review-draft.json')


def validate(payload):
    roles = {'direct_issue', 'conditional_issue', 'not_auto_priority', 'review_needed'}
    if payload['independent'] or payload['legal_review_status'] != 'pending':
        raise ValueError('This utility is only for pending development drafts')
    rows = payload['items']
    if not rows or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Empty or duplicate draft IDs')
    for r in rows:
        if r['legal_review_status'] != 'pending' or r['expected_final_rank'] is not None or r['absolute_retrieval_ban']:
            raise ValueError('Draft must not imply a final ranking or absolute exclusion gold')
        if r['proposed_retrieval_role'] not in roles:
            raise ValueError('Unknown proposed role')
        if not r['annotation']['request_span'] or r['annotation']['request_span'] not in r['question']:
            raise ValueError('Request evidence must appear in question')
        if not r['annotation']['entity_span'] or r['annotation']['entity_span'] not in r['context'] + '\n' + r['question']:
            raise ValueError('Entity evidence must appear in input')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', type=Path, default=DEFAULT)
    parser.add_argument('--out', type=Path, help='Optional new rule-observation output; never scored')
    args = parser.parse_args()
    if args.out and args.out.exists():
        parser.error('Use a new output file')
    payload = json.loads(args.draft.read_text(encoding='utf-8'))
    rows = validate(payload)
    if args.out:
        from src.retrieval.service import detect_civil_topics
        results = []
        for r in rows:
            query = r['context'] + '\n사용자 질문: ' + r['question']
            results.append({'id': r['id'], 'query': query,
                            'existing_rule_topics': [t.article_id for t in detect_civil_topics(query)]})
        report = {'purpose': 'rule observation only; no correctness or retrieval score',
                  'independent': False, 'gold_review_status': 'pending',
                  'draft_sha256': hashlib.sha256(args.draft.read_bytes()).hexdigest(),
                  'service_sha256': hashlib.sha256(Path('src/retrieval/service.py').read_bytes()).hexdigest(),
                  'retrieval_executed': False, 'results': results}
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'drafts': len(rows), 'valid': True, 'score_computed': False}))


if __name__ == '__main__':
    main()
