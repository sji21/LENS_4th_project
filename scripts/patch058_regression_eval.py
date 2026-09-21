"""Reuse frozen PATCH-057 measurements as a regression contract, with new coverage.

Only source availability/identity and the real statutory-form channel mapping change.
Questions, required labels and law/case budgets remain unchanged. A SHA seal records
uncommitted work without implicitly authorizing a Git commit.
"""
import argparse
import subprocess
from pathlib import Path

from scripts import patch057_comprehensive_eval as base

ROOT = Path(__file__).resolve().parents[1]
_case_chunks = base.case_profile_chunks
_source_identity = base.source_identity
_source_files = base.source_files
_serialize = base.serialized_result
_load_service = base.load_service
_metric_cutoffs = base.metric_cutoffs


def corpus_chunks(path):
    profile, chunks = _case_chunks(path)
    chunks += [c for c in base.base_chunks(ROOT / 'data')
               if c['metadata'].get('corpus_role') == 'case_supplement']
    return profile, chunks


def source_identity(code, source, article_ids, chunks):
    channel, identity, available = _source_identity(code, source, article_ids, chunks)
    if channel == 'forms':
        available = 'official-form:' + identity in article_ids
    return channel, identity, available


def source_files(*args):
    files = _source_files(*args)
    paths = [Path(__file__), *sorted((ROOT / 'src/ingestion').glob('*.py')),
             *base.all_files(ROOT / 'data/sources/retrieval-supplements-v1'),
             *base.all_files(ROOT / 'data/sources/supplement-v1')]
    for path in paths:
        files[str(path.resolve())] = {'sha256': base.sha(path), 'bytes': path.stat().st_size,
                                     'required_at_capture': True}
    return files


def serialize(service, result):
    value = _serialize(service, result)
    # Forms are delivered in the guide budget, never manufactured from a law's
    # cross-reference or an evaluation source code.
    value['forms'] = []
    for evidence in value['guides']:
        identity = evidence.get('article_id', '')
        if identity.startswith('official-form:'):
            owner = service.base_service or service
            chunk = owner._chunks[evidence['chunk_id']]
            if chunk['metadata'].get('guide_type') != 'official_form':
                raise ValueError('Form ID has no verified statutory-form metadata')
            value['forms'].append({**evidence, 'article_id': identity.removeprefix('official-form:')})
        else:
            # Retain guide positions so a form delivered second is not Hit@1.
            value['forms'].append({**evidence, 'article_id': ''})
    return value


def load_service(protocol):
    service, environment = _load_service(protocol)
    service.attach_base_service(service.base_service)
    environment['supplement_cases'] = sum(
        c['metadata'].get('corpus_role') == 'case_supplement' for c in service._chunks.values())
    environment['case_supplement_manifest'] = service.case_supplement_manifest
    return service, environment


def sealed_protocol(bundle, seal):
    if base.sha(bundle / 'manifest.json') != seal:
        raise ValueError('Pre-run protocol SHA does not match the supplied seal')
    base.verify_bundle(bundle)


def install_adapters():
    base.SCHEMA = 'patch058-retrieval-regression-v1'
    base.case_profile_chunks = corpus_chunks
    base.source_identity = source_identity
    base.source_files = source_files
    base.serialized_result = serialize
    base.load_service = load_service
    base.committed_protocol = sealed_protocol
    base.metric_cutoffs = lambda run, channel: ((1, 2) if channel == 'forms' else _metric_cutoffs(run, channel))
    for limits in base.RUN_BUDGETS.values():
        limits['forms'] = 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'capture', 'score'))
    parser.add_argument('--protocol', type=Path)
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--seal')
    parser.add_argument('--baseline-protocol', type=Path,
                        default=Path('C:/team_project/patch057-worktree/data/eval/patch057-comprehensive/protocol'))
    args = parser.parse_args()
    install_adapters()
    if args.command != 'score' and not args.protocol:
        parser.error('prepare and capture require --protocol')
    if args.command == 'prepare':
        baseline = args.baseline_protocol
        base.prepare(ROOT / 'data', ROOT / 'data/case_corpus/runtime-profile.json',
                     baseline / 'inputs/case-dev13.jsonl', baseline / 'inputs/case-external8.jsonl',
                     Path(base.read(baseline / 'protocol.json')['paths']['legacy_case_chunks']), args.protocol)
        current = base.read(args.protocol / 'jobs.json')
        old = base.read(baseline / 'jobs.json')
        def contract(job):
            return job['qid'], job['mode'], job['query'], {
                c: [t['label'] for t in ts] for c, ts in job['targets'].items()}
        if [contract(j) for j in current] != [contract(j) for j in old]:
            raise ValueError('Regression questions or expected labels changed')
        protocol = base.read(args.protocol / 'protocol.json')
        protocol['evaluation_status'] = 'regression_after_public_baseline_not_independent_holdout'
        protocol['form_scoring'] = 'Exact official-form identity projected at its original guide rank; shared guide budget 2'
        protocol['baseline_protocol_sha256'] = base.sha(baseline / 'manifest.json')
        protocol['code_state'] = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)
        base.write(args.protocol / 'protocol.json', protocol)
        manifest = base.read(args.protocol / 'manifest.json')
        manifest['protocol.json'] = base.sha(args.protocol / 'protocol.json')
        base.write(args.protocol / 'manifest.json', manifest)
        print('Protocol seal:', base.sha(args.protocol / 'manifest.json'))
    elif args.command == 'capture':
        if not args.seal or not args.out:
            parser.error('capture requires --seal and --out')
        base.capture(args.protocol, args.seal, args.out)
    else:
        if not args.out or not args.capture:
            parser.error('score requires --capture and --out')
        base.score(args.capture, args.out)


if __name__ == '__main__':
    main()
