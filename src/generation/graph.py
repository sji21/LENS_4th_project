"""LENS Generation LangGraph workflow.

기존 Generation 정책과 판단 기준은 ``src.generation.chain`` 및 기존 기능 모듈에
그대로 둔다. 이 파일은 그 기능들을 LangGraph 노드로 연결해 질문 하나의 실행 순서,
상태 전달, 조건 분기를 실제로 담당한다.

실제 서비스 흐름:

    input_guard
      → prompt injection deterministic check
      → 필요 시 prompt injection semantic check
      → scope deterministic check
      → 필요 시 scope semantic check
      → retrieval
      → generation
      → grounding
      → deterministic validation
      → semantic validation
      → answered / abstained / refused

기존 ``chain.answer_question()``은 삭제하거나 수정하지 않는다. 기존 테스트·호환
경로로 그대로 남기고, Streamlit은 이미 이 모듈의 ``answer_question()``을 사용한다.
"""

from __future__ import annotations

from dataclasses import replace
import logging
from contextlib import nullcontext
from typing import Any, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from src.document_check.session_retrieval import SessionDocumentEvidence
from src.generation import chain as chain_module
from src.generation.evidence_routing import EvidenceRoute, retrieve_staged
from src.generation.models import Answer
from src.retrieval.service import RetrievalResult, RetrievalService


logger = logging.getLogger(__name__)


Route = Literal[
    "abstain_no_input",
    "injection_check",
    "injection_semantic",
    "scope_check",
    "scope_semantic",
    "retrieval",
    "generate",
    "grounding",
    "deterministic_validation",
    "semantic_validation",
    "refuse",
    "abstain_no_evidence",
    "abstain_generation",
    "abstain_validation",
    "answer",
]


class GenerationGraphState(TypedDict, total=False):
    """질문 하나를 처리하면서 LangGraph 노드 사이에 전달하는 상태."""

    safe_question: str
    injection: Any
    scope: Any
    retrieval_result: RetrievalResult
    evidence_route: EvidenceRoute | None
    raw_text: str
    candidate: Answer
    validation_report: Any
    validation_mode: str
    refusal_reason: str
    next_step: Route
    answer: Answer


def _next_step(state: GenerationGraphState) -> Route:
    return state["next_step"]


def build_generation_graph(
    *,
    service: RetrievalService | None = None,
    llm=None,
    k_law: int = chain_module.DEFAULT_K_LAW,
    k_case: int = chain_module.DEFAULT_K_CASE,
    k_guide: int = chain_module.DEFAULT_K_GUIDE,
    refuse_check: Callable[[str], bool] | None = None,
    auxiliary_llm=None,
    document_evidences: tuple[SessionDocumentEvidence, ...] = (),
    document_search_attempted: bool = False,
):
    """기존 Generation 정책을 그대로 사용하는 실행 Graph를 만든다.

    ``chain.answer_question()`` 안에 있던 판단 기준을 새로 정의하지 않는다.
    prompt injection, scope, Retrieval, prompt/LLM, grounding, validation은 기존
    함수와 상수를 그대로 호출하고 LangGraph는 실행 순서와 분기만 담당한다.
    """

    runtime_aux_llm = auxiliary_llm if auxiliary_llm is not None else llm

    def get_aux_llm():
        nonlocal runtime_aux_llm
        if runtime_aux_llm is None:
            # chain.answer_question()과 동일한 보조 Qwen 설정.
            runtime_aux_llm = chain_module.get_llm(
                temperature=0.0,
                max_tokens=160,
                timeout=90,
                max_retries=0,
            )
        return runtime_aux_llm

    def input_guard_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        question = state["safe_question"]
        return {
            "next_step": (
                "abstain_no_input"
                if not question.strip()
                else "injection_check"
            )
        }

    def abstain_no_input_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        question = state["safe_question"]
        return {
            "answer": Answer(
                question=question,
                status="abstained",
                text=(
                    f"{chain_module.prompt_module.NO_EVIDENCE_TEXT}\n\n"
                    f"{chain_module.prompt_module.DISCLAIMER}"
                ),
            )
        }

    def injection_check_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        question = state["safe_question"]
        injection = chain_module.classify_prompt_injection(question)

        if injection.blocked:
            return {
                "injection": injection,
                "refusal_reason": "prompt_injection",
                "next_step": "refuse",
            }

        if injection.needs_semantic_review:
            return {
                "injection": injection,
                "next_step": "injection_semantic",
            }

        return {
            "injection": injection,
            "next_step": "scope_check",
        }

    def injection_semantic_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        question = state["safe_question"]
        injection = chain_module.classify_prompt_injection(
            question,
            semantic_judge=chain_module._injection_judge(get_aux_llm()),
        )

        if injection.blocked:
            return {
                "injection": injection,
                "refusal_reason": "prompt_injection",
                "next_step": "refuse",
            }

        return {
            "injection": injection,
            "next_step": "scope_check",
        }

    def scope_check_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        question = state["safe_question"]
        scope = chain_module.classify_scope(question)

        if scope.out_of_scope:
            return {
                "scope": scope,
                "refusal_reason": scope.reason,
                "next_step": "refuse",
            }

        # 기존 answer_question()과 동일하게 custom refuse_check는
        # semantic scope judge보다 먼저 적용한다.
        if refuse_check is not None and refuse_check(question):
            return {
                "scope": scope,
                "refusal_reason": "custom_scope",
                "next_step": "refuse",
            }

        # Streamlit이 실제 세션 첨부 참조를 확인해 문서 경계로 보낸 요청은
        # "첨부한 등본 검토해줘"처럼 법률 용어가 짧아도 일반 범위 LLM으로
        # 재분류하지 않는다. deterministic 금지 질문과 custom guard는 위에서 유지한다.
        if scope.needs_semantic_review and not document_search_attempted:
            return {
                "scope": scope,
                "next_step": "scope_semantic",
            }

        return {
            "scope": scope,
            "next_step": "retrieval",
        }

    def scope_semantic_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        question = state["safe_question"]
        scope = chain_module.classify_scope(
            question,
            semantic_judge=chain_module._scope_judge(get_aux_llm()),
        )

        if scope.out_of_scope:
            return {
                "scope": scope,
                "refusal_reason": scope.reason,
                "next_step": "refuse",
            }

        return {
            "scope": scope,
            "next_step": "retrieval",
        }

    def refuse_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        return {
            "answer": chain_module._refused_answer(
                state["safe_question"],
                state["refusal_reason"],
            )
        }

    def retrieval_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        question = state["safe_question"]

        # 안전성/범위 검사를 통과한 뒤에만 검색 서비스를 만든다.
        # 기존 chain의 Retrieval-before/after 경계를 그대로 유지한다.
        # 문서 전용 질문은 상한이 모두 0으로 내려오므로 공식 검색을 건너뛴다.
        routed = None
        if k_law <= 0 and k_case <= 0 and k_guide <= 0:
            result = RetrievalResult(question=question)
        else:
            runtime_service = (
                service if service is not None
                else chain_module.get_default_service()
            )
            routed = retrieve_staged(
                runtime_service,
                question,
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

        # 문서 전용 질문은 공식 검색을 돌리지 않으므로 라우팅 결과가 없다.
        return {
            "retrieval_result": result,
            "evidence_route": routed.route if routed is not None else None,
            "next_step": (
                "abstain_no_evidence"
                if result.is_empty() and not document_evidences
                else "generate"
            ),
        }

    def abstain_no_evidence_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        question = state["safe_question"]
        no_evidence_text = (
            chain_module._NO_DOCUMENT_AND_OFFICIAL_EVIDENCE_TEXT
            if document_search_attempted
            else chain_module.prompt_module.NO_EVIDENCE_TEXT
        )
        return {
            "answer": Answer(
                question=question,
                status="abstained",
                text=(
                    f"{no_evidence_text}\n\n"
                    f"{chain_module.prompt_module.DISCLAIMER}"
                ),
                document_evidences=document_evidences,
            )
        }

    def generate_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        question = state["safe_question"]
        result = state["retrieval_result"]

        # 기존 runtime과 동일하게 main 생성 호출은 retry를 끈다.
        document_only = (
            bool(document_evidences)
            and k_law <= 0
            and k_case <= 0
            and k_guide <= 0
        )
        main_llm = (
            llm
            if llm is not None
            else chain_module.get_llm(
                max_retries=0,
                **(
                    {"max_tokens": max(384, chain_module.llm_module.LLM_MAX_TOKENS)}
                    if document_only
                    else {}
                ),
            )
        )
        qa_chain = (
            chain_module.build_document_qa_chain(main_llm)
            if document_only
            else chain_module.build_qa_chain(main_llm)
        )

        try:
            raw_text = qa_chain.invoke(
                {
                    "context": chain_module.prompt_module.format_context(
                        result, document_evidences
                    ),
                    "question": question,
                }
            )
        except Exception as error:
            logger.warning(
                "LLM 호출이 실패했습니다: %s",
                error,
                exc_info=True,
            )
            return {
                "next_step": "abstain_generation",
            }

        if not raw_text.strip():
            logger.warning(
                "모델이 빈 답변을 반환했습니다. 사고 과정이 토큰 상한(%s)을 모두 "
                "소진했을 가능성이 큽니다. JEONSEON_LLM_MAX_TOKENS 를 늘리거나 "
                "사고 과정 비활성화를 확인하세요.",
                chain_module.llm_module.LLM_MAX_TOKENS,
            )
            return {
                "next_step": "abstain_generation",
            }

        return {
            "raw_text": raw_text,
            "next_step": "grounding",
        }

    def abstain_generation_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        question = state["safe_question"]
        result = state["retrieval_result"]

        return {
            "answer": Answer(
                question=question,
                status="abstained",
                text=(
                    f"{chain_module.prompt_module.GENERATION_FAILED_TEXT}\n\n"
                    f"{chain_module.prompt_module.DISCLAIMER}"
                ),
                laws=tuple(result.laws),
                cases=tuple(result.cases),
                guides=tuple(result.guides),
                document_evidences=document_evidences,
            )
        }

    def grounding_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        question = state["safe_question"]
        result = state["retrieval_result"]
        raw_text = state["raw_text"]

        evidences = tuple(result.laws + result.cases + result.guides)
        grounded_text = chain_module.ground_answer_conditions(
            raw_text,
            evidences,
        )

        if grounded_text != raw_text:
            logger.info(
                "검색 근거의 시점 표현으로 생성 답변의 오기를 교정했습니다."
            )

        candidate = Answer(
            question=question,
            status="answered",
            text=(
                f"{grounded_text}\n\n"
                f"{chain_module.prompt_module.DISCLAIMER}"
            ),
            raw_text=grounded_text,
            laws=tuple(result.laws),
            cases=tuple(result.cases),
            guides=tuple(result.guides),
            document_evidences=document_evidences,
            requires_official_citation=not result.is_empty(),
        )

        return {
            "raw_text": grounded_text,
            "candidate": candidate,
            "next_step": "deterministic_validation",
        }

    def deterministic_validation_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        report = chain_module.audit_answer(state["candidate"])

        if not report.is_valid:
            next_step: Route = "abstain_validation"
        elif chain_module.requires_semantic_validation(state["candidate"]):
            next_step = "semantic_validation"
        else:
            logger.info("조건부 의미 검증 생략: 단일 법령의 단순 답변")
            next_step = "answer"

        return {
            "validation_report": report,
            "validation_mode": "deterministic",
            "next_step": next_step,
        }

    def semantic_validation_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        candidate = state["candidate"]
        report = chain_module.audit_answer(
            candidate,
            semantic_judge=lambda question, text, evidences: chain_module._semantic_judge(
                get_aux_llm()
            )(question, text, evidences, document_evidences),
        )

        return {
            "validation_report": report,
            "validation_mode": "semantic",
            "next_step": (
                "abstain_validation"
                if not report.is_valid
                else "answer"
            ),
        }

    def abstain_validation_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        return {
            "answer": chain_module._abstained_after_validation(
                state["safe_question"],
                state["retrieval_result"],
                state["validation_report"],
                document_evidences,
                validation_mode=state.get("validation_mode", "deterministic"),
            )
        }

    def answer_node(
        state: GenerationGraphState,
    ) -> GenerationGraphState:
        return {
            "answer": replace(
                state["candidate"],
                validation_mode=state.get("validation_mode", "deterministic"),
            ),
        }

    builder = StateGraph(GenerationGraphState)

    builder.add_node("input_guard", input_guard_node)
    builder.add_node("abstain_no_input", abstain_no_input_node)
    builder.add_node("injection_check", injection_check_node)
    builder.add_node("injection_semantic", injection_semantic_node)
    builder.add_node("scope_check", scope_check_node)
    builder.add_node("scope_semantic", scope_semantic_node)
    builder.add_node("refuse", refuse_node)
    builder.add_node("retrieval", retrieval_node)
    builder.add_node("abstain_no_evidence", abstain_no_evidence_node)
    builder.add_node("generate", generate_node)
    builder.add_node("abstain_generation", abstain_generation_node)
    builder.add_node("grounding", grounding_node)
    builder.add_node(
        "deterministic_validation",
        deterministic_validation_node,
    )
    builder.add_node("semantic_validation", semantic_validation_node)
    builder.add_node("abstain_validation", abstain_validation_node)
    builder.add_node("answer", answer_node)

    builder.add_edge(START, "input_guard")

    builder.add_conditional_edges(
        "input_guard",
        _next_step,
        {
            "abstain_no_input": "abstain_no_input",
            "injection_check": "injection_check",
        },
    )
    builder.add_edge("abstain_no_input", END)

    builder.add_conditional_edges(
        "injection_check",
        _next_step,
        {
            "refuse": "refuse",
            "injection_semantic": "injection_semantic",
            "scope_check": "scope_check",
        },
    )
    builder.add_conditional_edges(
        "injection_semantic",
        _next_step,
        {
            "refuse": "refuse",
            "scope_check": "scope_check",
        },
    )

    builder.add_conditional_edges(
        "scope_check",
        _next_step,
        {
            "refuse": "refuse",
            "scope_semantic": "scope_semantic",
            "retrieval": "retrieval",
        },
    )
    builder.add_conditional_edges(
        "scope_semantic",
        _next_step,
        {
            "refuse": "refuse",
            "retrieval": "retrieval",
        },
    )
    builder.add_edge("refuse", END)

    builder.add_conditional_edges(
        "retrieval",
        _next_step,
        {
            "abstain_no_evidence": "abstain_no_evidence",
            "generate": "generate",
        },
    )
    builder.add_edge("abstain_no_evidence", END)

    builder.add_conditional_edges(
        "generate",
        _next_step,
        {
            "abstain_generation": "abstain_generation",
            "grounding": "grounding",
        },
    )
    builder.add_edge("abstain_generation", END)
    builder.add_edge("grounding", "deterministic_validation")

    builder.add_conditional_edges(
        "deterministic_validation",
        _next_step,
        {
            "abstain_validation": "abstain_validation",
            "semantic_validation": "semantic_validation",
            "answer": "answer",
        },
    )
    builder.add_conditional_edges(
        "semantic_validation",
        _next_step,
        {
            "abstain_validation": "abstain_validation",
            "answer": "answer",
        },
    )

    builder.add_edge("abstain_validation", END)
    builder.add_edge("answer", END)

    return builder.compile()


def answer_question(
    question: str,
    service: RetrievalService | None = None,
    llm=None,
    k_law: int = chain_module.DEFAULT_K_LAW,
    k_case: int = chain_module.DEFAULT_K_CASE,
    k_guide: int = chain_module.DEFAULT_K_GUIDE,
    refuse_check: Callable[[str], bool] | None = None,
    auxiliary_llm=None,
    document_evidences: tuple[SessionDocumentEvidence, ...] = (),
    document_search_attempted: bool = False,
) -> Answer:
    """LangGraph가 실행·상태·분기를 담당하는 Generation 진입점.

    raw 사용자 입력이 LangSmith의 Graph root input에 기록되기 전에
    기존 ``chain._safe_question()``과 동일한 마스킹을 먼저 적용한다.
    """

    safe_question = chain_module._safe_question(question)

    graph = build_generation_graph(
        service=service,
        llm=llm,
        k_law=k_law,
        k_case=k_case,
        k_guide=k_guide,
        refuse_check=refuse_check,
        auxiliary_llm=auxiliary_llm,
        document_evidences=document_evidences,
        document_search_attempted=document_search_attempted,
    )

    # 문서 OCR 원문은 Graph state에 넣지 않는다. LangSmith가 활성화돼 있어도
    # 문서 질의에서는 전체 실행 추적을 끄고, 근거는 위 클로저에서만 사용한다.
    trace_scope = tracing_context(enabled=False) if document_evidences else nullcontext()
    with trace_scope:
        state = graph.invoke(
            {
                "safe_question": safe_question,
            },
            config={
                "run_name": "jeonseon_generation_graph",
            },
        )

    answer = state.get("answer")
    if answer is None:
        raise RuntimeError("Generation Graph가 answer를 반환하지 않았습니다.")

    return answer


def answer_document_question(
    question: str,
    document_evidences: tuple[SessionDocumentEvidence, ...],
    **kwargs,
) -> Answer:
    """세션 OCR과 공식 검색 결과를 같은 Graph 정책·검증 경로로 처리한다."""

    if not document_evidences or chain_module._is_document_only_question(question):
        kwargs.setdefault("k_law", 0)
        kwargs.setdefault("k_case", 0)
        kwargs.setdefault("k_guide", 0)

    return answer_question(
        question,
        document_evidences=document_evidences,
        document_search_attempted=True,
        **kwargs,
    )
