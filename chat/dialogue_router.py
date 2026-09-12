"""Dispatch validated conversation actions while retaining the legal pipeline."""
from copy import deepcopy
import time

from langsmith import tracing_context

from src.generation import chain
from . import dialogue_planner
from .dialogue_contract import Decision
from .dialogue_state import apply_user_update, ensure_dialogue, record_answer


SOCIAL_TEXT = "안녕하세요. 주택 임대차와 관련해 궁금한 점을 편하게 말씀해 주세요."
CORRECTION_TEXT = "말씀하신 내용으로 수정했어요. 이어서 궁금한 점을 말씀해 주세요."
CLARIFY_TEXT = "답변을 이어가려면 상황을 조금 더 알려주시겠어요?"


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
    state.clear()
    state.update(draft)


def respond_conversational(state, question, document_id=None, *, legacy):
    """Execute one action and commit only after the whole exchange succeeds."""
    # services delegates here lazily; importing it here avoids a module cycle.
    from . import services

    started = time.perf_counter()
    draft = deepcopy(state)
    with tracing_context(enabled=False):
        refusal = _original_input_refusal(question)
        if refusal is not None:
            message = services.answer_message(refusal, started)
            message.update(action="refuse", intent=None)
            record_answer(draft, message)
            draft["dialogue_runtime"] = {"path": "conversation"}
            services.append_exchange(draft, question, message)
            _commit(state, draft)
            return message

        try:
            planned = dialogue_planner.plan_turn(draft, question, document_id)
        except dialogue_planner.PlanningError as error:
            # No partially proposed facts survive a rejected plan. The legacy
            # adapter owns message appending; do not append the exchange twice.
            draft = deepcopy(state)
            message = legacy(draft, question, document_id)
            message.update(action="refuse" if message.get("status") == "refused" else "rag", intent=None)
            record_answer(draft, message)
            draft["dialogue_runtime"] = {"path": "legacy_fallback", "reason": error.code}
            _commit(state, draft)
            return message

        decision = planned.decision
        if not isinstance(decision, Decision):
            raise RuntimeError("Invalid conversation decision")
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
                message = _static_message(services, CLARIFY_TEXT, "clarify", started)
                dialogue["pending"] = {"field": decision.clarify_field, "question": CLARIFY_TEXT, "attempts": 1}
            elif decision.action == "rag":
                query = decision.search_query
                active_id = dialogue["active_document_id"]
                documents = draft["documents"]
                kinds = tuple(dict.fromkeys(doc["kind"] for doc in documents))
                use_document = bool(documents) and (
                    bool(active_id) or services.question_references_uploaded_document(question, kinds)
                )
                if use_document:
                    evidences = services.find_evidences(query, documents, active_id)
                    answer = services.graph.answer_document_question(query, evidences, service=services.retrieval_loader().result())
                else:
                    answer = services.graph.answer_question(query, service=services.retrieval_loader().result())
                used_history = not decision.topic_changed and bool(previous["history"] or previous["facts"] or previous["active_document_id"])
                message = services.answer_message(answer, started, used_history)
                dialogue["pending"] = None
                if message["status"] == "refused":
                    draft = deepcopy(state)
            else:
                raise RuntimeError("Invalid conversation action")

        message.update(action="refuse" if message["status"] == "refused" else decision.action, intent=decision.intent)
        if message["status"] in {"answered", "abstained", "refused"}:
            record_answer(draft, message)
        else:
            # An interleaved social reply is not a replacement legal answer.
            draft["dialogue"]["last_status"] = message["status"]
        draft["dialogue_runtime"] = {"path": "conversation"}
        services.append_exchange(draft, question, message)
        _commit(state, draft)
        return message
