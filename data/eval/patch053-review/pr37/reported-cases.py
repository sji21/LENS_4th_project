"""Compare PR37 request-gate fixes using the unchanged official BM25 corpus."""
import hashlib
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from src.retrieval import expanded
from src.retrieval.retrieval_intent import LawIntentSelector, requested_law_intents
from src.retrieval.retriever import load_chunks
from src.retrieval.service import route_law_corpus

original = types.ModuleType('original_pr37_intent')
source = subprocess.check_output([
    'git', '-c', 'safe.directory=' + ROOT.as_posix(), 'show',
    '0b202fa:src/retrieval/retrieval_intent.py',
], cwd=ROOT).decode('utf-8')
exec(compile(source, '0b202fa:retrieval_intent.py', 'exec'), original.__dict__)
path = Path(sys.argv[1])
chunks = load_chunks(path)
service = expanded.ExpandedLawRetrievalService(chunks)
by_id = {c['chunk_id']: c['metadata']['article_id'] for c in chunks}
questions = [
    '등록민간임대주택에서 전입신고에 필요한 서류를 알려주세요.',
    '전입신고를 하지 않았는데 계약을 해지할 수 있나요?',
]
rows = []
for query in questions:
    corpus = route_law_corpus(query, service.corpora[0])
    raw = service._context_law.search(query, 3, corpus.where())
    service._law_intent_selector = original.LawIntentSelector(chunks)
    with patch.object(expanded, 'requested_law_intents', original.requested_law_intents):
        before = service._search_one(corpus, query, 3)
    service._law_intent_selector = LawIntentSelector(chunks)
    after = service._search_one(corpus, query, 3)
    assert [(e.chunk_id, e.score) for e in after] == [(cid, round(score, 4)) for cid, score in raw]
    rows.append({
        'query': query, 'before_intents': original.requested_law_intents(query),
        'after_intents': requested_law_intents(query),
        'raw_k3': [by_id[cid] for cid, _ in raw],
        'before_k3': [by_id[e.chunk_id] for e in before],
        'after_k3': [by_id[e.chunk_id] for e in after],
    })
result = {'baseline_commit': '0b202fa', 'corpus': str(path),
          'corpus_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
          'retrieval': 'actual BM25 only; no KURE, LLM, HTTP or DB/index writes', 'rows': rows}
Path(sys.argv[2]).write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
