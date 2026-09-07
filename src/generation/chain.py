"""LCEL 생성 runtime — 사전 안전성 검사 → Retrieval → Qwen → 최종 검증.

사용자에게 나가는 답은 세 갈래로만 끝난다.

  refused   : 프롬프트 인젝션 또는 서비스 범위 밖 질문. Retrieval 전에 끝낸다.
  abstained : 근거가 없거나 생성·검증을 통과하지 못한 답변.
  answered  : 검색 근거를 바탕으로 만든 최종 문장이 검증까지 통과한 답변.

검색은 `src.retrieval.service.RetrievalService`의 공개 경계만 사용한다. 검색기 구현이나
검색 개수 정책은 이 파일에서 바꾸지 않는다. 생성 runtime은 B 파트의 deterministic
검사와 Qwen semantic judge를 연결하고, 사용자에게 실제로 보낼 `raw_text` 자체를
최종 검증한다.

기본 흐름:

    secret/PII masking
      → prompt-injection hard guard (+ 애매한 경우 Qwen judge)
      → scope hard guard (+ 애매하거나 다른 도메인일 때만 Qwen judge)
      → 질문 유형 판별
      → 법령·기관 안내 1차 Retrieval
      → 1차 근거가 부족하거나 판례 요청일 때만 판례 Retrieval
      → main Qwen answer (정확성 우선 + 쉬운 표현까지 한 번에 생성)
      → citation/deterministic validation
      → Qwen semantic judge
      → PASS: answered / FAIL: abstained

`build_qa_chain()`은 프롬프트와 main LLM만 묶는 저수준 체인이다. 사용자 요청을
처리하는 안전한 진입점은 `answer_question()`이다.
"""

from __future__ import annotations

from dataclasses import replace
import logging

from pathlib import Path

from typing import Callable

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda

from src.document_check.privacy import mask_sensitive_text
from src.document_check.session_retrieval import SessionDocumentEvidence
from src.generation import llm as llm_module
from src.generation import prompt as prompt_module
from src.generation.abstention import (
    SCOPE_JUDGE_SYSTEM,
    build_scope_judge_prompt,
    classify_scope,
)
from src.generation.llm import clean_output, get_llm
from src.generation.models import Answer
from src.generation.evidence_routing import retrieve_staged
from src.generation.validation import (
    DOCUMENT_SEMANTIC_JUDGE_SYSTEM,
    SEMANTIC_JUDGE_SYSTEM,
    SemanticJudgement,
    audit_answer,
    build_semantic_judge_prompt,
    ground_answer_conditions,
    requires_semantic_validation,
)
from src.retrieval.service import Evidence, RetrievalResult, RetrievalService
from src.security.prompt_injection import (
    PROMPT_INJECTION_JUDGE_SYSTEM,
    build_prompt_injection_judge_prompt,
    classify_prompt_injection,
)
from src.security.secret_filter import redact_secrets

logger = logging.getLogger(__name__)

# ★ 검색이 주는 기본값(법령 5 · 판례 5)보다 적게 쓴다. 측정 결과다.
#
#   질문                  법령5·판례5              법령3·판례2
#   임차권등기명령 시기    "임대차 기간 내에" 오답   "임대차가 끝난 후" 정답
#   임차권등기 비용       "임차인 부담" 오답        "임대인에게 청구" 정답
#   경매 우선변제         47자에서 잘림            확정일자·금액 구간까지 정답
#
# 5+5 는 컨텍스트가 2,200토큰이 되는데 8B 양자화 모델은 그 안에서 초점을 잃는다.
# 눈앞의 제3조의3 ⑧을 두고 "명시적 규정이 없다"고 답한 경우까지 있었다.
#
# 대가는 정답 조문이 근거에 아예 안 들어오는 경우가 는다는 것이다(dev 27문항
# 기준 dev-003·dev-008). "흐릿한 5건"보다 "제대로 읽는 3건"이 낫다는 판단이고,
# 더 큰 모델(14B 이상)로 바꾸면 다시 올려 재측정해야 한다.
DEFAULT_K_LAW = 3
DEFAULT_K_CASE = 2

# 공식 안내는 상한이다. 검색이 질문 주제일 때만 0~2건을 내므로 무관한 질문에는
# 따라붙지 않는다. 법적 근거가 아니라 실무 절차 자료라 법령·판례와 별도로 센다.
DEFAULT_K_GUIDE = 2

_SETUP_HINT = (
    "검색 인덱스를 찾지 못했습니다. docs/retrieval-handoff.md 3절 순서대로 준비하세요.\n"
    "  python -m src.ingestion.fetch_law_mock --records data/parsed/law_records.jsonl\n"
    "  python -m src.ingestion.load_laws --records data/parsed/law_records.jsonl "
    "--export data/chunks/chunks.jsonl\n"
    "  python scripts/load_case_only_demo_corpus.py\n"
    "  python -m src.ingestion.fetch_guides --records data/parsed/guide_records.jsonl\n"
    "  python -m src.ingestion.load_guides --records data/parsed/guide_records.jsonl "
    "--export data/chunks/guides.jsonl\n"
    "  python -m src.retrieval.index --chunks data/chunks/chunks.jsonl "
    "--path data/index/chroma_kurev1_1024\n"
    "  python -m src.retrieval.index --chunks data/chunks/cases.jsonl "
    "--path data/index/chroma_kurev1_1024\n"
    "  python -m src.retrieval.index --chunks data/chunks/guides.jsonl "
    "--path data/index/chroma_kurev1_1024"
)

_service: RetrievalService | None = None


# ── 검색 진입점 ────────────────────────────────────────────────

def get_default_service() -> RetrievalService:
    """검색 서비스를 한 번만 만들어 재사용한다.

    KURE-v1 이 2.3GB 라 질의마다 올리면 쓸 수 없다. Streamlit 에서는 이 함수
    대신 `@st.cache_resource` 로 감싼 팩토리를 쓰고 그 결과를 인자로 넘긴다.

    ★ 인덱스를 못 열면 어휘 검색만으로 동작하고, 그 상태가 이 캐시에 그대로
      굳는다. 품질이 떨어진 줄 모르고 답변을 평가하는 것이 가장 찾기 어려운
      문제라 로그를 남긴다. 인덱스를 만든 뒤에는 reset_default_service() 를
      부르거나 프로세스를 다시 띄워야 반영된다.
    """
    global _service
    if _service is None:
        _service = _build_service()
    return _service


def fallback_chunk_paths() -> tuple:
    """인덱스 없이 뜰 때 읽을 청크 파일들.

    ★ 검색의 `from_index` 기본값과 같아야 한다. 어긋나면 특정 묶음만 조용히
      빠진 채로 서비스가 뜬다. 안내(guide)가 추가됐을 때 실제로 그랬다.
      `tests/test_generation_chain.py`의 FallbackCorpusTests 가 두 값을 대조한다.
    """
    from src.retrieval.service import CASE_CHUNKS, GUIDE_CHUNKS, LAW_CHUNKS

    return (LAW_CHUNKS, CASE_CHUNKS, GUIDE_CHUNKS)


def _build_service() -> RetrievalService:
    try:
        return RetrievalService.from_index()
    except Exception as error:
        # 인덱스 없음·패키지 미설치·메모리 부족·검색팀 코드의 버그까지 함께
        # 삼키는 자리라, 트레이스백이 없으면 원인을 되짚을 수 없다.
        logger.warning(
            "Chroma 인덱스를 열지 못해 어휘 검색만 사용합니다: %s", error, exc_info=True
        )

    # 검색팀 모듈의 공개 이름만 쓴다. 밑줄로 시작하는 이름은 예고 없이 바뀐다.
    from src.retrieval.retriever import load_chunks

    chunks: list[dict] = []
    for path in fallback_chunk_paths():
        if Path(path).exists():          # 한쪽이 없어도 나머지로 동작해야 한다
            chunks.extend(load_chunks(path))

    if not chunks:
        raise RuntimeError(_SETUP_HINT)
    return RetrievalService(chunks, dense=None)


def reset_default_service() -> None:
    """테스트에서 캐시를 비울 때 쓴다."""
    global _service
    _service = None


# ── LCEL 체인 ─────────────────────────────────────────────────

def build_qa_chain(llm=None) -> Runnable:
    """prompt | llm | 문자열 파싱 | 후처리(사고 과정 제거 · 잘린 문장 다듬기).

    `clean_output` 을 체인 안에 두는 이유는, 체인을 직접 가져다 쓰는 쪽
    (앱의 스트리밍 등)도 같은 후처리를 거치게 하기 위해서다. 밖에서 하면
    부르는 곳마다 빠뜨릴 수 있다.
    """
    llm = llm if llm is not None else get_llm()
    return (
        prompt_module.build_qa_prompt()
        | llm
        | StrOutputParser()
        | RunnableLambda(clean_output)
    )


def build_document_qa_chain(llm=None) -> Runnable:
    """업로드 문서만 사용하는 질문을 위한 짧은 생성 체인."""

    llm = llm if llm is not None else get_llm()
    return (
        prompt_module.build_document_qa_prompt()
        | llm
        | StrOutputParser()
        | RunnableLambda(clean_output)
    )


# ── 질문 하나 처리 ─────────────────────────────────────────────

_PROMPT_INJECTION_NOTICE = (
    "시스템 지시를 바꾸거나 숨겨진 내부 지시를 요구하는 요청은 처리할 수 없습니다. "
    "주택임대차 관련 질문으로 다시 작성해 주세요."
)

_OUT_OF_SCOPE_NOTICE = (
    "주택임대차 관련 법령·판례·공식 기관 안내 범위에서 답할 수 없는 질문입니다. "
    "임대차 권리·절차와 관련된 질문으로 다시 작성해 주세요."
)

_MARKET_PRICE_NOTICE = (
    "부동산 시세나 실거래가 조회는 이 서비스의 답변 범위가 아닙니다. "
    "주택임대차 관련 권리·절차나 법적 근거를 질문해 주세요."
)

_VALIDATION_FAILED_TEXT = (
    "생성된 답변이 검색 근거와 일치하는지 충분히 확인하지 못해 답변을 보류했습니다. "
    "질문을 조금 더 구체적으로 바꿔 다시 물어봐 주세요."
)

_NO_DOCUMENT_AND_OFFICIAL_EVIDENCE_TEXT = (
    "첨부 문서 OCR에서 질문과 관련된 내용을 확인하지 못했습니다. "
    "필요한 페이지가 흐리거나 판독되지 않았을 수 있으니 더 선명한 파일로 다시 첨부하거나 "
    "문서명·항목명·쪽수를 포함해 질문해 주세요."
)

_DOCUMENT_FACT_TERMS = (
    "업로드문서",
    "첨부문서",
    "이문서",
    "해당문서",
    "계약서",
    "등기부",
    "등본",
    "등기사항",
    "특약",
    "보증금",
    "전세금",
    "월세",
    "차임",
    "관리비",
    "계약기간",
    "계약일",
    "잔금",
    "계약금",
    "중도금",
    "임대인",
    "임차인",
    "소유자",
    "소재지",
    "주소",
    "채권최고액",
    "근저당",
    "가압류",
    "압류",
    "신탁",
    "임차권",
    "발급일",
    "열람일",
)

_DOCUMENT_LEGAL_INTERPRETATION_TERMS = (
    "효력",
    "법적",
    "법률",
    "유효",
    "대항력",
    "우선변제",
    "순위",
    "보호받",
)


def _is_document_only_question(question: str) -> bool:
    """업로드 문서 요약만 필요하고 공식 법률 검색은 불필요한 질문인지 판별한다."""

    normalized = "".join((question or "").split())
    return (
        any(term in normalized for term in _DOCUMENT_FACT_TERMS)
        and not any(term in normalized for term in _DOCUMENT_LEGAL_INTERPRETATION_TERMS)
    )


def _safe_question(question: str) -> str:
    """LLM·검색·로그에 넘기기 전에 비밀정보와 개인정보를 가린다."""

    secret_masked = redact_secrets(question or "").text
    return mask_sensitive_text(secret_masked)


def _invoke_auxiliary_llm(llm, system_prompt: str, user_prompt: str) -> str:
    """분류·재작성·사후검증용 Qwen 호출을 한 형태로 묶는다."""

    # Qwen3는 짧은 PASS/FAIL 판정에서도 사고 과정을 먼저 생성할 수 있다.
    # native API의 think=false를 지원하지 않거나 무시하는 서버도 있으므로
    # main 답변과 동일하게 사용자 메시지 끝에도 /no_think를 명시한다.
    if llm_module.THINK_OFF:
        user_prompt = f"{user_prompt.rstrip()}\n\n/no_think"

    chain = (
        ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                ("human", "{input}"),
            ]
        )
        | llm
        | StrOutputParser()
        | RunnableLambda(clean_output)
    )
    return chain.invoke({"input": user_prompt}).strip()


def _parse_label(text: str, allowed: tuple[str, ...]) -> str:
    """분류기의 첫 비어 있지 않은 줄에서 허용된 label 하나를 읽는다."""

    for line in (text or "").splitlines():
        normalized = line.strip().upper().rstrip(".:：")
        if not normalized:
            continue
        for label in allowed:
            if normalized == label or normalized.startswith(f"{label} "):
                return label
        break
    raise ValueError(f"예상하지 못한 LLM 판정 출력: {text!r}")


def _scope_judge(llm) -> Callable[[str], bool]:
    def judge(question: str) -> bool:
        output = _invoke_auxiliary_llm(
            llm,
            SCOPE_JUDGE_SYSTEM,
            build_scope_judge_prompt(question),
        )
        return _parse_label(output, ("ALLOW", "REFUSE")) == "REFUSE"

    return judge


def _injection_judge(llm) -> Callable[[str], bool]:
    def judge(text: str) -> bool:
        output = _invoke_auxiliary_llm(
            llm,
            PROMPT_INJECTION_JUDGE_SYSTEM,
            build_prompt_injection_judge_prompt(text),
        )
        return _parse_label(output, ("ALLOW", "BLOCK")) == "BLOCK"

    return judge


def _semantic_judge(llm):
    def judge(
        question: str,
        answer_text: str,
        evidences: tuple[Evidence, ...],
        document_evidences: tuple[SessionDocumentEvidence, ...] = (),
    ) -> SemanticJudgement:
        # 공식 근거는 laws 슬롯에만 넣고 OCR 근거는 별도 필드로 둔다. 두 출처를
        # 검증 프롬프트에서도 섞지 않아야 법적 근거로 오인하지 않는다.
        probe = Answer(
            question=question,
            status="answered",
            text=answer_text,
            raw_text=answer_text,
            laws=tuple(evidences),
            document_evidences=document_evidences,
            requires_official_citation=bool(evidences),
        )
        judge_system = (
            DOCUMENT_SEMANTIC_JUDGE_SYSTEM
            if document_evidences and not evidences
            else SEMANTIC_JUDGE_SYSTEM
        )
        output = _invoke_auxiliary_llm(
            llm,
            judge_system,
            build_semantic_judge_prompt(probe),
        )
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            raise ValueError("semantic judge가 빈 결과를 반환했습니다.")

        label = _parse_label(lines[0], ("PASS", "FAIL"))
        detail = " ".join(lines[1:]).strip()
        return SemanticJudgement(
            supported=label == "PASS",
            detail=detail,
        )

    return judge


def _refused_answer(question: str, reason: str) -> Answer:
    if reason == "contract_safety_verdict":
        notice = prompt_module.NON_VERDICT_NOTICE
    elif reason == "market_price_lookup":
        notice = _MARKET_PRICE_NOTICE
    elif reason == "prompt_injection":
        notice = _PROMPT_INJECTION_NOTICE
    else:
        notice = _OUT_OF_SCOPE_NOTICE

    return Answer(
        question=question,
        status="refused",
        text=f"{notice}\n\n{prompt_module.DISCLAIMER}",
    )


def _abstained_after_validation(
    question: str,
    result: RetrievalResult,
    report,
    document_evidences: tuple[SessionDocumentEvidence, ...] = (),
    *,
    validation_mode: str = "deterministic",
) -> Answer:
    # OCR 문구와 생성 원문은 로그에 남기지 않는다. 운영 진단에는 오류 분류와
    # 공식 청크 식별자만 남긴다.
    issue_summary = [
        {"kind": issue.kind, "evidence_chunk_ids": issue.evidence_chunk_ids}
        for issue in report.issues
    ]
    logger.warning(
        "생성 답변 검증 실패: issues=%s document_evidence_count=%d",
        issue_summary,
        len(document_evidences),
    )
    return Answer(
        question=question,
        status="abstained",
        text=f"{_VALIDATION_FAILED_TEXT}\n\n{prompt_module.DISCLAIMER}",
        laws=tuple(result.laws),
        cases=tuple(result.cases),
        guides=tuple(result.guides),
        document_evidences=document_evidences,
        validation_mode=validation_mode,
    )


def answer_question(
    question: str,
    service: RetrievalService | None = None,
    llm=None,
    k_law: int = DEFAULT_K_LAW,
    k_case: int = DEFAULT_K_CASE,
    k_guide: int = DEFAULT_K_GUIDE,
    refuse_check: Callable[[str], bool] | None = None,
    auxiliary_llm=None,
    document_evidences: tuple[SessionDocumentEvidence, ...] = (),
    document_search_attempted: bool = False,
) -> Answer:
    """질문 하나를 사전 검사부터 사후 검증까지 처리한다.

    ``llm``은 실제 답변 생성 모델이고, ``auxiliary_llm``은 범위 분류와
    semantic validation에 쓰는 모델이다. 값을 주지 않으면 둘 다 같은 Qwen 설정의
    별도 클라이언트를 만든다. 테스트에서는 fake LLM을 각각 주입할 수 있다.

    prompt injection과 scope의 LLM 판정은 deterministic 단계가 semantic review가
    필요하다고 표시한 입력에만 호출한다. 명백한 임대차 질문은 scope Qwen을 생략한다.
    """

    safe_question = _safe_question(question)

    if not safe_question.strip():
        return Answer(
            question=safe_question,
            status="abstained",
            text=f"{prompt_module.NO_EVIDENCE_TEXT}\n\n{prompt_module.DISCLAIMER}",
        )

    # 1) 명백한 prompt injection은 LLM에 보여 주기 전에 코드로 차단한다.
    injection = classify_prompt_injection(safe_question)
    if injection.blocked:
        return _refused_answer(safe_question, "prompt_injection")

    runtime_aux_llm = auxiliary_llm if auxiliary_llm is not None else llm

    def get_aux_llm():
        nonlocal runtime_aux_llm
        if runtime_aux_llm is None:
            # 분류·검증은 창작이 필요 없으므로 main answer보다 짧게 제한한다.
            # 동일 Qwen을 쓰되 보조 호출의 폭주를 막는다.
            runtime_aux_llm = get_llm(
                temperature=0.0,
                max_tokens=160,
                timeout=90,
                max_retries=0,
            )
        return runtime_aux_llm

    # ambiguous injection만 Qwen으로 재검사한다. 일반 질문마다 한 번 더 부르지 않는다.
    if injection.needs_semantic_review:
        injection = classify_prompt_injection(
            safe_question,
            semantic_judge=_injection_judge(get_aux_llm()),
        )
        if injection.blocked:
            return _refused_answer(safe_question, "prompt_injection")

    # 2) scope hard guard. 개별 계약 안전성/시세는 Qwen 전에 즉시 REFUSE한다.
    scope = classify_scope(safe_question)
    if scope.out_of_scope:
        return _refused_answer(safe_question, scope.reason)

    # 기존 호출자가 별도 정책을 주입했다면 semantic scope judge 전에 비용 없이 적용한다.
    if refuse_check is not None and refuse_check(safe_question):
        return _refused_answer(safe_question, "custom_scope")

    # 명백한 임대차 질문은 scope Qwen을 생략한다. 범위가 애매하거나
    # 임대차 도메인 신호가 없는 경우에만 semantic judge를 호출한다. 다만 호출자가
    # 이미 세션 문서 참조를 확인한 질의는 OCR 근거를 읽는 요청이므로 재분류하지 않는다.
    if scope.needs_semantic_review and not document_search_attempted:
        scope = classify_scope(
            safe_question,
            semantic_judge=_scope_judge(get_aux_llm()),
        )
        if scope.out_of_scope:
            return _refused_answer(safe_question, scope.reason)

    # 3) 단계형 Retrieval. 법령·기관 안내를 먼저 찾고, 판례를 직접 요청했거나
    # 질문 유형에 맞는 1차 근거가 없을 때만 판례를 추가한다.
    # 문서 전용 질문은 상한이 모두 0으로 내려오므로 공식 검색을 아예 건너뛴다.
    if k_law <= 0 and k_case <= 0 and k_guide <= 0:
        result: RetrievalResult = RetrievalResult(question=safe_question)
    else:
        service = service if service is not None else get_default_service()
        routed = retrieve_staged(
            service,
            safe_question,
            k_law=k_law,
            k_case=k_case,
            k_guide=k_guide,
        )
        result = routed.result
        logger.info(
            "근거 라우팅: question_type=%s primary_sufficient=%s cases_added=%s",
            routed.route.question_type,
            routed.route.primary_sufficient,
            routed.route.cases_added,
        )

    if result.is_empty() and not document_evidences:
        no_evidence_text = (
            _NO_DOCUMENT_AND_OFFICIAL_EVIDENCE_TEXT
            if document_search_attempted
            else prompt_module.NO_EVIDENCE_TEXT
        )
        return Answer(
            question=safe_question,
            status="abstained",
            text=f"{no_evidence_text}\n\n{prompt_module.DISCLAIMER}",
        )

    # 4) main Qwen answer.
    # 네트워크/서버 오류 때 OpenAI client의 자동 재시도로 180초 timeout이
    # 여러 번 반복되지 않도록 runtime 기본 생성에서는 retry를 끈다.
    document_only = bool(document_evidences) and k_law <= 0 and k_case <= 0 and k_guide <= 0
    main_llm = (
        llm
        if llm is not None
        else get_llm(
            max_retries=0,
            **(
                {"max_tokens": max(384, llm_module.LLM_MAX_TOKENS)}
                if document_only
                else {}
            ),
        )
    )
    chain = build_document_qa_chain(main_llm) if document_only else build_qa_chain(main_llm)
    try:
        raw_text = chain.invoke(
            {
                "context": prompt_module.format_context(result, document_evidences),
                "question": safe_question,
            }
        )
    except Exception as error:
        logger.warning("LLM 호출이 실패했습니다: %s", error, exc_info=True)
        return Answer(
            question=safe_question,
            status="abstained",
            text=f"{prompt_module.GENERATION_FAILED_TEXT}\n\n{prompt_module.DISCLAIMER}",
            laws=tuple(result.laws),
            cases=tuple(result.cases),
            guides=tuple(result.guides),
            document_evidences=document_evidences,
        )

    if not raw_text.strip():
        logger.warning(
            "모델이 빈 답변을 반환했습니다. 사고 과정이 토큰 상한(%s)을 모두 "
            "소진했을 가능성이 큽니다. JEONSEON_LLM_MAX_TOKENS 를 늘리거나 "
            "사고 과정 비활성화를 확인하세요.",
            llm_module.LLM_MAX_TOKENS,
        )
        return Answer(
            question=safe_question,
            status="abstained",
            text=f"{prompt_module.GENERATION_FAILED_TEXT}\n\n{prompt_module.DISCLAIMER}",
            laws=tuple(result.laws),
            cases=tuple(result.cases),
            guides=tuple(result.guides),
            document_evidences=document_evidences,
        )

    evidences = tuple(result.laws + result.cases + result.guides)
    grounded_text = ground_answer_conditions(raw_text, evidences)
    if grounded_text != raw_text:
        logger.info("검색 근거의 시점 표현으로 생성 답변의 오기를 교정했습니다.")
        raw_text = grounded_text

    # 5) main Qwen이 정확성 우선 원칙과 쉬운 표현 규칙을 함께 적용해
    # 사용자에게 보낼 최종 본문을 직접 만든다. 별도 재작성 Qwen은 호출하지 않는다.
    candidate = Answer(
        question=safe_question,
        status="answered",
        text=f"{raw_text}\n\n{prompt_module.DISCLAIMER}",
        raw_text=raw_text,
        laws=tuple(result.laws),
        cases=tuple(result.cases),
        guides=tuple(result.guides),
        document_evidences=document_evidences,
        requires_official_citation=not result.is_empty(),
    )

    # 6) main Qwen이 만든 최종 본문을 deterministic citation/validation으로
    # 먼저 검사한다. 명확한 오류가 있으면 semantic judge까지 호출하지 않는다.
    report = audit_answer(candidate)
    if not report.is_valid:
        return _abstained_after_validation(
            safe_question, result, report, document_evidences
        )

    # 단일 법령의 단순 설명은 결정론적 검사로 끝낸다. 판례·기관 안내·복수 출처,
    # 숫자·시점·조건·예외처럼 의미 변형 위험이 있는 답변만 Qwen이 한 번 더 본다.
    if not requires_semantic_validation(candidate):
        logger.info("조건부 의미 검증 생략: 단일 법령의 단순 답변")
        return replace(candidate, validation_mode="deterministic")

    report = audit_answer(
        candidate,
        semantic_judge=lambda question, text, evidences: _semantic_judge(
            get_aux_llm()
        )(question, text, evidences, document_evidences),
    )
    if not report.is_valid:
        return _abstained_after_validation(
            safe_question,
            result,
            report,
            document_evidences,
            validation_mode="semantic",
        )

    return replace(candidate, validation_mode="semantic")


def answer_document_question(
    question: str,
    document_evidences: tuple[SessionDocumentEvidence, ...],
    **kwargs,
) -> Answer:
    """세션 OCR 근거와 기존 공식 검색 결과를 함께 생성 경계에 전달한다."""

    # 문서에 적힌 보증금·특약·당사자 등을 그대로 묻는 질문에 무관한 법령 검색을
    # 섞으면 작은 모델이 억지로 법령을 인용하고 semantic judge가 답을 거절한다.
    # 해석·위험 분석 질문은 기존 공식 검색을 유지한다.
    if not document_evidences or _is_document_only_question(question):
        kwargs.setdefault("k_law", 0)
        kwargs.setdefault("k_case", 0)
        kwargs.setdefault("k_guide", 0)

    return answer_question(
        question,
        document_evidences=document_evidences,
        document_search_attempted=True,
        **kwargs,
    )
