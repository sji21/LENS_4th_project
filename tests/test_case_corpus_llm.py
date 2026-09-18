import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from scripts.case_corpus_llm import generate, result_from_payload


def payload():
    return {"schema":"lens-retrieval-evidence-v1", "profile_version":"test-v1",
            "question":"보증금 반환 판례는?", "channels":{"cases":[{
                "rank":1,"chunk_id":"case-1","doc_type":"case","citation":"실제 출처 표기",
                "text":"판례 본문 보존 검사", "score":0.1,
                "source_url":"https://example.test/case-1", "canonical_case_key":"key-1"}]}}


def test_saved_evidence_reaches_existing_generation_prompt():
    captured=[]
    def invoke(value):
        captured.extend(m.content for m in value.to_messages())
        return AIMessage(content="검색 근거를 확인했습니다.")
    result=generate(payload(),RunnableLambda(invoke))
    assert "판례 본문 보존 검사" in "\n".join(captured)
    assert "실제 출처 표기" in "\n".join(captured)
    assert result["evidence"]["channels"]["cases"][0]["source_url"].endswith("case-1")
    assert result["application_quality_verified"] is False


def test_empty_or_unversioned_evidence_is_rejected():
    with pytest.raises(ValueError):result_from_payload({})
    data=payload();data["channels"]["cases"]=[]
    with pytest.raises(ValueError):result_from_payload(data)


def test_empty_generation_is_not_success():
    with pytest.raises(RuntimeError):
        generate(payload(),RunnableLambda(lambda value:AIMessage(content="")))
