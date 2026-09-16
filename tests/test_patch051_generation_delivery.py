"""Independent delivery checks at the real graph/LCEL/native request boundary.

The HTTP response is fake; prompt formatting and native request serialization
are real. These tests establish delivery, not a model's legal correctness.
"""
import json
from pathlib import Path

import pytest

from src.generation import chain, graph, llm
from src.retrieval.service import Evidence, RetrievalResult


QUESTION = "전입신고만 하고 확정일자를 안 받으면 어떻게 되나요?"
ROOT = Path(__file__).resolve().parents[1]
ANSWER = (
    "주택임대차보호법 제3조에 따르면 주택의 인도와 주민등록을 마친 "
    "그 다음 날부터 대항력이 생깁니다."
)


def _core_evidences():
    rows = [json.loads(line) for line in
            (ROOT / "data/sample/chunks_expanded.jsonl").read_text(encoding="utf-8").splitlines()]
    found = []
    for article in ("주택임대차보호법-제3조", "주택임대차보호법-제3조의2"):
        chunk, = [row for row in rows if row["metadata"]["article_id"] == article]
        found.append(Evidence(
            rank=len(found) + 1, chunk_id=chunk["chunk_id"], doc_type="law",
            citation=chunk["text"].split("\n", 1)[0].strip("[]"),
            text=chunk["text"], score=1.0, source_url=chunk["metadata"]["source_url"],
        ))
    return found


class SuppliedEvidenceService:
    """Isolate delivery from ranking while honoring the actual top-k contract."""

    def __init__(self, laws):
        self.laws = laws
        self.calls = []

    def search(self, question, k_law=5, k_case=5, k_guide=2):
        self.calls.append((question, k_law, k_case, k_guide))
        return RetrievalResult(question=question, laws=self.laws[:k_law])


def _capture_native_requests(monkeypatch):
    payloads = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"message": {"content": ANSWER}, "done": True}).encode()

    def urlopen(request, timeout):
        payloads.append(json.loads(request.data.decode("utf-8")))
        return Response()

    monkeypatch.setattr(llm, "_select_ollama_base", lambda *args, **kwargs: "http://test.invalid")
    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    return payloads


@pytest.mark.parametrize("entrypoint,options", [
    (chain.answer_question, {}),
    (graph.answer_question, {}),
    (graph.answer_question, {"response_style": "brief"}),
])
def test_both_core_articles_and_full_paragraph_two_reach_native_request(monkeypatch, entrypoint, options):
    laws = _core_evidences()
    service = SuppliedEvidenceService(laws)
    payloads = _capture_native_requests(monkeypatch)

    entrypoint(
        QUESTION, service=service, llm=llm.get_llm(),
        auxiliary_llm=llm.get_llm(fake_responses=["PASS"]), **options,
    )

    assert service.calls == [(QUESTION, 3, 0, 2)]
    assert len(payloads) == 1
    messages = payloads[0]["messages"]
    human = next(message["content"] for message in messages if message["role"] == "user")
    assert QUESTION in human
    for evidence in laws:
        assert evidence.text in human
    paragraph_two = laws[1].text.split("②", 1)[1].split("③", 1)[0].strip()
    assert "확정일자" in paragraph_two and "우선하여 보증금을 변제" in paragraph_two
    assert paragraph_two in human
    assert payloads[0]["options"]["num_ctx"] == llm.LLM_NUM_CTX


def test_visible_article_citation_does_not_prove_its_required_paragraph_was_supplied(monkeypatch):
    """A title-only/partial chunk must not be mistaken for complete evidence."""
    full = _core_evidences()[1]
    partial = Evidence(
        rank=1, chunk_id="partial-law-3-2", doc_type="law", citation=full.citation,
        text=full.text.split("②", 1)[0].rstrip(), score=1.0, source_url=full.source_url,
    )
    payloads = _capture_native_requests(monkeypatch)
    graph.answer_question(
        QUESTION, service=SuppliedEvidenceService([partial]), llm=llm.get_llm(),
        auxiliary_llm=llm.get_llm(fake_responses=["PASS"]),
    )
    human = next(message["content"] for message in payloads[0]["messages"] if message["role"] == "user")
    assert partial.citation in human
    assert partial.text in human
    assert "상의 확정일자" not in human


def _official_snapshot_chunks():
    """Build a portable BM25 corpus from the repository's official snapshots."""
    from src.ingestion.fetch_law_mock import LAWS, parse_articles, parse_law_header
    from src.retrieval.expanded import CIVIL_IDS

    root = ROOT / "data/sources/server-v1"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    chunks = []
    import hashlib

    specs = [(name, date, kind) for name, _, date, kind, _ in LAWS]
    specs.append(("민법", "20260317", "법률"))
    for name, date, kind in specs:
        path = root / f"{name.replace(' ', '')}-{date}.txt"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["files"][path.name]["sha256"]
        text = path.read_text(encoding="utf-8")
        header = parse_law_header(text)
        for number, title, body in parse_articles(text):
            article = name + "-" + number
            if name == "민법" and article not in CIVIL_IDS:
                continue
            chunks.append({
                "chunk_id": article + "#snapshot", "doc_id": name + "-" + date,
                "text": f"[{name} {number}({title})]\n{body}",
                "metadata": {
                    "title": name, "article_id": article, "article_no": number,
                    "article_title": title, "doc_type": "law" if kind == "법률" else "decree",
                    "status": "current", "version": header["proclamation_number"],
                    "effective_date": header["effective_from"],
                    "source_url": f"https://www.law.go.kr/법령/{name.replace(' ', '')}/{number}",
                },
            })
    return chunks


@pytest.mark.parametrize("question", [
    QUESTION,
    "대화 주제: 대항력\n질문 목적: 개념의 의미와 차이\n사용자 입력: " + QUESTION,
])
def test_real_bm25_product_search_delivers_both_articles_to_native_http(monkeypatch, question):
    from src.retrieval.expanded import ExpandedLawRetrievalService

    chunks = _official_snapshot_chunks()
    service = ExpandedLawRetrievalService(chunks)
    payloads = _capture_native_requests(monkeypatch)
    answer = graph.answer_question(
        question, service=service, llm=llm.get_llm(), response_style="standard",
        auxiliary_llm=llm.get_llm(fake_responses=["PASS"]),
    )

    by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    returned = {by_id[e.chunk_id]["metadata"]["article_id"]: e for e in answer.laws}
    assert len(answer.laws) == 3
    assert {"주택임대차보호법-제3조", "주택임대차보호법-제3조의2"} <= set(returned)
    assert len(payloads) == 1
    human = next(message["content"] for message in payloads[0]["messages"] if message["role"] == "user")
    assert question in human
    for article in ("주택임대차보호법-제3조", "주택임대차보호법-제3조의2"):
        assert returned[article].text in human
    paragraph_two = returned["주택임대차보호법-제3조의2"].text.split("②", 1)[1].split("③", 1)[0].strip()
    assert "확정일자" in paragraph_two and "우선하여 보증금을 변제" in paragraph_two
    assert paragraph_two in human
