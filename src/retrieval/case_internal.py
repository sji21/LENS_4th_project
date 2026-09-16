"""Versioned case-only retrieval boundary, without routing or generation changes."""
from dataclasses import dataclass, replace
import math
import re
import threading
import time

from src.retrieval.case_profile import case_query_filter
from src.retrieval.case_rerank import rerank_cases
from src.retrieval.hybrid import HybridRetriever, Member
from src.retrieval.retriever import BM25Retriever, tokenize, matches
from src.retrieval.service import CASE, _to_evidence
from src.retrieval.terms import expand, expand_civil


@dataclass(frozen=True)
class CaseInternalPolicy:
    candidate_depth: int = 80
    fusion_depth: int = 80
    rrf_k: int = 60
    bm25_weight: float = 2
    dense_weight: float = 1
    query_expansion: str = 'civil_terms'
    field_policy: str = 'tax_source_guard'
    rerank: str = 'band_3pct'
    min_relevance: float = 0.0
    complement_weight: float = 0.0
    body_policy: str = 'selected_chunk'
    require_lexical_support: bool = False

    def __post_init__(self):
        if self.candidate_depth not in (80, 160, 240):
            raise ValueError('candidate_depth must be 80/160/240')
        if type(self.fusion_depth) is not int or not 1 <= self.fusion_depth <= 2*self.candidate_depth:
            raise ValueError('invalid fusion depth')
        if type(self.rrf_k) is not int or not 1 <= self.rrf_k <= 100:
            raise ValueError('invalid RRF constant')
        if any(not math.isfinite(v) or v <= 0 for v in (self.bm25_weight, self.dense_weight)):
            raise ValueError('invalid member weights')
        if self.query_expansion not in ('standard', 'civil_terms') or self.field_policy not in ('all', 'tax_source_guard'):
            raise ValueError('invalid case query policy')
        if self.rerank not in ('pure_rrf', 'band_3pct', 'cross_encoder'):
            raise ValueError('invalid case reranking policy')
        if not 0 <= self.min_relevance <= 1 or not 0 <= self.complement_weight <= 1:
            raise ValueError('invalid relevance/complement setting')
        if self.rerank != 'cross_encoder' and (self.min_relevance or self.complement_weight):
            raise ValueError('cross-encoder scores required for relevance selection')
        if self.body_policy not in ('selected_chunk','continuous_official_case'):
            raise ValueError('invalid case body policy')
        if type(self.require_lexical_support) is not bool:
            raise ValueError('invalid lexical support policy')


def continuous_case_bodies(chunks):
    """Reconstruct continuous indexed source spans, never mix separate cases."""
    grouped={};contexts={}
    for c in chunks:
        m=c['metadata']
        if 'source_relative_path' in m:
            grouped.setdefault(m['canonical_case_key'],[]).append(c)
    for key,parts in grouped.items():
        parts.sort(key=lambda c:c['metadata']['source_start'])
        first=parts[0];m=first['metadata'];start=m['source_start'];end=start;body=''
        for c in parts:
            meta=c['metadata'];a,b=meta['source_start'],meta['source_end'];text=c['text'].split('\n',1)[1]
            if (meta['source_sha256'],meta['source_url'],meta['case_id'])!=(m['source_sha256'],m['source_url'],m['case_id']):
                raise ValueError('case source context identity mismatch')
            if len(text)!=b-a or a>end:raise ValueError('case source context has a gap')
            overlap=min(end,b)-a
            if body[a-start:a-start+overlap]!=text[:overlap]:raise ValueError('case source overlap mismatch')
            if b>end:body+=text[end-a:];end=b
        contexts[key]={'text':first['text'].split('\n',1)[0]+'\n'+body,
            'source_url':m['source_url'],'source_sha256':m['source_sha256'],
            'source_relative_path':m['source_relative_path'],'source_start':start,'source_end':end,
            'source_chunk_ids':[c['chunk_id'] for c in parts],
            'policy':'continuous_official_case'}
    return contexts


class LocalCaseCrossEncoder:
    """One local, reusable model; errors propagate without silent fallback."""
    def __init__(self, model_path, *, device='cpu', max_length=2048, batch_size=8):
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=False)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model_path, local_files_only=True, trust_remote_code=False).to(device).eval()
        self.device, self.max_length, self.batch_size = device, max_length, batch_size
        self._lock = threading.Lock()
        self.last_truncated_pairs = 0

    def score(self, query, documents):
        import torch
        result = []
        with self._lock, torch.inference_mode():
            self.last_truncated_pairs = 0
            for offset in range(0, len(documents), self.batch_size):
                docs = documents[offset:offset+self.batch_size]
                questions = [query]*len(docs)
                lengths = self.tokenizer(questions, docs, truncation=False, return_length=True)['length']
                self.last_truncated_pairs += sum(n > self.max_length for n in lengths)
                encoded = self.tokenizer(questions, docs, padding=True, truncation='only_second',
                                         max_length=self.max_length, return_tensors='pt').to(self.device)
                values = self.model(**encoded).logits.reshape(-1).sigmoid().cpu().tolist()
                if len(values) != len(docs) or not all(math.isfinite(v) for v in values):
                    raise ValueError('invalid cross-encoder scores')
                result.extend(values)
        return result


class CaseInternalRetriever:
    """search(query, k) preserves requested K; top2 is an explicit caller choice."""
    def __init__(self, chunks, dense, policy=CaseInternalPolicy(), cross_encoder=None):
        if any(c['metadata'].get('doc_type') != 'case' for c in chunks):
            raise ValueError('only case chunks are accepted')
        if any(not matches(c['metadata'],CASE.where()) for c in chunks):
            raise ValueError('case chunk metadata does not satisfy existing case filter')
        self.chunks = {c['chunk_id']: c for c in chunks}
        if len(self.chunks) != len(chunks):
            raise ValueError('duplicate chunk IDs')
        self.policy, self.cross_encoder = policy, cross_encoder
        self.body_contexts=continuous_case_bodies(chunks)
        if policy.rerank == 'cross_encoder' and cross_encoder is None:
            raise ValueError('a local cross-encoder is required')
        query_expander = expand_civil if policy.query_expansion == 'civil_terms' else expand
        self.hybrid = HybridRetriever([
            Member(BM25Retriever(chunks, b=CASE.bm25_b, query_expander=query_expander),
                   'bm25', policy.bm25_weight, CASE.expand_weight),
            Member(dense, 'dense', policy.dense_weight)], rrf_k=policy.rrf_k, depth=policy.candidate_depth)

    def _key(self, cid):
        key = self.chunks[cid]['metadata'].get('canonical_case_key')
        if not key:
            raise ValueError('canonical case identity is required: '+cid)
        return key

    def render(self, cid, rank, score):
        chunk=self.chunks[cid];item=_to_evidence(rank,chunk,score)
        source={'policy':'selected_chunk','anchor_chunk_id':cid,'source_chunk_ids':[cid]}
        if self.policy.body_policy=='continuous_official_case':
            context=self.body_contexts.get(self._key(cid))
            if context:
                item=replace(item,text=context['text'],source_url=context['source_url'])
                source=dict(context,anchor_chunk_id=cid);source.pop('text')
        return item,source

    def candidates(self, query):
        policy = self.policy
        where = case_query_filter(CASE.where(), query, policy.field_policy)
        # Keep member depth independent of fusion depth, unlike max(depth, k).
        member_hits, fused = {}, {}
        for member in self.hybrid.members:
            hits = self.hybrid._ask(member, query, policy.candidate_depth, where)
            member_hits[member.name] = [cid for cid, _ in hits]
            for rank, (cid, _) in enumerate(hits, 1):
                if cid not in self.chunks:
                    raise ValueError('index/chunk mismatch: '+cid)
                fused[cid] = fused.get(cid, 0)+member.weight/(policy.rrf_k+rank)
        all_fused = sorted(fused.items(), key=lambda h: (-h[1], h[0]))
        return all_fused[:policy.fusion_depth], {'query': query, 'where': where,
            'member_hits': member_hits, 'full_fusion': all_fused,
            'fusion_candidates': all_fused[:policy.fusion_depth]}

    def select(self, query, hits, k, scores=None,member_hits=None):
        policy = self.policy
        if policy.require_lexical_support:
            if member_hits is None:raise ValueError('lexical member evidence is required')
            allowed=set(member_hits['bm25'])
            if scores is not None:
                if len(scores)!=len(hits):raise ValueError('invalid relevance score count')
                kept=[(hit,value) for hit,value in zip(hits,scores) if hit[0] in allowed]
                hits=[h for h,v in kept];scores=[v for h,v in kept]
            else:hits=[h for h in hits if h[0] in allowed]
        if policy.rerank == 'cross_encoder':
            if scores is None:
                scores = self.cross_encoder.score(query, [self.chunks[cid]['text'] for cid, _ in hits])
            if len(scores) != len(hits) or any(not math.isfinite(v) or not 0 <= v <= 1 for v in scores):
                raise ValueError('invalid relevance scores')
            ranked = sorted(((cid, score) for (cid, _), score in zip(hits, scores)
                             if score >= policy.min_relevance), key=lambda h: (-h[1], h[0]))
        elif policy.rerank == 'band_3pct':
            ranked = rerank_cases(hits, self.chunks, len(hits), .03)
        else:
            ranked = list(hits)
        unique, seen = [], set()
        for cid, score in ranked:
            key = self._key(cid)
            if key not in seen:
                unique.append((cid, score)); seen.add(key)
        selected = []
        query_terms = set(tokenize(query, 2))
        covered = set()
        while unique and len(selected) < k:
            def value(hit):
                cid, score = hit
                terms = query_terms.intersection(tokenize(self.chunks[cid]['text'], 2))
                gain = len(terms-covered)/max(1, len(query_terms))
                return score+policy.complement_weight*gain
            # Query-token coverage is a tested heuristic, not a claim of legal issue support.
            best = max(range(len(unique)), key=lambda i: (value(unique[i]), -i)) if policy.complement_weight else 0
            cid, score = unique.pop(best)
            selected.append((cid, score))
            covered.update(query_terms.intersection(tokenize(self.chunks[cid]['text'], 2)))
        return selected

    def search_with_trace(self, query, k=2):
        if type(k) is not int or k < 0 or k > self.policy.fusion_depth:
            raise ValueError('K must be an integer between 0 and fusion_depth')
        if not query.strip() or k == 0:
            return [], {'query': query, 'member_hits': {}, 'fusion_candidates': [], 'selected': []}
        start = time.perf_counter()
        hits, trace = self.candidates(query)
        trace['candidate_seconds'] = time.perf_counter()-start
        start = time.perf_counter()
        selected = self.select(query, hits, k,member_hits=trace['member_hits'])
        evidence = [];body_sources=[]
        for rank, (cid, score) in enumerate(selected, 1):
            chunk = self.chunks[cid]
            item,source = self.render(cid,rank,score)
            if not item.text.strip() or not item.source_url:
                raise ValueError('case text and source URL are required')
            evidence.append(item)
            body_sources.append(source)
        trace.update(selected=selected, rerank_seconds=time.perf_counter()-start,
                     body_sources=body_sources,
                     unique_candidate_cases=len({self._key(cid) for cid, _ in hits}))
        if self.policy.rerank == 'cross_encoder':
            trace['truncated_pairs'] = self.cross_encoder.last_truncated_pairs
        return evidence, trace

    def search(self, query, k=2):
        return self.search_with_trace(query, k)[0]
