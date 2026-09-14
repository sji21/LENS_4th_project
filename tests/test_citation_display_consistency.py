"""Copied prompt labels, validation and UI must agree without hiding claims."""
import pytest

from chat.services import display_extras, safe_text
from src.generation.citation import audit_citations, answer_citation_scan_text, citation_scan_text
from src.generation.evidence_highlight import build_citation_spans
from src.generation.models import Answer
from src.generation.prompt import _answer_source_names
from src.generation.validation import audit_answer
from src.retrieval.service import Evidence, RetrievalResult, citation_of


def source(label, chunk="civil"):
    return Evidence(1, chunk, "law", label, "① 본문", 1.0)


def answer(raw, *evidences):
    return Answer("임차권등기 비용을 청구할 수 있나요?", "answered", raw,
                  raw_text=raw, civil_laws=evidences)


TITLES = ["「국가유산기본법」 제3조에 따른 국가유산의 국유",
          "특례(다른법 제3조 제5항 관련)", "제3조 제5항에 따른 절차"]


@pytest.mark.parametrize("title", TITLES)
@pytest.mark.parametrize("format", ["{}", "**{}**", "__{}__"])
def test_copied_prompt_label_passes_all_validators_and_display(title, format):
    label = citation_of(dict(doc_type="law", title="민법", article_no="제255조", article_title=title))
    ev = source(label)
    prompt = _answer_source_names(RetrievalResult("질문", civil_laws=[ev]))
    assert label in prompt
    raw = format.format(label) + "에 따릅니다."
    a = answer(raw, ev)
    assert audit_citations(a).is_valid
    assert not audit_answer(a).issues
    assert a.raw_text == raw
    assert len(answer_citation_scan_text(raw, a.evidences)) == len(raw)
    spans = display_extras(a, safe_text(raw), [])['citations']
    assert len(spans) == 1
    assert spans[0]['chunk_id'] == "civil"
    assert spans[0]['excerpt'] == "① 본문"
    assert spans[0]['url']
    assert safe_text(raw)[spans[0]['start']:spans[0]['end']] == "민법 제255조"


@pytest.mark.parametrize("claim", [
    "민법 제255조(다른법 제3조 제5항 관련 수정)",
    "민법 제255조(다른법 제3조 제5항 관련; 민법 제9조)",
    "민법 제255조. (다른법 제3조 제5항 관련)",
    "민법 제255조(다른법 제3조 제5항 관련) 및 다른법 제3조 제5항",
    "민법 제254조(다른법 제3조 제5항 관련)",
    "민법 제255조(다른법 제3조 제5항 관련",  # unbalanced
])
def test_unknown_detached_or_separate_claim_is_not_hidden(claim):
    ev = source("민법 제255조(다른법 제3조 제5항 관련)")
    a = answer(claim, ev)
    assert not audit_citations(a).is_valid
    assert audit_answer(a).issues
    assert "다른법 제3조" in answer_citation_scan_text(claim, (ev,))


def test_title_does_not_grant_cross_reference_or_hide_following_bad_paragraph():
    ev = source("민법 제255조(민법 제9조 제5항 관련)")
    raw = ev.citation + ". 민법 제255조 제5항에 따릅니다."
    assert audit_citations(answer(raw, ev)).is_valid
    issues = audit_answer(answer(raw, ev)).by_kind("paragraph")
    assert len(issues) == 1 and issues[0].text in raw
    assert not audit_citations(answer("민법 제9조", ev)).is_valid


@pytest.mark.parametrize("format", ["{law} {article}", "**{law} {article}**",
    "**{law}** **{article}**", "__{law}__ __{article}__",
    "*{law}* *{article}*", "***{law}*** ***{article}***", "「{law}」 **{article}**"])
def test_split_emphasis_links_exact_law_with_ambiguous_article_number(format):
    ev = source("민법 제3조")
    other = source("주택임대차보호법 제3조", "housing")
    raw = "근거: " + format.format(law="민법", article="제3조") + "에 따릅니다."
    a = answer(raw, other, ev)
    assert audit_citations(a).is_valid
    spans = display_extras(a, raw, [])['citations']
    assert len(spans) == 1 and spans[0]['chunk_id'] == 'civil'
    fragment = raw[spans[0]['start']:spans[0]['end']]
    assert fragment.startswith("민법") and fragment.endswith("제3조")
    scan_fragment = citation_scan_text(raw)[spans[0]['start']:spans[0]['end']]
    assert ''.join(scan_fragment.split()) == "민법제3조"


def test_repeated_and_adjacent_citations_have_separate_display_spans():
    ev = source("민법 제3조")
    other = source("주택임대차보호법 제3조", "housing")
    raw = "**민법** **제3조** 및 **주택임대차보호법** **제3조**. 민법 제3조"
    assert audit_citations(answer(raw, ev, other)).is_valid
    spans = build_citation_spans(raw, (ev, other))
    assert [s['chunk_id'] for s in spans] == ['civil', 'housing', 'civil']
    assert all(a['end'] <= b['start'] for a, b in zip(spans, spans[1:]))


def test_unsupported_explicit_law_never_falls_back_to_another_law():
    ev = source("민법 제3조")
    assert not build_citation_spans("**다른법** **제3조**", (ev,))
    assert build_citation_spans("**제3조**", (ev,))[0]['chunk_id'] == 'civil'
    assert not build_citation_spans("제3조", (ev, source("다른법 제3조", "other")))


@pytest.mark.parametrize("label", ["민법 제255조(제목) 및 다른법 제3조",
    "민법 제255조(미완성", "민법 제255조 및 제3조(제목)"])
def test_ambiguous_label_does_not_produce_a_source_link(label):
    from src.generation.source_links import citation_url
    assert citation_url(label, "law") == ""


def test_split_formatted_title_and_independent_citation_have_distinct_roles():
    ev = source("민법 제255조(「다른법」 제3조 제5항 관련)")
    raw = "**민법** **제255조**(「**다른법**」 **제3조** 제5항 관련). 다른법 제3조 제5항"
    a = answer(raw, ev)
    assert [m.supported for m in audit_citations(a).mentions] == [True, False]
    assert len(audit_answer(a).by_kind('paragraph')) == 1
    spans = build_citation_spans(raw, (ev,))
    assert len(spans) == 1 and spans[0]['chunk_id'] == 'civil'


@pytest.mark.parametrize("entrypoint", ["chain", "graph"])
@pytest.mark.parametrize("extra,status", [("", "answered"), (". 다른법 제3조 제5항", "abstained")])
def test_copied_source_runtime_and_display(entrypoint, extra, status):
    from src.generation import chain, graph
    from src.generation.llm import get_llm
    ev = source("민법 제255조(다른법 제3조 제5항 관련)")
    class Service:
        def search(self, question, **kwargs):
            return RetrievalResult(question, civil_laws=[ev])
    raw = f"**{ev.citation}**에 따릅니다{extra}."
    fn = chain.answer_question if entrypoint == 'chain' else graph.answer_question
    result = fn("임차권등기 비용을 청구할 수 있나요?", service=Service(),
                llm=get_llm(fake_responses=[raw, "PASS"]))
    assert result.status == status
    spans = display_extras(result, result.text, [])['citations']
    assert len(spans) == (1 if status == 'answered' else 0)
