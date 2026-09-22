"""Deployment boundary and real-K invariants for the opt-in case bundle."""
from copy import deepcopy
import json
import pytest

from src.retrieval.case_profile import (
    CaseCorpusRetrievalService,
    RETIRED_INTERNAL_SCHEMA,
    load_case_profile,
    read_case_profile,
    FILES,
    file_hash,
)
from src.retrieval.case_rerank import rerank_cases
from src.retrieval.service import RetrievalService


def chunk(i, court=2, day="2020-01-01"):
    return {"chunk_id":f"case-{i}","text":"임대차 보증금 반환", "metadata":{
        "doc_type":"case","court_level":court,"decision_date":day,"status":"current",
        "court_name":"지방법원","case_number":str(i),"canonical_case_key":str(i),
        "source_url":f"https://www.law.go.kr/LSW/precInfoP.do?precSeq={i}"}}


def test_live_k_uses_same_candidate_pool_and_preserves_unrounded_rrf():
    chunks=[chunk(i,0 if i==2 else 2) for i in range(1,21)]
    profile={"candidate_depth":80,"return_k":20,"rerank_policy":"band_3pct","version":"test"}
    service=CaseCorpusRetrievalService(chunks,None,profile)
    calls=[]

    class Candidates:
        def search_with_member_hits(self,question,k,where):
            calls.append(k)
            return [(c['chunk_id'],.03-i*.0001) for i,c in enumerate(chunks)],{"bm25":[c['chunk_id'] for c in chunks]}

    service._retrievers[service.corpora[1].name]=Candidates()
    large,trace=service.search_with_trace("보증금",k_case=20,k_law=0,k_guide=0)
    small=service.search("보증금",k_case=3,k_law=0,k_guide=0)
    assert [x.chunk_id for x in small.cases]==[x.chunk_id for x in large.cases[:3]]
    assert calls==[80,80]
    assert large.cases[0].chunk_id=='case-2'
    assert len(trace['raw_rrf_candidates'])==20
    empty,trace=service.search_with_trace('  ')
    assert not empty.cases and not trace
    assert calls==[80,80]


def test_court_preference_cannot_cross_rrf_band():
    chunks={c['chunk_id']:c for c in [chunk(1,2),chunk(2,0),chunk(3,0)]}
    hits=[('case-1',1.0),('case-2',.98),('case-3',.90)]
    assert [x[0] for x in rerank_cases(hits,chunks,3,.03)]==['case-2','case-1','case-3']
    assert [x[0] for x in rerank_cases(hits,chunks,3,.01)]==['case-1','case-2','case-3']


def test_opt_in_profile_does_not_fallback_in_actual_service_factory(monkeypatch):
    from src.generation.chain import _build_service
    monkeypatch.setenv('LENS_CASE_RETRIEVAL_PROFILE','missing-profile.json')
    def fail(*a,**kw):raise ValueError('sealed corpus unavailable')
    monkeypatch.setattr(RetrievalService,'from_index',fail)
    with pytest.raises(ValueError,match='sealed corpus unavailable'):
        _build_service()
    with pytest.raises(ValueError,match='어휘 전용'):
        RetrievalService.from_local_chunks([])


def test_patch043_internal_profile_is_refused_by_product_loader(tmp_path):
    profile = tmp_path / "patch043-profile.json"
    profile.write_text(json.dumps({"schema": RETIRED_INTERNAL_SCHEMA}), encoding="utf-8")

    with pytest.raises(ValueError, match="PATCH-043 내부 판례 프로필"):
        load_case_profile(profile)


def test_profile_refuses_swapped_db_before_model_loading(tmp_path):
    for name in FILES:
        target=tmp_path/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(name)
    profile={"schema":"lens-case-retrieval-v1","files":{name:file_hash(tmp_path/name) for name in FILES},
             "model_id":"nlpai-lab/KURE-v1","model_revision":"a"*40,"dimension":1024,
             "rerank_policy":"band_3pct","candidate_depth":80,"return_k":20,"fallback_allowed":False,
             "index_content_sha256":"b"*64,"data_root":str(tmp_path)}
    path=tmp_path/'profile.json';path.write_text(json.dumps(profile))
    assert read_case_profile(path)==profile
    (tmp_path/'database/knowledge.sqlite3').write_text('another corpus')
    with pytest.raises(ValueError,match='파일 해시'):
        read_case_profile(path)


def test_revision_is_forwarded_on_both_cache_paths(monkeypatch):
    import sys
    from types import ModuleType
    from src.retrieval.dense import SentenceTransformerEmbedding
    calls=[]
    class Model:
        def __init__(self,name,**kwargs):
            calls.append(kwargs)
            if kwargs['local_files_only']:raise OSError('cache miss')
    module=ModuleType('sentence_transformers');module.SentenceTransformer=Model
    monkeypatch.setitem(sys.modules,'sentence_transformers',module)
    SentenceTransformerEmbedding('nlpai-lab/KURE-v1',revision='a'*40)
    assert [x['revision'] for x in calls]==['a'*40,'a'*40]


def test_explicit_paths_cannot_override_enabled_profile(monkeypatch):
    monkeypatch.setenv('LENS_CASE_RETRIEVAL_PROFILE','profile.json')
    with pytest.raises(ValueError,match='동시에 지정'):
        RetrievalService.from_index(chunk_paths=())


def test_installed_default_profile_is_discovered_without_environment(monkeypatch, tmp_path):
    import src.retrieval.case_profile as module
    profile = tmp_path / "runtime-profile.json"
    profile.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("LENS_CASE_RETRIEVAL_PROFILE", raising=False)
    monkeypatch.setattr(module, "DEFAULT_PROFILE", profile)
    assert module.configured_case_profile() == str(profile)


def test_environment_profile_overrides_installed_default(monkeypatch, tmp_path):
    import src.retrieval.case_profile as module
    profile = tmp_path / "runtime-profile.json"
    profile.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "DEFAULT_PROFILE", profile)
    monkeypatch.setenv("LENS_CASE_RETRIEVAL_PROFILE", "explicit-profile.json")
    assert module.configured_case_profile() == "explicit-profile.json"


def test_pulled_release_without_runtime_profile_refuses_seed_fallback(monkeypatch):
    # Exercise the case installation boundary independently of a developer's
    # explicitly activated MySQL release in their local .env.
    monkeypatch.setenv("LENS_MYSQL_RELEASE", "")
    import src.retrieval.case_profile as module
    class MissingProfile:
        def is_file(self):return False
        def with_name(self, name):
            assert name == "release.json"
            return PresentRelease()
    class PresentRelease:
        def is_file(self):return True
    monkeypatch.delenv("LENS_CASE_RETRIEVAL_PROFILE", raising=False)
    monkeypatch.setattr(module, "DEFAULT_PROFILE", MissingProfile())
    def forbidden(*args, **kwargs):
        raise AssertionError("기본 시드 서비스로 돌아가면 안 됩니다")
    monkeypatch.setattr(RetrievalService, "_from_index_without_case_profile", forbidden)
    with pytest.raises(ValueError, match="setup_data.py"):
        RetrievalService.from_index()


def test_generation_factory_propagates_case_installation_error(monkeypatch):
    from src.generation import chain
    from src.retrieval.service import CaseCorpusSetupError
    import src.retrieval.case_profile as module
    monkeypatch.setattr(module, "configured_case_profile", lambda: "")
    def missing(*args, **kwargs):
        raise CaseCorpusSetupError("setup_data.py로 설치하세요")
    def forbidden(*args, **kwargs):
        raise AssertionError("시드 fallback으로 돌아가면 안 됩니다")
    monkeypatch.setattr(RetrievalService, "from_index", missing)
    monkeypatch.setattr(chain, "fallback_chunk_paths", forbidden)
    with pytest.raises(CaseCorpusSetupError, match="setup_data.py"):
        chain._build_service()


def test_tax_guard_filters_both_members_before_refilling_and_keeps_explicit_tax():
    from src.retrieval.retriever import matches
    chunks = [chunk(i) for i in range(1, 122)]
    for i, c in enumerate(chunks):
        c['metadata']['source_name'] = '국세법령정보시스템' if i < 40 else '국가법령정보센터'
        c['text'] = '보일러 수리비 임대차 세금 국세 체납'
    calls = []
    class Dense:
        def search(self, query, k, where):
            calls.append((k, where))
            return [(c['chunk_id'], 1.0) for c in chunks if matches(c['metadata'], where)][:k]
    service = CaseCorpusRetrievalService(chunks, Dense(), {
        'candidate_depth':80,'return_k':20,'rerank_policy':'pure_rrf','version':'test',
        'case_field_policy':'tax_source_guard'})
    _, trace = service.search_with_trace('보일러 수리비', k_law=0, k_guide=0)
    assert calls[-1][0] == 80
    assert all(len(ids) == 80 for ids in trace['member_hits'].values())
    assert all(service._chunks[cid]['metadata']['source_name'] != '국세법령정보시스템'
               for ids in trace['member_hits'].values() for cid in ids)
    for query in ('국세 체납', '2006두9658', '2006 두 9658'):
        _, trace = service.search_with_trace(query, k_law=0, k_guide=0)
        assert any(cid == 'case-1' for ids in trace['member_hits'].values() for cid in ids)
        assert '$nin' not in str(calls[-1][1])
