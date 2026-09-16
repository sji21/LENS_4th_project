"""Compatibility entry point for chat-room-based calendar generation."""

from .conversation_guidance import refresh_conversation_guidance


def rebuild_schedule_candidates(case, *, messages=None, llm=None):
    refresh_conversation_guidance(case, messages=messages, llm=llm)
    return case.schedule_events.all()
