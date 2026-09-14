"""Citation provenance and formatting regressions; no external models or DB."""
from dataclasses import replace

import pytest

from src.generation.citation import audit_citations, citation_scan_text, _build_law_index
from src.generation.models import Answer
from src.generation.validation import audit_answer
from src.retrieval.service import Evidence, RetrievalResult


def evidence(citation, body="① 본문", chunk_id="source"):
    return Evidence(1, chunk_id, "law", citation, body, 1.0)


def answer(raw, *sources):
    return Answer("임차권등기 비용을 청구할 수 있나요?", "answered", raw,
                  raw_text=raw, laws=sources)


@pytest.mark.parametrize("source,body,claim", [
    ("주택임대차보호법 제3조의3", "① 제8조를 참조한다.",
     "주택임대차보호법 제8조에 따라 비용을 청구할 수 있습니다."),
    ("주택임대차보호법 제6조의3", "① 제7조를 참조한다.",
     "주택임대차보호법 제7조 제8호에 따라 갱신을 거절할 수 있습니다."),
    ("주택임대차보호법 제3조의3", "① 「민사집행법」 제280조 및 제291조를 준용한다.",
     "주택임대차보호법 제291조에 따릅니다."),
    ("주택임대차보호법 제3조", "① 민법 제575조를 준용한다.",
     "민법 제575조에 따릅니다."),
    ("주택임대차보호법 제3조", "① 「민사집행법」 제280조를 준용한다.",
     "「민사집행법」 제280조에 따릅니다."),
])
def test_body_reference_is_not_retrieved_provenance(source, body, claim):
    report = audit_answer(answer(claim, evidence(source, body)))
    assert report.by_kind("citation")


def test_six_chunks_only_grant_their_six_article_identities():
    sources = tuple(evidence(f"검증용법 제{i}조", "① 제280조, 제291조를 참조한다.", str(i))
                    for i in range(1, 7))
    index, _ = _build_law_index(sources)
    assert set(index) == {("검증용법", (i, None)) for i in range(1, 7)}


def test_reference_names_remain_available_without_granting_support():
    ev = evidence("검증용법 제1조", "① 「민사집행법」 제280조를 준용한다.")
    index, names = _build_law_index((ev,))
    assert "민사집행법" in names
    assert ("민사집행법", (280, None)) not in index


def test_separately_retrieved_reference_is_supported_by_its_own_chunk():
    origin = evidence("검증용법 제1조", "① 민법 제575조를 준용한다.", "origin")
    target = evidence("민법 제575조", chunk_id="target")
    a = answer("민법 제575조에 따릅니다.", origin)
    a = replace(a, civil_laws=(target,))
    audit = audit_citations(a)
    assert audit.is_valid
    assert audit.mentions[0].evidence_chunk_ids == ("target",)


@pytest.mark.parametrize("label,body,valid", [
    ("", "[검증용법 제1조]\n① 본문", True),
    ("", "본문에서 검증용법 제1조를 참조한다.", False),
    ("출처 미상", "[검증용법 제1조]\n① 본문", False),
    ("검증용법 제1조 및 제2조", "① 본문", False),
    ("다른법 제1조", "① 검증용법 제1조를 참조한다.", False),
    ("검증용법 제1조의2", "① 제1조를 참조한다.", False),
    ("검증용법 제10조", "① 제1조를 참조한다.", False),
])
def test_source_identity_is_not_inferred_from_arbitrary_prose(label, body, valid):
    assert audit_citations(answer("검증용법 제1조", evidence(label, body))).is_valid == valid


FORMATS = [
    "{law} {article}", "**{law} {article}**", "**{law}** {article}",
    "{law} **{article}**", "**{law}** **{article}**",
    "__{law}__ __{article}__", "*{law}* *{article}*",
    "***{law}*** ***{article}***", "「{law}」 **{article}**",
]


@pytest.mark.parametrize("format", FORMATS)
@pytest.mark.parametrize("join", ["과 ", "와 ", " 및 ", " 또는 ", ", "])
@pytest.mark.parametrize("supplied", [(3,), (4,), (3, 4)])
def test_each_adjacent_citation_is_checked(format, join, supplied):
    first = format.format(law="민법", article="제3조")
    second = format.format(law="민법", article="제4조")
    raw = first + join + second + "에 따릅니다."
    sources = tuple(evidence(f"민법 제{n}조", chunk_id=str(n)) for n in supplied)
    audit = audit_citations(answer(raw, *sources))
    assert len(audit.mentions) == 2
    assert [m.supported for m in audit.mentions] == [3 in supplied, 4 in supplied]
    assert audit.is_valid == (len(supplied) == 2)
    assert all(m.text in raw for m in audit.mentions)


def test_different_laws_branches_and_paragraphs_keep_their_identities():
    raw = "**주택임대차보호법 제3조의3 제1항** 및 **민법 제626조 제1항**에 따릅니다."
    a = answer(raw, evidence("주택임대차보호법 제3조의3", chunk_id="housing"),
               evidence("민법 제626조", chunk_id="civil"))
    assert [m.evidence_chunk_ids for m in audit_citations(a).mentions] == [("housing",), ("civil",)]
    assert not audit_answer(a).issues


def test_internal_conjunction_in_multiword_law_name_is_preserved():
    raw = "민법 제3조 및 기록 및 보존법 제4조에 따릅니다."
    a = answer(raw, evidence("민법 제3조", chunk_id="first"),
               evidence("기록 및 보존법 제4조", chunk_id="second"))
    assert [m.evidence_chunk_ids for m in audit_citations(a).mentions] == [("first",), ("second",)]


@pytest.mark.parametrize("title", [
    "「국가유산기본법」 제3조에 따른 국가유산의 국유",
    "제3조 및 제4조에 따른 절차", "특례(다른법 제3조 관련)", "", "일반 제목",
])
@pytest.mark.parametrize("legacy_header", [False, True])
def test_article_title_references_never_change_the_source_identity(title, legacy_header):
    from src.retrieval.service import citation_of
    label = citation_of(dict(doc_type="law", title="민법", article_no="제255조", article_title=title))
    ev = evidence("" if legacy_header else label, f"[{label}]\n① 본문")
    index, _ = _build_law_index((ev,))
    assert set(index) == {("민법", (255, None))}
    assert audit_citations(answer("민법 제255조에 따릅니다.", ev)).is_valid
    assert not audit_citations(answer("민법 제3조에 따릅니다.", ev)).is_valid
    assert not audit_citations(answer("국가유산기본법 제3조에 따릅니다.", ev)).is_valid
    if not legacy_header:
        assert not audit_answer(answer("민법 제255조 제1항에 따릅니다.", ev)).issues
        assert audit_answer(answer("민법 제255조 제5항에 따릅니다.", ev)).by_kind("paragraph")


@pytest.mark.parametrize("label", [
    "민법 제255조 및 제3조(제목)",
    "민법 제255조(제목) 및 민법 제3조",
    "민법 제255조(제목) 및 민법 제3조(다른 제목)",
    "민법 제255조(끝나지 않은 제목", "민법 제255조(제목))",
])
def test_title_handling_does_not_hide_ambiguous_source_labels(label):
    assert not audit_citations(answer("민법 제255조", evidence(label))).is_valid


@pytest.mark.parametrize("entrypoint", ["chain", "graph"])
@pytest.mark.parametrize("claim,status", [("제255조", "answered"),
    ("제255조 제1항", "answered"), ("제3조", "abstained")])
def test_runtime_accepts_source_with_cross_reference_in_title(entrypoint, claim, status):
    from src.generation import chain, graph
    from src.generation.llm import get_llm
    from src.retrieval.service import citation_of
    label = citation_of(dict(doc_type="law", title="민법", article_no="제255조",
        article_title="「국가유산기본법」 제3조에 따른 국가유산의 국유"))
    ev = evidence(label, f"[{label}]\n① 본문")
    class Service:
        def search(self, question, **kwargs):
            return RetrievalResult(question, civil_laws=[ev])
    fn = chain.answer_question if entrypoint == "chain" else graph.answer_question
    raw = f"민법 {claim}에 따릅니다."
    result = fn("임차권등기 비용을 청구할 수 있나요?", service=Service(),
                llm=get_llm(fake_responses=[raw, "PASS"]))
    assert result.status == status


@pytest.mark.parametrize("format", FORMATS)
@pytest.mark.parametrize("article,valid", [("제3조의2", True), ("제30조", False)])
def test_emphasis_does_not_change_article_grounding(format, article, valid):
    raw = format.format(law="주택임대차보호법", article=article) + "에 따릅니다."
    a = answer(raw, evidence("주택임대차보호법 제3조의2"))
    audit = audit_citations(a)
    assert len(audit.mentions) == 1
    assert audit.is_valid == valid
    assert audit.mentions[0].text in raw
    assert a.raw_text == raw and a.text == raw
    assert len(citation_scan_text(raw)) == len(raw)


@pytest.mark.parametrize("reference,valid", [
    ("**제1항**", True), ("**제5항**", False),
    ("**제1항**·**제2항**", True), ("**제1항**·**제5항**", False),
    ("**제1항부터 제2항까지**", True), ("**제1항**부터 **제5항**까지", False),
])
def test_formatted_paragraphs_still_check_every_member(reference, valid):
    raw = f"**민법** **제626조** {reference}에 따릅니다."
    a = answer(raw, evidence("민법 제626조", "① 첫 항\n② 둘째 항"))
    report = audit_answer(a)
    assert not report.by_kind("citation")
    assert (not report.by_kind("paragraph")) == valid
    for issue in report.issues:
        assert issue.text in raw


@pytest.mark.parametrize("entrypoint", ["chain", "graph"])
@pytest.mark.parametrize("raw,status", [
    ("주택임대차보호법 제8조에 따라 비용을 청구할 수 있습니다.", "abstained"),
    ("**주택임대차보호법** **제8조**에 따라 비용을 청구할 수 있습니다.", "abstained"),
    ("**주택임대차보호법** **제3조의3**에 따라 비용을 청구할 수 있습니다.", "answered"),
    ("**주택임대차보호법** **제3조의3** **제5항**에 따릅니다.", "abstained"),
    ("**주택임대차보호법 제8조**과 **주택임대차보호법 제3조의3**에 따릅니다.", "abstained"),
    ("주택임대차보호법 제8조 및 주택임대차보호법 제3조의3에 따릅니다.", "abstained"),
])
def test_runtime_blocks_before_semantic_and_accepts_formatted_source(monkeypatch, entrypoint, raw, status):
    from src.generation import chain, graph
    from src.generation.llm import get_llm
    from src.generation.validation import SemanticJudgement

    ev = evidence("주택임대차보호법 제3조의3", "① 제8조를 참조한다.")
    class Service:
        def search(self, question, **kwargs):
            return RetrievalResult(question, laws=[ev])

    calls = []
    def judge_factory(llm):
        def judge(*args):
            calls.append(args)
            return SemanticJudgement(supported=True, detail="mock")
        return judge
    monkeypatch.setattr(chain, "_semantic_judge", judge_factory)
    monkeypatch.setattr(chain, "requires_semantic_validation", lambda a: True)
    fn = chain.answer_question if entrypoint == "chain" else graph.answer_question
    result = fn("임차권등기 비용을 청구할 수 있나요?", service=Service(),
                llm=get_llm(fake_responses=[raw]))
    assert result.status == status
    assert len(calls) == (1 if status == "answered" else 0)
