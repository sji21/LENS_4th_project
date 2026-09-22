import hashlib
import json
import sqlite3

import pytest

from src.ingestion.knowledge_release import (FILES, INDEX, MODEL_FILES, POLICY_KEYS, REVISION,
    audit_database, file_hash, load_rows, read_release, verify_release, write_runtime_profile)


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "release"
    (root / "database").mkdir(parents=True)
    (root / "chunks").mkdir()
    (root / INDEX).mkdir(parents=True)
    (root / INDEX / "chroma.sqlite3").write_bytes(b"placeholder-index")
    db = sqlite3.connect(root / FILES[0])
    db.executescript("""
        CREATE TABLE documents(document_id TEXT,source_url TEXT,checksum TEXT,status TEXT);
        CREATE TABLE cases(case_id TEXT,document_id TEXT,canonical_case_key TEXT,court_name TEXT,
          case_number TEXT,decision_date TEXT,case_name TEXT,full_text TEXT,source_name TEXT,
          scope_tier TEXT,court_level INTEGER,observed_at TEXT,corpus_active INTEGER,current_version_checksum TEXT);
        CREATE TABLE chunks(chunk_id TEXT,document_id TEXT,chunk_index INTEGER,content TEXT,
          source_type TEXT,checksum TEXT,token_count INTEGER,case_id TEXT);
    """)
    chunks = []
    for i in (1, 2):
        full_text = f"보증금 반환 판결 전체 원문 {i}"
        text = f"[대법원 2020다{i} 보증금반환]\n보증금 반환에 관한 판결요지 {i}"
        digest = hashlib.sha256(text.encode()).hexdigest()
        cid, did, key, url = str(i), f"doc-{i}", f"canonical-{i}", f"https://example.test/{i}"
        db.execute("INSERT INTO documents VALUES(?,?,?,?)", (did,url,hashlib.sha256(full_text.encode()).hexdigest(),"current"))
        db.execute("INSERT INTO cases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid,did,key,"대법원",f"2020다{i}","2020-01-01","보증금반환",full_text,
             "국가법령정보센터","lease_related",0,"2026-09-14",1,"version"))
        chunk_id = f"case:{i}#0"
        db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)", (chunk_id,did,0,text,"case",digest,30,cid))
        meta = dict(case_id=cid,canonical_case_key=key,court_name="대법원",case_number=f"2020다{i}",
            decision_date="2020-01-01",source_url=url,source_name="국가법령정보센터",status="current",
            scope_tier="lease_related",court_level=0,version_checksum="version",doc_type="case",
            checksum=digest,token_count=30)
        if i == 1:
            meta.update(observed_at="2026-09-14",corpus_active=True)
        chunks.append(dict(chunk_id=chunk_id,doc_id=did,chunk_index=0,text=text,metadata=meta))
    db.commit(); db.close()
    for name in FILES[1:]:
        (root/name).write_text("".join(json.dumps(c,ensure_ascii=False)+"\n" for c in chunks)
                               if name == "chunks/cases.jsonl" else "",encoding="utf-8")
    release = {"schema":"lens-knowledge-corpus-release-v1","version":"test-v1","case_count":2,
        "chunk_count":2,"fallback_allowed":False,"files":{f:file_hash(root/f) for f in FILES},
        "embedding_model":{"model_id":"nlpai-lab/KURE-v1","revision":REVISION,"dimension":1024,
                           "files":{name:"b"*64 for name in MODEL_FILES}},
        "index":{"path":INDEX,"document_count":2,"logical_sha256":"c"*64,
                 "files":{"chroma.sqlite3":file_hash(root/INDEX/"chroma.sqlite3")}},
        "retrieval_policy":dict(candidate_depth=80,return_k=20,rerank_policy="band_3pct",
            case_query_expansion="civil_terms",case_rrf_k=60,case_bm25_weight=2,
            case_dense_weight=1,case_field_policy="tax_source_guard")}
    path = root/"release.json"
    path.write_text(json.dumps(release),encoding="utf-8")
    return path


def test_complete_db_jsonl_identity_and_optional_supplement_fields(bundle):
    report = verify_release(bundle, verify_index=False, expected_case_count=2)
    assert report["pass"] and report["full_text_source_hashes_verified"]
    assert report["case_chunks_without_observed_at"] == 1
    assert report["case_chunks_without_corpus_active"] == 1


def test_tampered_files_refused_before_model_loading(bundle):
    (bundle.parent/"chunks/cases.jsonl").write_text("tampered")
    with pytest.raises(ValueError,match="해시 불일치"):
        read_release(bundle,expected_case_count=2)


@pytest.mark.parametrize("column,value", [("full_text",""),("source_url",""),("full_text","변조")])
def test_missing_or_changed_source_refused(bundle,column,value):
    db=sqlite3.connect(bundle.parent/FILES[0])
    table="documents" if column=="source_url" else "cases"
    db.execute(f"UPDATE {table} SET {column}=?",(value,));db.commit();db.close()
    with pytest.raises(ValueError,match="필수값 누락|SHA-256 불일치"):
        audit_database(bundle.parent,load_rows(bundle.parent),2)


def test_changed_source_identity_refused(bundle):
    rows=load_rows(bundle.parent)
    rows["case:1#0"]["metadata"]["source_url"]="https://wrong.test"
    with pytest.raises(ValueError,match="메타데이터 불일치"):
        audit_database(bundle.parent,rows,2)


def test_model_traversal_is_refused(bundle):
    release=json.loads(bundle.read_text(encoding="utf-8"))
    release["embedding_model"]["files"]={"../outside":"b"*64}
    bundle.write_text(json.dumps(release))
    with pytest.raises(ValueError,match="상대 경로|고정 정보"):
        read_release(bundle,expected_case_count=2)


def test_profile_requires_full_index_verification(bundle,monkeypatch):
    import src.ingestion.knowledge_release as module
    def fail(*args,**kwargs):raise ValueError("invalid index")
    monkeypatch.setattr(module,"verify_release",fail)
    output=bundle.parent/"runtime-profile.json"
    with pytest.raises(ValueError,match="invalid index"):
        write_runtime_profile(bundle,output,expected_case_count=2)
    assert not output.exists()


def test_index_physical_tampering_is_refused(bundle):
    (bundle.parent/INDEX/"chroma.sqlite3").write_bytes(b"changed native index")
    with pytest.raises(ValueError,match="물리 파일 해시 불일치"):
        read_release(bundle,expected_case_count=2)


def test_reexport_preserves_db_content_metadata_and_byte_hash(bundle,monkeypatch):
    import src.ingestion.knowledge_release as module
    monkeypatch.setattr(module,"verify_release",lambda *a,**kw:{"pass":True})
    output=bundle.parent/"reexport.jsonl"
    module.reexport_case_chunks(bundle,output)
    assert output.read_bytes()==(bundle.parent/"chunks/cases.jsonl").read_bytes()
    with pytest.raises(FileExistsError):
        module.reexport_case_chunks(bundle,output)


def test_portable_profile_keeps_original_policy_and_base_channels(bundle,monkeypatch):
    import src.ingestion.knowledge_release as module
    monkeypatch.setattr(module,"verify_release",lambda *a,**kw:{"pass":True})
    output=bundle.parent/"runtime-profile.json"
    write_runtime_profile(bundle,output,expected_case_count=2)
    profile=json.loads(output.read_text(encoding="utf-8"))
    assert profile["data_root"]==str(bundle.parent.resolve())
    assert profile["preserve_base_channels"] is True
    assert profile["model_revision"]==REVISION
    for key in POLICY_KEYS:
        assert profile[key]==json.loads(bundle.read_text(encoding="utf-8"))["retrieval_policy"][key]


def test_db_exported_case_evidence_reaches_llm_prompt(bundle):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from src.generation.chain import build_qa_chain
    from src.generation.prompt import format_context
    from src.retrieval.case_profile import CaseCorpusRetrievalService
    rows=load_rows(bundle.parent)
    profile=json.loads(bundle.read_text(encoding="utf-8"))["retrieval_policy"]
    profile["version"]="test-v1"
    service=CaseCorpusRetrievalService(list(rows.values()),None,profile)
    result=service.search("보증금 반환 판례",k_law=0,k_case=2,k_guide=0,k_civil=0)
    captured=[]
    def generate(prompt):
        captured.extend(m.content for m in prompt.to_messages())
        return AIMessage(content="판례 근거를 확인했습니다.")
    answer=build_qa_chain(RunnableLambda(generate)).invoke({"context":format_context(result),"question":"보증금 반환 판례"})
    assert answer and len(result.cases)==2
    for evidence in result.cases:
        assert evidence.source_url.startswith("https://example.test/")
        assert evidence.text.split("\n",1)[1] in "\n".join(captured)


def test_overlay_keeps_base_channel_results(bundle):
    from src.retrieval.case_profile import CaseCorpusRetrievalService
    from src.retrieval.service import Evidence,RetrievalResult
    law=Evidence(rank=1,chunk_id="law",doc_type="law",citation="법령 인용",
                 text="법령 원문",score=1,source_url="https://example.test/law")
    class Base:
        def search(self,question,**kwargs):
            assert kwargs["k_case"]==0
            return RetrievalResult(question=question,laws=[law])
    profile={"version":"test",**json.loads(bundle.read_text(encoding="utf-8"))["retrieval_policy"]}
    service=CaseCorpusRetrievalService(list(load_rows(bundle.parent).values()),None,profile,base_service=Base())
    result=service.search("보증금 반환 판례",k_case=2)
    assert result.laws==[law] and len(result.cases)==2
    payload=service.evidence_payload(result)
    assert payload["channels"]["laws"][0]["source_url"]==law.source_url
    assert payload["channels"]["laws"][0]["canonical_case_key"] is None
