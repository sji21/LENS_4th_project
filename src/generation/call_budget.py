"""Request-local model limits; legacy callers have no additional budget."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import time


@dataclass
class Budget:
    deadline: float
    remaining_calls: int


_budget = ContextVar("conversation_model_budget", default=None)


def check_deadline():
    budget = _budget.get()
    if budget is not None and time.monotonic() >= budget.deadline:
        raise RuntimeError("Conversation time budget exceeded")


def reserve_call(timeout):
    budget = _budget.get()
    if budget is None:
        return timeout
    check_deadline()
    if budget.remaining_calls <= 0:
        raise RuntimeError("Conversation model call limit exceeded")
    budget.remaining_calls -= 1
    return min(timeout, budget.deadline - time.monotonic())


@contextmanager
def conversation_budget(*, seconds=180, calls=6):
    token = _budget.set(Budget(time.monotonic() + seconds, calls))
    try:
        yield
    finally:
        _budget.reset(token)
