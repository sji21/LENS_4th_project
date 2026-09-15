def title_from_question(question, limit=36):
    """첫 질문을 사이드바에서 읽기 좋은 안전한 채팅방 이름으로 줄인다."""
    title = " ".join(str(question).split()).strip()
    if not title:
        return "새 임대차 상담"
    if len(title) <= limit:
        return title
    return f"{title[:limit - 1].rstrip()}…"
