"""Capture DEV200 and reviewed CIV35 on snapshot indices; never generate answers."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PATCH015_CAPTURE_COMMIT = '2841e0444060b92cb54ece1d459b75b3b1f57e5b'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


def norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFC', value))


def score(targets, ranked, available, candidates):
    targets = {norm(t) for t in targets}
    ranked = [norm(t) for t in ranked]
    available = {norm(t) for t in available}
    candidates = {norm(t) for t in candidates}
    return {
        'targets': sorted(targets), 'absent': sorted(targets - available),
        'candidate_not_observed': sorted((targets & available) - candidates - set(ranked)),
        'candidate_present_not_top5': sorted((targets & candidates) - set(ranked[:5])),
        'metrics': {f'{name}@{k}': (int(bool(targets & set(ranked[:k]))) if name == 'AnyHit' else int(targets <= set(ranked[:k])) )
                    if targets else None for k in (3, 5) for name in ('AnyHit', 'AllRequired')},
    }


def require_patch015_capture_checkout(commit: str) -> None:
    """Reject capture when current retrieval code is outside PATCH-015's trace contract."""
    if commit != PATCH015_CAPTURE_COMMIT:
        raise ValueError(
            'PATCH-015 capture only supports the historical checkout '
            f'{PATCH015_CAPTURE_COMMIT}; current checkout is {commit}. '
            'Use that checkout for PATCH-015 replay or a version-specific evaluator for newer retrieval code.'
        )


def capture(out):
    current_commit = subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, encoding='utf-8',
    ).strip()
    require_patch015_capture_checkout(current_commit)
    from scripts.dev100_v2.report import load_dataset
    devplan, old = load_dataset(ROOT / 'data/eval/dev100-v2')
    civilbase = ROOT / 'data/eval/civil-review2'
    for f in read(civilbase / 'manifest.json')['files']:
        if sha(civilbase / f['path']) != f['stored_sha256']:
            raise ValueError('Civil bundle hash mismatch: ' + f['path'])
    civil = read(civilbase / 'retrieval-plan.json')
    dev = read(ROOT / 'data/eval/dev100-v2/questions.json')
    jobs = [(q['qid'], mode, value['query'], 'dev200') for q in dev for mode, value in q['modes'].items()]
    jobs += [(q['id'], 'question_context', q['input_text'], 'civil35') for q in civil['items']]
    if len(jobs) != 235 or len({(a,b) for a,b,_,_ in jobs}) != 235:
        raise ValueError('Expected 235 unique inputs')
    out.mkdir(parents=True, exist_ok=False)
    indexes = ['chroma_kurev1_1024', 'chroma_civil_kurev1_1024']
    files = [ROOT/'data/database/knowledge.sqlite3']
    files += [ROOT/'data/chunks'/f'{n}.jsonl' for n in ('chunks','cases','guides','civil')]
    for name in indexes:
        if not (ROOT/'data/index'/name/'chroma.sqlite3').is_file():
            raise ValueError('Missing index: ' + name)
        files += [p for p in (ROOT/'data/index'/name).rglob('*') if p.is_file()]
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in files}
    snapshot = out/'snapshot'
    for p in files:
        dest = snapshot/p.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if p.name == 'knowledge.sqlite3':
            with sqlite3.connect(p.as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(dest) as dst:
                src.backup(dst)
        else:
            shutil.copy2(p, dest)
    snapshot_before = {p.relative_to(snapshot).as_posix(): sha(p) for p in snapshot.rglob('*') if p.is_file()}
    from src.retrieval.service import RetrievalService, LAW_TYPES, DEFAULT_MODEL
    from src.retrieval.retriever import load_chunks
    from src.retrieval.index import clean_metadata
    from src.evaluation.baseline import settings

    class TracedService(RetrievalService):
        def _search_one(self, corpus, question, k):
            hits = super()._search_one(corpus, question, k)
            retriever = self._retrievers.get(corpus.name)
            if retriever is not None and k > 0:
                self.trace.append({'corpus': corpus.name, 'where': corpus.where(), 'k': k,
                                   'members': retriever.last_member_hits(),
                                   'selected': [h.chunk_id for h in hits]})
            return hits

    paths = tuple(snapshot/'data/chunks'/f'{n}.jsonl' for n in ('chunks','cases','guides'))
    chunks = [c for p in paths for c in load_chunks(p)]
    byid = {c['chunk_id']: c for c in chunks}
    svc = TracedService.from_index(chunk_paths=paths, index_path=snapshot/'data/index'/indexes[0], civil_index_path=snapshot/'data/index'/indexes[1])
    seen = []
    for retriever in (svc.dense, svc.civil_dense):
        data = retriever.collection.get(include=['documents','metadatas'])
        for cid, text, meta in zip(data['ids'], data['documents'], data['metadatas']):
            if text != byid[cid]['text'] or meta != clean_metadata(byid[cid]['metadata']):
                raise ValueError('Index/chunk mismatch: ' + cid)
            seen.append(cid)
    if len(seen) != len(set(seen)) or set(seen) != set(byid):
        raise ValueError('Index coverage mismatch')
    inventory = []
    for c in chunks:
        m = c['metadata']
        if m['doc_type'] in LAW_TYPES:
            inventory.append({'chunk_id': c['chunk_id'], 'article_anchor': norm(m['article_id']),
                              'title': m['title'], 'article_no': m['article_no'],
                              'body_sha256': hashlib.sha256(c['text'].encode()).hexdigest(),
                              'effective_date': m.get('effective_date'), 'version': m.get('version')})
    with sqlite3.connect(snapshot/'data/database/knowledge.sqlite3') as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('DB integrity failure')
        dbanchors = {norm(n+'-'+a) for n,a in db.execute('SELECT l.law_name,a.article_number FROM law_articles a JOIN law_versions v USING(law_version_id) JOIN laws l USING(law_id)')}
    if dbanchors != {r['article_anchor'] for r in inventory}:
        raise ValueError('DB/chunk article coverage mismatch')
    git = lambda *args: subprocess.check_output(['git',*args], cwd=ROOT, encoding='utf-8').strip()
    artifacts = [p for folder in ('data/eval/dev100-v2','data/eval/civil-review2') for p in (ROOT/folder).rglob('*') if p.is_file()]
    cache = Path.home()/'.cache/huggingface/hub/models--nlpai-lab--KURE-v1'
    model_files = {p.relative_to(cache).as_posix(): sha(p) for p in (cache/'snapshots').rglob('*') if p.is_file()}
    manifest = {'schema':'patch015-run-v1','started_at': datetime.now(timezone.utc).isoformat(),
                'commit':git('rev-parse','HEAD'),'branch':git('branch','--show-current'), 'status':git('status','--porcelain'),
                'python':platform.python_version(),'platform':platform.platform(), 'model':DEFAULT_MODEL,
                'model_files':model_files, 'packages':{n:importlib.metadata.version(n) for n in ('torch','transformers','sentence-transformers','chromadb')},
                'settings':settings(svc),'operating_hashes_before':before,'snapshot_hashes_before':snapshot_before,
                'code_hashes':{p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'src/retrieval').glob('*.py')},
                'runner_sha256':sha(__file__), 'dataset_hashes':{p.relative_to(ROOT).as_posix():sha(p) for p in artifacts},
                'generation_performed':False,'search_k':{'law':5,'case':5,'guide':2}}
    write(out/'manifest.json',manifest)
    write(out/'inventory.json',inventory)
    anchor = {r['chunk_id']:r['article_anchor'] for r in inventory}
    results = []
    for qid, mode, query, group in jobs:
        svc.trace = []
        start = time.perf_counter()
        raw = svc.search(query, k_law=5, k_case=5, k_guide=2)
        row = {'qid':qid,'mode':mode,'group':group,'query':query,'query_sha256':hashlib.sha256(query.encode()).hexdigest(),
               'seconds':time.perf_counter()-start,'civil_topics':raw.civil_topics,'trace':svc.trace}
        for kind in ('laws','cases','guides'):
            row[kind] = []
            for h in getattr(raw,kind):
                value = asdict(h)
                value['body_sha256'] = hashlib.sha256(value.pop('text').encode()).hexdigest()
                if kind == 'laws': value['article_anchor'] = anchor[h.chunk_id]
                row[kind].append(value)
        results.append(row)
        with (out/'progress.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(row,ensure_ascii=False)+'\n')
        if len(results)%10 == 0: print(f'{len(results)}/235',flush=True)
    after = {p.relative_to(ROOT).as_posix():sha(p) for p in files}
    if before != after: raise ValueError('Operating data changed during run')
    if manifest['dataset_hashes'] != {p.relative_to(ROOT).as_posix():sha(p) for p in artifacts}:
        raise ValueError('Evaluation criteria changed during run')
    write(out/'results.json',results)
    write(out/'audit.json',{'queries':len(results),'index_records':len(seen),'law_articles':len(inventory),
                           'operating_hashes_after':after,'operating_unchanged':True,'dataset_unchanged':True,
                           'completed_at':datetime.now(timezone.utc).isoformat()})
    print('CAPTURE COMPLETE',flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(ROOT/'tmp') or out.exists():
        parser.error('Use a new directory under repository tmp/')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', ANONYMIZED_TELEMETRY='False', LANGSMITH_TRACING='false')
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    capture(out)


if __name__ == '__main__':
    main()
