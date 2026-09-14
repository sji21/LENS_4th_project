"""Document provenance compares law identities, not display/log substrings."""
from dataclasses import replace

import pytest

from src.document_check.session_retrieval import SessionDocumentEvidence
from src.generation.citation import audit_citations
from src.generation.models import Answer
from src.generation.validation import audit_answer
from src.retrieval.service import Evidence


def document(text, chunk="document:1"):
    return SessionDocumentEvidence(chunk_id=chunk, filename="contract.pdf", page_number=1,
        extraction_method="text", checksum="test", text=text, document_id="contract",
        document_kind="임대차계약서")


def answer(raw, doc):
    official = Evidence(1, "official:3", "law", "민법 제3조", "① 본문", 1.0)
    return Answer("문서에 적힌 법령을 확인해줘", "answered", raw, raw_text=raw,
                  laws=(official,), document_evidences=(doc,))


@pytest.mark.parametrize("join", ["과 ", "와 ", " 및 ", " 또는 ", ", "])
@pytest.mark.parametrize("format", ["{}", "**{}**", "__{}__"])
@pytest.mark.parametrize("label", ["민법 제4조", "민법 제4조의2", "주택임대차보호법 제4조",
                                  "주택임대차보호법 시행령 제4조의2"])
def test_connected_document_citation_uses_identity_and_retains_original(join, format, label):
    raw = format.format("민법 제3조") + join + format.format(label) + "가 표시되어 있습니다."
    a = answer(raw, document("문서에는 " + label + "가 표시되어 있습니다."))
    audit = audit_citations(a)
    assert len(audit.mentions) == 2 and audit.is_valid
    assert [m.evidence_chunk_ids for m in audit.mentions] == [("official:3",), ("document:1",)]
    assert all(m.text in raw for m in audit.mentions)
    assert not audit_answer(a).by_kind("citation")
    assert a.raw_text == raw

    missing = replace(a, document_evidences=(document("법령 표기 없음"),))
    failed = audit_citations(missing)
    assert not failed.is_valid
    assert failed.unsupported[0].text == audit.mentions[1].text
    issues = audit_answer(missing).by_kind("citation")
    assert len(issues) == 1 and issues[0].text == failed.unsupported[0].text
    assert issues[0].text in raw


@pytest.mark.parametrize("doc_label,claim", [
    ("민법 제40조", "민법 제4조"), ("민법 제4조의2", "민법 제4조"),
    ("민법 제4조", "민법 제4조의2"), ("민법 제4조의20", "민법 제4조의2"),
    ("민법 제4조", "다른법 제4조"),
    ("주택임대차보호법 시행령 제4조", "주택임대차보호법 제4조"),
])
def test_different_law_article_or_branch_never_matches_by_substring(doc_label, claim):
    a = answer(f"**민법 제3조**과 **{claim}**가 표시되어 있습니다.", document(doc_label))
    audit = audit_citations(a)
    assert [m.supported for m in audit.mentions] == [True, False]
    assert audit_answer(a).by_kind("citation")[0].text == audit.unsupported[0].text


def test_document_spacing_and_emphasis_share_the_same_identity():
    a = answer("민법 제3조 및 민법 제4조의2", document("**민법** **제 4 조 의 2**"))
    assert audit_citations(a).is_valid


def test_document_support_is_local_and_does_not_become_an_official_link():
    from src.generation.evidence_highlight import build_citation_spans
    raw = "**민법 제3조**과 **민법 제4조**"
    a = answer(raw, document("민법 제4조"))
    assert audit_citations(a).is_valid
    assert not audit_citations(replace(a, document_evidences=())).is_valid
    assert [s['chunk_id'] for s in build_citation_spans(raw, a.evidences)] == ['official:3']


def test_matching_document_chunk_ids_only_and_official_source_precedence():
    a = answer("민법 제3조 및 민법 제4조", document("민법 제4조"))
    a = replace(a, document_evidences=(document("민법 제40조", "wrong"),
                                      document("민법 제3조 및 민법 제4조", "right")))
    assert [m.evidence_chunk_ids for m in audit_citations(a).mentions] == [("official:3",), ("right",)]
