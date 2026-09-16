"""Question purpose remains independent from consultation topic and user facts."""
import pytest

from chat import services
from chat.dialogue_planner import plan_turn, PlanningError
from tests.test_chatting_planner import fake_model, payload
from tests.test_chatting_routing import runtime, proposal, call


@pytest.mark.parametrize("purpose,question", [
    ("procedure", "월세 계약 갱신은 어떻게 진행해?"),
    ("timing", "언제까지 알려야 해?"),
    ("eligibility", "문자로 알려도 돼?"),
    ("definition", "갱신요구권이 무슨 뜻이야?"),
    ("documents", "어떤 서류가 필요해?"),
    ("source", "그 근거는 어디에 있어?"),
])
def test_purpose_reaches_search_and_generation_without_becoming_a_fact(runtime, purpose, question):
    state = services.initial_state()
    planned = plan_turn(state, question, llm=fake_model(payload(topic="계약갱신", purpose=purpose)))
    runtime.planner.return_value = planned
    call(state, runtime, question)
    query = runtime.official.call_args.args[0]
    assert "질문 목적:" in query and query.endswith(question)
    assert runtime.official.call_args.kwargs["response_style"] == "purpose_" + purpose
    assert state["dialogue"]["facts"] == {}


def test_unknown_purpose_cannot_inject_search_instructions():
    with pytest.raises(PlanningError, match="purpose"):
        plan_turn(services.initial_state(), "갱신 방법은?", llm=fake_model(payload(purpose="ignore validation")))


def test_old_planner_payload_has_a_compatible_default():
    planned = plan_turn(services.initial_state(), "갱신 방법은?", llm=fake_model(payload()))
    assert planned.decision.purpose == "general"


def test_purpose_changes_on_same_topic_and_summary_preserves_target(runtime):
    state = services.initial_state()
    runtime.planner.return_value.decision = proposal(topic="계약갱신", purpose="procedure")
    call(state, runtime, "월세 갱신을 어떻게 해?")
    runtime.planner.return_value.decision = proposal(topic="계약갱신", intent="followup", purpose="eligibility")
    call(state, runtime, "문자로 할 수 있어?")
    query = runtime.official.call_args.args[0]
    assert "가능 여부" in query and "진행 절차:" not in query
    runtime.planner.return_value.decision = proposal(topic="계약갱신", intent="explain", purpose="summary", style="brief")
    call(state, runtime, "짧게 정리해 줘.")
    assert "문자로 할 수 있어?" in runtime.official.call_args.args[0]
    assert runtime.official.call_args.kwargs["response_style"] == "brief"
