"""평가셋 정답 라벨 점검.

코퍼스가 커지면 정답 라벨이 조용히 낡는다. 이전에는 없던 더 알맞은 조문이
생기거나(제14조 위원회 설치 -> 제21조 조정의 신청), 근거가 없어서
unanswerable 로 분류했던 질문에 답이 생기기도 한다(시행령 추가).
그대로 두면 검색기가 맞는 답을 냈는데 오답으로 채점된다.

자동으로 고치지 않는다. 의심 항목만 추려서 사람이 판단할 목록을 만든다.
법적 판단은 도메인 담당의 몫이다.

실행:
    python -m src.evaluation.audit_eval
    python -m src.evaluation.audit_eval --chunks data/sample/chunks_expanded.jsonl
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from src.evaluation.run_eval import load_questions
from src.retrieval.retriever import BM25Retriever, load_chunks

OUT_PATH = Path("docs/eval-audit.md")
DEFAULT_CHUNKS = "data/sample/chunks_expanded.jsonl"
DEFAULT_EVAL = "data/eval/dev.jsonl"
SANG = ["상가건물 임대차보호법", "상가건물 임대차보호법 시행령"]
WHERE = {"title": {"$nin": SANG}}
DEPTH = 20

_WORD = re.compile(r"[가-힣]{2,}")
# 어느 조문에나 나오는 말은 제목 일치 신호로 쓰지 않는다.
_STOP = {
    "있나요", "되나요", "하나요", "인가요", "어떻게", "무엇", "얼마", "경우",
    "때는", "하는", "있는", "되는", "합니다", "임대차", "임차인", "임대인",
    "수는", "제가", "우리", "그런", "이런",
}


def keywords(text: str) -> set[str]:
    return {w for w in _WORD.findall(text) if w not in _STOP}


def title_overlap(question: str, article_title: str) -> set[str]:
    """질문 낱말이 조문 제목에 얼마나 들어 있나.

    조문 제목은 그 조문이 무엇을 정한 것인지 가장 압축적으로 말해 주므로,
    제목과 겹치는 말이 많은데 정답이 아니라면 라벨을 의심할 만하다.
    """
    if not article_title:
        return set()
    hits = set()
    for word in keywords(question):
        # "신청할" 과 "신청" 처럼 어미가 달라도 잡히도록 앞 두 글자로 비교한다.
        if word in article_title or word[:2] in article_title:
            hits.add(word)
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default=DEFAULT_CHUNKS)
    ap.add_argument("--eval-set", default=DEFAULT_EVAL)
    args = ap.parse_args()

    chunks = load_chunks(args.chunks)
    questions = load_questions(args.eval_set)
    meta = {c["metadata"]["article_id"]: c["metadata"] for c in chunks}
    retriever = BM25Retriever(chunks, b=0.25)
    id_of = {c["chunk_id"]: c["metadata"]["article_id"] for c in chunks}

    def top(question: str, depth: int = DEPTH) -> list[str]:
        seen: list[str] = []
        for chunk_id, _ in retriever.search(question, depth, WHERE):
            article = id_of[chunk_id]
            if article not in seen:
                seen.append(article)
        return seen

    def label(article_id: str) -> str:
        m = meta.get(article_id)
        if not m:
            return f"`{article_id}` **(코퍼스에 없음)**"
        title = m["article_title"]
        return f"`{article_id}`" + (f" — {title}" if title else "")

    flagged: list[tuple[str, dict, str, list[str]]] = []

    for q in questions:
        gold = q.get("gold_articles") or []
        ranked = top(q["question"])
        notes: list[str] = []

        missing = [g for g in gold if g not in meta]
        if missing:
            notes.append(f"정답 조문이 코퍼스에 없음: {', '.join(missing)}")

        if not gold:
            # 근거가 없다고 분류한 질문. 코퍼스가 커졌으니 정말 없는지 다시 본다.
            cands = []
            for a in ranked[:3]:
                shared = title_overlap(q["question"], meta[a]["article_title"])
                if shared:
                    cands.append((a, shared))
            if cands:
                notes.append(
                    "`" + q.get("answer_type", "?") + "` 으로 분류돼 있으나, "
                    "제목이 질문과 겹치는 조문이 검색됨 — 답이 생겼는지 확인"
                )
        else:
            rank = next((i for i, a in enumerate(ranked, 1) if a in gold), None)
            if rank is None:
                notes.append(f"정답이 상위 {DEPTH}개 안에 없음")
            elif rank > 5:
                notes.append(f"정답이 {rank}위 — 검색 실패인지 라벨 문제인지 확인")

            # 정답보다 위에 있으면서 제목이 질문과 더 겹치는 조문
            better = []
            for i, a in enumerate(ranked[: rank - 1 if rank else 5], 1):
                shared = title_overlap(q["question"], meta[a]["article_title"])
                gold_shared = max(
                    (len(title_overlap(q["question"], meta[g]["article_title"]))
                     for g in gold if g in meta),
                    default=0,
                )
                if len(shared) > gold_shared:
                    better.append((i, a, shared))
            if better:
                notes.append(
                    "정답보다 상위에 제목이 더 맞아 보이는 조문이 있음 — "
                    "라벨 재검토 대상"
                )

        if notes:
            flagged.append((q["qid"], q, "\n".join(f"- {n}" for n in notes), ranked))

    lines = [
        "# 평가셋 정답 라벨 점검",
        "",
        f"- 코퍼스: `{args.chunks}` ({len(chunks)}개 청크)",
        f"- 평가셋: `{args.eval_set}` ({len(questions)}문항)",
        f"- 검토 대상으로 걸린 문항: **{len(flagged)}개**",
        "",
        "자동 판정이 아니다. 아래 항목은 **법을 아는 사람이 직접 확인**해야 한다.",
        "확인 후 `dev.jsonl` 의 `gold_articles` / `answer_type` 을 고치고 이 파일을 다시 생성한다.",
        "",
        "---",
        "",
    ]

    for qid, q, notes, ranked in flagged:
        lines += [
            f"## [{qid}] {q['question']}",
            "",
            f"- 현재 분류: `{q.get('answer_type', '?')}`",
            "- 현재 정답: "
            + (", ".join(label(g) for g in q.get("gold_articles") or []) or "_(없음)_"),
            "",
            "**의심 사유**",
            notes,
            "",
            "**검색 상위 5개**",
            "",
        ]
        for i, a in enumerate(ranked[:5], 1):
            mark = " ✅" if a in (q.get("gold_articles") or []) else ""
            lines.append(f"{i}. {label(a)}{mark}")
        lines += [
            "",
            "**판정** (해당란에 표시)",
            "",
            "- [ ] 현재 라벨이 맞다 — 검색 개선으로 해결할 문제",
            "- [ ] 라벨을 고쳐야 한다 → 올바른 조문: `____________`",
            "- [ ] 분류를 바꿔야 한다 → `answerable` / `unanswerable` / `out_of_scope`",
            "",
            "---",
            "",
        ]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(flagged)}/{len(questions)}문항이 검토 대상 -> {OUT_PATH}")
    for qid, q, _, _ in flagged:
        print(f"  {qid}  {q['question'][:46]}")


if __name__ == "__main__":
    main()
