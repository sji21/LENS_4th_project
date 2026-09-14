"""Dispatch validated conversation actions while retaining the legal pipeline."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
import time

from langsmith import tracing_context

from src.generation import chain
from src.generation.call_budget import conversation_budget, check_deadline
from . import dialogue_planner
from .dialogue_contract import Decision, parse_decision, previous_answer_query, pending_request
from .dialogue_query import grounded_query
from .dialogue_documents import select_document, document_question, retain_followup_topic
from .dialogue_clarification import short_answer_decision, prepare_clarification, answer_reason
from .dialogue_state import apply_user_update, ensure_dialogue, record_answer
from .dialogue_recovery import recovery_pending, resume_recovery


SOCIAL_TEXT = "안녕하세요. 주택 임대차와 관련해 궁금한 점을 편하게 말씀해 주세요."
CORRECTION_TEXT = "말씀하신 내용으로 수정했어요. 이어서 궁금한 점을 말씀해 주세요."


def _original_input_refusal(question):
    """Finish original-input checks before a planner or document rewrite runs."""
    safe_question = chain._safe_question(question)
    injection = chain.classify_prompt_injection(safe_question)
    if injection.needs_semantic_review and not injection.blocked:
        def judge(text):
            model = chain.get_llm(
                temperature=0.0, max_tokens=128, timeout=35, max_retries=0,
                allow_route_fallback=False, extra_body={"think": False},
            )
            return chain._injection_judge(model)(text)

        injection = chain.classify_prompt_injection(safe_question, semantic_judge=judge)
        if injection.needs_semantic_review:
            # The shared classifier keeps its legacy default-allow policy. This
            # new rewrite boundary requires a completed review before proceeding.
            raise RuntimeError("Input review unavailable")
    if injection.blocked:
        return chain._refused_answer(safe_question, "prompt_injection")
    scope = chain.classify_scope(safe_question)
    if scope.out_of_scope:
        return chain._refused_answer(safe_question, scope.reason)
    return None


def _static_message(services, text, status, started):
    # Use the established formatter without labelling a code template as a
    # verified legal answer or adding an unverified model proposal to context.
    from src.generation.models import Answer
    answer = Answer(question="", status="abstained", text=text)
    message = services.answer_message(answer, started)
    message["status"] = status
    return message


def _commit(state, draft):
    check_deadline()
    state.clear()
    state.update(draft)


def respond_conversational(state, question, document_id=None, *, legacy):
    """Execute one action and commit only after the whole exchange succeeds."""
    # services delegates here lazily; importing it here avoids a module cycle.
    from . import services

    question = services.safe_text(question)
    started = time.perf_counter()
    draft = deepcopy(state)
    with conversation_budget(), tracing_context(enabled=False):
        refusal = _original_input_refusal(question)
        if refusal is not None:
            message = services.answer_message(refusal, started)
            message.update(action="refuse", intent=None)
            record_answer(draft, message)
            draft["dialogue_runtime"] = {"path": "conversation"}
            services.append_exchange(draft, question, message)
            _commit(state, draft)
            return message

        resume_recovery(draft, question)
        recovery_base = deepcopy(draft)
        selected_input, ambiguous = select_document(draft, question, document_id)
        try:
            decision = document_question(draft) if ambiguous else None
            awaiting_guided_answer = (ensure_dialogue(draft)["pending"] or {}).get("mode") == "after_answer"
            if decision is None and not selected_input and not awaiting_guided_answer:
                decision = short_answer_decision(draft, question)
            if decision is None:
                decision = dialogue_planner.plan_turn(draft, question, selected_input).decision
        except dialogue_planner.PlanningError as error:
            # Service errors are retryable HTTP failures, not an invitation to
            # answer with unclassified context. No state is committed on these.
            if error.code == "model_unavailable" or error.code.startswith("invalid_input:"):
                raise RuntimeError("Conversation planning unavailable") from None
            draft = recovery_base
            pending = recovery_pending(draft, question, selected_input)
            message = _static_message(services, pending["question"], "clarify", started)
            message.update(action="clarify", intent=None, choices=pending["choices"], reason="needs_information")
            pending["message_id"] = message["id"]
            draft["dialogue"]["pending"] = pending
            draft["dialogue"]["last_status"] = "clarify"
            draft["dialogue_runtime"] = {"path": "clarification_recovery", "reason": error.code}
            services.append_exchange(draft, question, message)
            _commit(state, draft)
            return message

        if not isinstance(decision, Decision):
            raise RuntimeError("Invalid conversation decision")
        decision = retain_followup_topic(draft, decision)
        continuation = not decision.topic_changed and decision.intent in {"document_question", "followup", "explain", "clarification_answer", "correction"}
        selected, unresolved = select_document(draft, question, selected_input, continue_document=continuation)
        if unresolved:
            decision = document_question(draft)
        elif decision.action != "refuse":
            decision = replace(decision, document_id=selected)
        decision, pending = prepare_clarification(draft, question, decision)
        use_document = bool(selected) and not unresolved
        if decision.action == "rag":
            query = grounded_query(draft, question, decision, document_context=use_document)
            decision = parse_decision(json.dumps(asdict(replace(decision, search_query=query)), ensure_ascii=False),
                                      state=draft, user=question, preserved_user=True)
        if decision.action == "refuse":
            answer = chain._refused_answer(chain._safe_question(question), "prompt_injection")
            message = services.answer_message(answer, started)
            # Even a valid representation is not permission to remember an
            # unsafe request as the user's consultation facts or user history.
            draft = deepcopy(state)
        else:
            previous = ensure_dialogue(deepcopy(state))
            selected_id = decision.document_id if decision.document_id is not None else document_id
            dialogue = apply_user_update(
                draft, user=question, updates=decision.updates, topic=decision.topic,
                topic_changed=decision.topic_changed, document_id=selected_id,
            )
            if decision.action == "social":
                text = CORRECTION_TEXT if decision.intent == "correction" else SOCIAL_TEXT
                message = _static_message(services, text, "social", started)
            elif decision.action == "clarify":
                message = _static_message(services, pending["question"], "clarify", started)
                message.update(choices=pending["choices"], reason="needs_information")
                pending["message_id"] = message["id"]
                dialogue["pending"] = pending
                dialogue["clarification_counts"][pending["field"]] = pending["attempts"]
            elif decision.action == "rag":
                query = decision.search_query
                response_style = "consult" if pending and decision.style == "standard" else decision.style
                active_id = dialogue["active_document_id"]
                documents = draft["documents"]
                if use_document:
                    evidences = services.find_evidences(query, documents, active_id)
                    answer = services.graph.answer_document_question(query, evidences, service=services.retrieval_loader().result(), response_style=response_style)
                else:
                    answer = services.graph.answer_question(query, service=services.retrieval_loader().result(), response_style=response_style)
                used_history = not decision.topic_changed and bool(previous["history"] or previous["facts"] or previous["active_document_id"])
                message = services.answer_message(answer, started, used_history)
                message["reason"] = answer_reason(answer)
                dialogue["pending"] = None
                if message["status"] == "refused":
                    draft = deepcopy(state)
                elif pending:
                    message.update(followup_question=pending["question"], choices=pending["choices"])
                    pending["message_id"] = message["id"]
                    dialogue["pending"] = pending
                    dialogue["clarification_counts"][pending["field"]] = pending["attempts"]
            else:
                raise RuntimeError("Invalid conversation action")

        message.update(action="refuse" if message["status"] == "refused" else decision.action, intent=decision.intent)
        if message["status"] in {"answered", "abstained", "refused"}:
            query = None
            request = None
            if decision.action == "rag":
                cached = ensure_dialogue(draft).get("last_answer") or {}
                query = (previous_answer_query(ensure_dialogue(draft), "explain")
                         if decision.intent == "explain" else None) or decision.search_query
                request = ((cached.get("request") if decision.intent == "explain" else None)
                           or pending_request(previous, decision.intent) or question)
            record_answer(draft, message, query=query, request=request)
        else:
            # An interleaved social reply is not a replacement legal answer.
            draft["dialogue"]["last_status"] = message["status"]
        draft["dialogue_runtime"] = {"path": "conversation"}
        services.append_exchange(draft, question, message)
        _commit(state, draft)
        return message
