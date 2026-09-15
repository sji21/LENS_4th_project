"""Load and verify the applied case backend through the existing profile entry."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
from threading import local

from src.retrieval.case_profile import file_hash, index_content_hash
from src.retrieval.case_internal import CaseInternalPolicy, CaseInternalRetriever, LocalCaseCrossEncoder
from src.retrieval.service import RetrievalService, CASE


class AppliedCaseInternalService(RetrievalService):
    def __init__(self, backend, profile, base_service=None):
        # Reuse the existing result/citation/search contracts. Other corpus
        # implementations remain inherited and are not changed by this backend.
        super().__init__([], dense=None)
        self._chunks = backend.chunks
        self.case_backend = backend
        self.case_profile = profile
        self._request_trace = local()
        self._retrievers[CASE.name] = backend.hybrid
        self.base_service = base_service

    @staticmethod
    def _warn_if_civil_missing(civil, civil_chunks):
        # Readiness of another corpus is outside this case-only loader. An
        # attached base service retains its own existing readiness behaviour.
        return None

    def search(self, question, k_law=5, k_case=None, k_guide=2, *, k_civil=None):
        # The preceding sealed case profile defaults to return_k=20. Explicit
        # caller K, including the application's top2 request, remains authoritative.
        k_case=self.case_profile.get('public_default_case_k',20) if k_case is None else k_case
        if self.base_service is None:
            return super().search(question,k_law,k_case,k_guide,k_civil=k_civil)
        # An already configured application service owns every other channel.
        # Preserve its result unchanged except for the explicitly requested cases.
        result=self.base_service.search(question,k_law=k_law,k_case=0,k_guide=k_guide,k_civil=k_civil)
        if not question or not question.strip():return result
        return replace(result,cases=self._search_one(CASE,question,k_case))

    def _search_one(self, corpus, question, k):
        if corpus.name != CASE.name:
            return super()._search_one(corpus, question, k)
        if k <= 0:
            self._request_trace.case = {}
            return []
        result, trace = self.case_backend.search_with_trace(question, k)
        self._request_trace.case = trace
        return result

    def search_with_trace(self, question, **kwargs):
        self._request_trace.case = {}
        result = self.search(question, **kwargs)
        return result, dict(self._request_trace.case)

    def evidence_payload(self, result):
        return {'schema':'lens-retrieval-evidence-v1', 'profile_version':self.case_profile['version'],
            'question':result.question, 'generation_status':'not_requested',
            'channels':{name:[dict(asdict(e),canonical_case_key=self._chunks.get(e.chunk_id,{}).get('metadata',{}).get('canonical_case_key'))
                for e in getattr(result,name)] for name in ('cases','laws','civil_laws','guides')}}


def read_internal_case_profile(path):
    profile=json.loads(Path(path).read_text('utf-8-sig'))
    if profile.get('schema')!='lens-case-internal-v1' or profile.get('fallback_allowed') is not False:
        raise ValueError('invalid internal case profile')
    root=Path(profile['data_root'])
    if not root.is_absolute():raise ValueError('case data root must be absolute')
    required={'database/cases.sqlite3','chunks/cases.jsonl','CORPUS_MANIFEST.json',
              'DB_CONNECTION_AUDIT.json','INDEX_MANIFEST.json'}
    if set(profile['files'])!=required:raise ValueError('incomplete case bundle files')
    for name,expected in profile['files'].items():
        if file_hash(root/name)!=expected:raise ValueError('case file hash mismatch: '+name)
    policy=CaseInternalPolicy(**profile['policy'])
    if profile.get('model_id')!='nlpai-lab/KURE-v1' or profile.get('dimension')!=1024:
        raise ValueError('invalid case embedding model')
    if not __import__('re').fullmatch('[0-9a-f]{40}',profile.get('model_revision','')):
        raise ValueError('embedding revision must be pinned')
    if 'embedding_local' in profile:
        model=profile['embedding_local'];model_root=Path(model['path']).resolve()
        if not Path(model['path']).is_absolute() or not model['files']:
            raise ValueError('local embedding model must be pinned')
        for name,expected in model['files'].items():
            target=(model_root/name).resolve()
            if not target.is_relative_to(model_root) or file_hash(target)!=expected:
                raise ValueError('embedding model file mismatch')
    if policy.rerank=='cross_encoder':
        model=profile['cross_encoder'];model_root=Path(model['path'])
        if not model_root.is_absolute() or not __import__('re').fullmatch('[0-9a-f]{40}',model.get('revision','')):
            raise ValueError('cross-encoder path/revision must be pinned')
        for name,expected in model['files'].items():
            if Path(name).name!=name or file_hash(model_root/name)!=expected:
                raise ValueError('cross-encoder file mismatch')
    return profile


def load_internal_case_profile(path, *, base_service=None):
    from src.retrieval.dense import SentenceTransformerEmbedding, ChromaRetriever
    from src.retrieval.retriever import load_chunks
    profile=read_internal_case_profile(path);root=Path(profile['data_root'])
    chunks=load_chunks(root/'chunks/cases.jsonl')
    db=sqlite3.connect((root/'database/cases.sqlite3').resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    stored={r[0]:(r[1],r[2],r[3]) for r in db.execute('SELECT chunk_id,case_id,canonical_case_key,text FROM case_chunks')}
    db_metadata={r[0]:json.loads(r[1]) for r in db.execute('SELECT chunk_id,metadata_json FROM case_chunks')}
    sources={r[0]:(r[1],r[2]) for r in db.execute('SELECT case_id,canonical_case_key,source_url FROM case_sources')}
    missing=db.execute('SELECT count(*) FROM case_chunks c LEFT JOIN case_sources s ON c.case_id=s.case_id WHERE s.case_id IS NULL OR c.canonical_case_key<>s.canonical_case_key').fetchone()[0]
    db.close()
    exported={c['chunk_id']:(str(c['metadata']['case_id']),c['metadata']['canonical_case_key'],c['text']) for c in chunks}
    if stored!=exported or missing:raise ValueError('case DB/chunk identity or text mismatch')
    if db_metadata!={c['chunk_id']:c['metadata'] for c in chunks}:
        raise ValueError('case DB/chunk metadata mismatch')
    for c in chunks:
        meta=c['metadata']
        if sources[str(meta['case_id'])] != (meta['canonical_case_key'],meta['source_url']):
            raise ValueError('case DB/chunk source mismatch')
    embedding=SentenceTransformerEmbedding(profile.get('embedding_local',{}).get('path',profile['model_id']),
        revision=profile['model_revision'])
    embedding.name=profile['model_id']
    dense=ChromaRetriever(embedding,root/'index/chroma_kurev1_1024')
    if index_content_hash(dense.collection)!=profile['index_content_sha256']:
        raise ValueError('case logical index hash mismatch')
    got=dense.collection.get(include=['documents','metadatas'])
    if dict(zip(got['ids'],got['documents']))!={c['chunk_id']:c['text'] for c in chunks}:
        raise ValueError('case index text mismatch')
    index_metadata=dict(zip(got['ids'],got['metadatas']))
    for c in chunks:
        for name in ('case_id','canonical_case_key','source_url','status','source_name'):
            if index_metadata[c['chunk_id']].get(name)!=c['metadata'].get(name):
                raise ValueError('case index metadata mismatch: '+name)
    policy=CaseInternalPolicy(**profile['policy']);cross_encoder=None
    if policy.rerank=='cross_encoder':
        config=profile['cross_encoder']
        cross_encoder=LocalCaseCrossEncoder(config['path'],device=config['device'],
            max_length=config['max_length'],batch_size=config['batch_size'])
    return AppliedCaseInternalService(CaseInternalRetriever(chunks,dense,policy,cross_encoder),profile,base_service)
