"""Python 3.11 항목별 검증. 프로젝트 루트에서 python scripts/verify_project.py --help."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("tests", "agency", "ollama", "ocr", "document")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", choices=STAGES, default=list(STAGES),
                        help="실행할 항목. 기본값: 전체")
    parser.add_argument("--pdf", type=Path, default=Path.home() / "Downloads/부동산등기부등본.pdf",
                        help="실제 등기 PDF 경로")
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/verification",
                        help="결과 저장 상위 폴더. 실행마다 새 하위 폴더 생성")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        parser.error(f"Python 3.11이 필요합니다. 현재: {sys.version.split()[0]}")
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    # 실물 문서는 외부로 전송하지 않는다. 셸/.env 원격 설정보다 우선한다.
    os.environ["JEONSEON_LLM_BASE_URL"] = "http://localhost:11434"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)

    directory = args.output.expanduser().resolve() / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    directory.mkdir(parents=True, exist_ok=False)
    report = {"python": sys.version.split()[0], "project": str(ROOT),
              "selected": args.only, "endpoint": "http://localhost:11434", "results": []}

    def save():
        (directory / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [f"Python {report['python']}", f"Project: {ROOT}"]
        for item in report["results"]:
            lines.append(f"\n[{item['status']}] {item['item']}")
            lines.extend(f"  {k}: {v}" for k, v in item.items() if k not in {"status", "item"})
        (directory / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def record(name, status, **details):
        report["results"].append({"item": name, "status": status, **details})
        save()
        print(f"[{status}] {name}", flush=True)
        for key, value in details.items():
            print(f"  {key}: {value}", flush=True)

    print(f"Python {report['python']} / 결과: {directory}", flush=True)
    print("PASS는 실행·자동 검증 통과입니다. 법률 답변 및 OCR 정확도 전수 검증은 아닙니다.", flush=True)
    save()

    def tests():
        env = os.environ.copy()
        env["REGISTRY_SAMPLE_PDF"] = str(args.pdf.expanduser().resolve())
        command = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   f"--junitxml={directory / 'pytest.xml'}"]
        print("전체 테스트 진행 중. pytest.log에서 실시간 로그를 확인할 수 있습니다.", flush=True)
        with (directory / "pytest.log").open("w", encoding="utf-8") as log:
            with subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT) as proc:
                try:
                    code = proc.wait()
                except KeyboardInterrupt:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    raise
        counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "SKIP": 0}
        cases = []
        if (directory / "pytest.xml").exists():
            for case in ET.parse(directory / "pytest.xml").iter("testcase"):
                status = next((label for tag, label in (("error", "ERROR"), ("failure", "FAIL"), ("skipped", "SKIP"))
                               if case.find(tag) is not None), "PASS")
                counts[status] += 1
                cases.append({"test": case.get("classname", "") + "::" + case.get("name", ""), "status": status})
        (directory / "test-cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "test-cases.txt").write_text(
            "\n".join(f"[{c['status']}] {c['test']}" for c in cases) + "\n", encoding="utf-8")
        record("전체 자동 테스트", "PASS" if code == 0 else "FAIL", exit_code=code,
               junit_counts=counts, note="하위 테스트 집계는 pytest.log 최종 요약을 함께 확인하세요.")

    def agency():
        from src.generation.citation import extract_citation_mentions
        from src.retrieval.service import Evidence
        for name in ("조정위원회", "심의위원회", "분쟁조정위원회", "국세청"):
            phrase = f"{name}에 신청해야 하며, 조정 절차 및 효력 등에 대한 안내를 받을 수 있습니다."
            mentions = extract_citation_mentions(phrase, ())
            found = [m.text for m in mentions if m.kind == "guide"]
            record(f"절차 설명 오탐: {name}", "FAIL" if found else "PASS", sentence=phrase,
                   expected="guide 인용 없음", detected=found)
        for name in ("국세청", "분쟁조정위원회"):
            text = f"{name} 안내에 따르면 신청할 수 있습니다."
            ev = Evidence(rank=1, chunk_id="test-guide", doc_type="guide", citation=f"{name} 안내",
                          text="신청 안내", score=1.0, source_url="https://example.com")
            for supplied in (False, True):
                mentions = [m for m in extract_citation_mentions(text, (ev,) if supplied else ()) if m.kind == "guide"]
                ok = len(mentions) == 1 and mentions[0].text == name and mentions[0].supported == supplied
                record(f"실제 자료 인용: {name} / 근거 {supplied}", "PASS" if ok else "FAIL",
                       detected=[{"text": m.text, "supported": m.supported} for m in mentions])

    service = None
    extraction = None

    def get_service():
        nonlocal service
        if service is None:
            from src.generation.chain import get_default_service
            service = get_default_service()
        return service

    def generate(question, evidences=None):
        from unittest.mock import patch
        from src.generation import chain, graph
        from src.generation import llm
        checks = []
        original = chain.audit_answer

        def capture(answer, *a, **kw):
            result = original(answer, *a, **kw)
            # 문서 질문의 원문·개인정보는 결과 파일에 남기지 않는다.
            checks.append({"stage": "semantic" if kw.get("semantic_judge") else "deterministic",
                           "issues": [{"kind": i.kind, "detail": i.detail,
                                       **({"text": i.text} if evidences is None else {})} for i in result.issues]})
            return result

        current_service = get_service()
        start = time.perf_counter()
        with patch.object(chain, "audit_answer", capture):
            answer = (graph.answer_question(question, service=current_service) if evidences is None
                      else graph.answer_document_question(question, evidences, service=current_service))
        record(question, "PASS" if answer.status == "answered" else "FAIL", answer_status=answer.status,
               seconds=round(time.perf_counter() - start, 2), validation=checks,
               configured_max_tokens=llm.LLM_MAX_TOKENS, num_ctx=llm.LLM_NUM_CTX,
               token_note="위 값은 전역 설정입니다. 실제 호출 상한은 Ollama 성능 로그의 max_tokens를 확인하세요.",
               **({"answer": answer.text} if evidences is None else {"answer_chars": len(answer.text)}))

    def ollama():
        for question in ("대항력은 언제부터 생기나요?", "임대인과 분쟁이 생겼을 때 소송 말고 조정을 신청할 수 있나요?"):
            try:
                generate(question)
            except Exception as exc:
                record(question, "ERROR", error_type=type(exc).__name__)

    def ocr():
        nonlocal extraction
        from src.document_check.upload_analysis import analyze_uploaded_document
        path = args.pdf.expanduser().resolve()
        if not path.is_file():
            record("실물 등기 PDF OCR", "ERROR", reason="PDF 파일이 없습니다. --pdf로 지정하세요.")
            return
        start = time.perf_counter()
        result = analyze_uploaded_document(path.name, path.read_bytes())
        extraction = result.extraction
        ok = extraction.page_count > 0 and extraction.unreadable_page_count == 0 and result.classification.kind == "registry"
        record("실물 등기 PDF OCR", "PASS" if ok else "FAIL", pages=extraction.page_count,
               methods=[p.method for p in extraction.pages], unreadable=extraction.unreadable_page_count,
               kind=result.classification.kind, analysis_status=getattr(result.analysis, "status", None),
               seconds=round(time.perf_counter() - start, 2))

    def document():
        if extraction is None:
            ocr()
        if extraction is None or extraction.unreadable_page_count == extraction.page_count:
            record("문서 답변", "ERROR", reason="사용 가능한 문서 추출 결과가 없습니다.")
            return
        from src.document_check.session_retrieval import build_session_document_context, SessionDocumentRetriever
        context = build_session_document_context(args.pdf.name, extraction, "local-verification")
        retriever = SessionDocumentRetriever(context)
        evidences = tuple(retriever.search("발급일", k=3) or retriever.first_pages(k=2))
        generate("업로드한 등기부 등본의 발급일을 알려줘", evidences)

    actions = {"tests": tests, "agency": agency, "ollama": ollama, "ocr": ocr, "document": document}
    try:
        for stage in dict.fromkeys(args.only):
            print(f"\n=== {stage} ===", flush=True)
            try:
                actions[stage]()
            except Exception as exc:
                record(stage, "ERROR", error_type=type(exc).__name__)
    except KeyboardInterrupt:
        record("사용자 중단", "ERROR", reason="완료된 항목까지 저장했습니다.")
    failures = sum(r["status"] in {"FAIL", "ERROR"} for r in report["results"])
    print(f"\n완료: {len(report['results'])}개 항목 / 실패·오류 {failures}개\n결과: {directory}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
