from copy import deepcopy

import pytest

from scripts.patch027_context_tuning import (
    _communication_concept,
    _current_request,
    _reference_graph,
    civil_concepts,
    law_concepts,
    _validated_reference_graph,
)


def test_communication_requires_notice_purpose_and_delivery_action():
    assert _communication_concept("계약을 끝낸다고 내용증명으로 알리려 합니다")
    assert _communication_concept("보증금 돌려달라고 보낸 우편이 반송됐어요")
    assert not _communication_concept("보일러 수리 일정을 문자로 알렸어요")
    assert not _communication_concept("계약서와 문자를 보면 승소 확률을 알 수 있나요?")


def test_earlier_dialogue_does_not_trigger_current_notice_expansion():
    query = """이전 대화: 보증금 반환 요구를 내용증명으로 보냈습니다.
사용자 질문: 오늘은 보일러 수리 일정을 문자로 알려도 되나요?"""
    assert _current_request(query).startswith("오늘은")
    assert "의사표시 도달" not in " ".join(civil_concepts(query))


def test_returned_mail_requires_mail_context():
    assert any("공시송달" in term for term in civil_concepts("보증금 반환 우편이 돌아왔어요"))
    assert not any("공시송달" in term for term in civil_concepts("휴가 뒤 집으로 돌아왔어요"))


def test_general_domains_use_current_explicit_context():
    assert any("공공임대주택" in term for term in law_concepts("공공임대 계약 절차가 궁금해요"))
    assert any("민간임대주택" in term for term in law_concepts("등록민간임대 재계약 절차가 궁금해요"))
    assert not law_concepts("세금 계산 방법이 궁금해요")
    assert "확정일자 부여 현황" in " ".join(
        law_concepts("전입신고랑 확정일자는 언제 해야 하나요?")
    )
    assert "확정일자 우선변제" not in " ".join(
        law_concepts("재계약이면 확정일자를 다시 챙겨야 하나요?")
    )


def test_reference_graph_uses_verified_civil_act_adoption_body_only():
    def chunk(article, title, text):
        return {"chunk_id": article, "text": text,
                "metadata": {"title": "민법", "article_id": article,
                             "article_title": title, "article_no": article.split("-")[1],
                             "status": "current", "version": "법률 제1호",
                             "effective_date": "2026-01-01"}}
    chunks = [
        chunk("민법-제615조", "원상회복", "[민법 제615조] 원상회복"),
        chunk("민법-제654조", "준용규정", "[민법 제654조] 제615조의 규정은 임대차에 준용한다."),
        chunk("민법-제626조", "상환청구권", "[민법 제626조] 제615조와 표현만 함께 나온다."),
    ]
    graph, evidence = _reference_graph(chunks)
    assert graph["민법-제615조"] == {"민법-제654조"}
    assert graph["민법-제654조"] == {"민법-제615조"}
    assert not graph["민법-제626조"]
    assert evidence[0]["source"] == "민법-제654조"


@pytest.mark.parametrize("question", [
    "계약 해지 통지는 하지 않았습니다. 보일러 수리 일정을 문자로 통지했습니다.",
    "계약 해지 통지는 하지 않았지만 보일러 수리 일정은 문자로 통지했습니다.",
    "계약은 종료됐어요. 보일러 수리 일정을 문자로 통지했습니다.",
    '예문은 "계약 해지를 내용증명으로 알립니다"입니다. 저는 수리 문자만 보냈어요.',
    "“보증금 반환을 요구하는 문자”라는 예시를 읽었어요. 수리 일정을 알릴까요?",
    "계약 해지를 문자로 통지하지 않았습니다.",
    "보험 사고를 문자로 통지했습니다.",
])
def test_notice_does_not_join_denied_quoted_or_unrelated_actions(question):
    assert not _communication_concept(question)


@pytest.mark.parametrize("question", [
    "계약 해지를 문자로 통지했습니다.",
    "임대인이 계약 종료를 내용증명으로 알리려 합니다.",
    "보증금 반환을 요구하는 우편이 반송됐어요.",
    "보증금 돌려달라고 보낸 우편이 집주인한테 전달되지 않고 돌아왔어요.",
    "계약 해지를 통보하는 내용증명을 보냈으나 도달하지 않았습니다.",
])
def test_positive_notice_context_is_preserved(question):
    assert _communication_concept(question)


def test_public_generic_operator_does_not_imply_private_registration():
    terms = law_concepts("공공임대 임대사업자에게 계약 절차를 물어보려 합니다.")
    assert any("공공임대주택" in t for t in terms)
    assert not any("민간임대주택" in t for t in terms)
    mixed = law_concepts("공공임대와 등록민간임대 임대사업자의 계약 절차를 비교해 주세요.")
    assert any("공공임대주택" in t for t in mixed)
    assert any("민간임대주택" in t for t in mixed)


def _reference_fixture():
    from scripts.patch015_baseline import ROOT, read
    return [deepcopy(c) for c in read(ROOT / "data/eval/patch026-full/capture/new-chunks.json")
            if c["metadata"].get("article_id") in ("민법-제615조", "민법-제654조")]


@pytest.mark.parametrize("body", [
    "다른법 제615조의 규정은 임대차에 준용한다.",
    "「검증용다른법」 제615조의 규정은 임대차에 준용한다.",
    "본문에는 참조가 없다.\n[종전 제615조에서 이동]",
    "제615조의 규정은 임대차에 준용하지 않는다.",
    '“제615조의 규정은 임대차에 준용한다.”는 인용 예시다.',
    "제615조의 규정은 임대차에 준용한다.\n[알 수 없는 주석]",
    "제615조의1의 규정은 임대차에 준용한다.",
    "제615조 내지 제999999999조의 규정은 임대차에 준용한다.",
])
def test_reference_rejects_foreign_law_history_quotation_and_ambiguous_body(body):
    chunks = _reference_fixture()
    chunks[1]["text"] = "[민법 제654조(준용규정)]\n" + body
    assert not _reference_graph(chunks)[1]


@pytest.mark.parametrize("body", [
    "제615조의 규정은 임대차에 준용한다.",
    "민법 제615조의 규정은 임대차에 준용한다.",
    "제610조제1항, 제615조 내지 제617조의 규정은 임대차에 이를 준용한다.",
    "제615조의 규정은 임대차에 준용한다.\n[종전 제7조는 제14조로 이동 <2013. 12. 30.>]",
])
def test_reference_accepts_exact_adoption_and_known_history(body):
    chunks = _reference_fixture()
    chunks[1]["text"] = "[민법 제654조(준용규정)]\n" + body
    edges = _reference_graph(chunks)[1]
    assert [(e["source"], e["target"]) for e in edges] == [("민법-제654조", "민법-제615조")]


@pytest.mark.parametrize("field,value", [
    ("version", "법률 제999호"), ("effective_date", "2020-01-01"),
    ("status", "historical"), ("article_no", "제615조의1"),
])
def test_reference_rejects_target_edition_or_identity_mismatch(field, value):
    chunks = _reference_fixture()
    chunks[0]["metadata"][field] = value
    assert not _reference_graph(chunks)[1]


def test_report_rebuilds_reference_endpoints_even_if_body_hash_is_unchanged():
    from scripts.patch015_baseline import ROOT, read
    chunks = read(ROOT / "data/eval/patch026-full/capture/new-chunks.json")
    _, edges = _reference_graph(chunks)
    audit = {"anchors": read(ROOT / "data/eval/patch027-full-ranking/audit.json")["anchors"],
             "reference_evidence": edges}
    assert _validated_reference_graph(audit)["민법-제654조"] == {"민법-제615조"}
    original_hash = edges[0]["body_sha256"]
    edges[0]["target"] = "민법-제626조"
    assert edges[0]["body_sha256"] == original_hash
    with pytest.raises(ValueError, match="Reference endpoints"):
        _validated_reference_graph(audit)


@pytest.mark.parametrize("field,value", [
    ("rank", 2), ("citation", "다른법 제615조"), ("source_url", "https://example.com/wrong"),
    ("text_sha256", "0" * 64), ("chunk_id", "wrong-id"),
])
def test_live_verification_rejects_rank_body_source_changes(field, value):
    from scripts.patch027_context_live import validate_live_row
    identity = {"chunk_id": "civil-615", "doc_type": "law", "citation": "민법 제615조",
                "source_url": "https://www.law.go.kr/", "text_sha256": "1" * 64}
    catalog = {"civil-615": identity}
    expected = {"laws": [], "civil_laws": ["civil-615"], "cases": [], "guides": []}
    row = {"laws": [], "civil_laws": [{**identity, "rank": 1}], "cases": [], "guides": []}
    validate_live_row(row, expected, catalog)
    row["civil_laws"][0][field] = value
    with pytest.raises(ValueError):
        validate_live_row(row, expected, catalog)


def _bundle_copy(tmp_path):
    import shutil
    from scripts.patch027_context_tuning import BUNDLE
    shutil.copytree(BUNDLE, tmp_path / "bundle")
    return tmp_path / "bundle"


def _refresh_manifest(bundle):
    from scripts.patch015_baseline import read, write, sha
    manifest = read(bundle / "manifest.json")
    write(bundle / "manifest.json", {p: sha(bundle / p) for p in manifest})


def test_corrected_frozen_capture_replays_with_live_evidence():
    from scripts.patch027_context_tuning import check
    result = check()
    joint = result["joint"]["context_both+context_reference"]
    assert joint["groups"]["question_only"]["union_all_required"] == {"hits": 43, "n": 75}
    assert joint["groups"]["context_diagnostic"]["union_all_required"] == {"hits": 42, "n": 75}
    assert len(joint["losses"]) == 8
    assert not joint["adoption"]["passed"]


@pytest.mark.parametrize("filename", ["execution-spec.json", "live-verification.json", "evidence-catalog.json"])
def test_incomplete_live_bundle_fails_even_when_manifest_entry_is_removed(tmp_path, filename):
    from scripts.patch015_baseline import read, write
    from scripts.patch027_context_tuning import check
    bundle = _bundle_copy(tmp_path)
    (bundle / filename).unlink()
    manifest = read(bundle / "manifest.json")
    del manifest[filename]
    write(bundle / "manifest.json", manifest)
    with pytest.raises(ValueError, match="Incomplete bundle"):
        check(bundle)


def test_complete_replay_rejects_forged_edge_with_refreshed_manifest(tmp_path):
    from scripts.patch015_baseline import read, write
    from scripts.patch027_context_tuning import check
    bundle = _bundle_copy(tmp_path)
    audit = read(bundle / "audit.json")
    audit["reference_evidence"][0]["target"] = "민법-제626조"
    write(bundle / "audit.json", audit)
    _refresh_manifest(bundle)
    with pytest.raises(ValueError, match="Reference endpoints"):
        check(bundle)


def test_complete_replay_rejects_wrong_live_body_with_refreshed_manifest(tmp_path):
    from scripts.patch015_baseline import read, write
    from scripts.patch027_context_tuning import check
    bundle = _bundle_copy(tmp_path)
    live = read(bundle / "live-verification.json")
    live["rows"][0]["candidate"]["laws"][0]["text_sha256"] = "0" * 64
    write(bundle / "live-verification.json", live)
    _refresh_manifest(bundle)
    with pytest.raises(ValueError, match="Live body/source"):
        check(bundle)


def test_finalist_cannot_be_changed_after_measurement(tmp_path):
    from scripts.patch015_baseline import read, write
    from scripts.patch027_context_tuning import check
    bundle = _bundle_copy(tmp_path)
    audit = read(bundle / "audit.json")
    audit["final_policies"][1] = "context_both"
    write(bundle / "audit.json", audit)
    _refresh_manifest(bundle)
    with pytest.raises(ValueError, match="Policies changed"):
        check(bundle)
