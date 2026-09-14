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
