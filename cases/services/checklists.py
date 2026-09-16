"""Compatibility entry point for chat-room-based checklist generation."""

from .conversation_guidance import refresh_conversation_guidance


def rebuild_checklist(case, *, messages=None, llm=None):
    refresh_conversation_guidance(case, messages=messages, llm=llm)
    return case.checklist_items.all()
