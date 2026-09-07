"""청크 규격 검사기.

수집·청킹 파이프라인이 만든 chunks.jsonl 이 검색기와 평가 하네스가 기대하는
형태인지 확인한다. 규격이 어긋나면 검색은 되는데 채점이 통째로 오답이 되는 식으로
조용히 망가지므로, 인덱싱 전에 여기서 걸러낸다.

규격 설명은 docs/chunk-schema.md 참고.

실행:
    python -m src.ingestion.validate_chunks data/sample/chunks_expanded.jsonl
    python -m src.ingestion.validate_chunks <파일> --eval-set data/eval/dev.jsonl

정상이면 종료 코드 0, 문제가 있으면 1. CI 에 그대로 걸 수 있다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REQUIRED_TOP = ["chunk_id", "doc_id", "text", "metadata"]
REQUIRED_META = [
    "title", "doc_type", "article_id", "article_no",
    "article_title", "source_url", "effective_date", "status",
]

DOC_TYPES = {"law", "decree", "rule", "case", "interp", "guide"}
STATUSES = {"current", "historical", "repealed"}

# "주택임대차보호법-제3조의2" / "주택임대차보호법 시행령-제10조"
ARTICLE_ID = re.compile(r"^.+-제\d+조(?:의\d+)?$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEADER = re.compile(r"^\[.+\]")

# Chroma 는 메타데이터 값으로 이 타입만 받는다. 리스트와 None 은 적재 시점에 터진다.
SCALARS = (str, int, float, bool)


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def load(path: Path, rep: Report) -> list[dict]:
    chunks = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            chunks.append(json.loads(line))
        except json.JSONDecodeError as e:
            rep.error(f"{lineno}번째 줄: JSON 파싱 실패 — {e}")
    return chunks


def check_structure(chunks: list[dict], rep: Report) -> None:
    seen_ids: Counter[str] = Counter()

    for i, c in enumerate(chunks):
        where = f"[{i}] {c.get('chunk_id', '(chunk_id 없음)')}"

        for field in REQUIRED_TOP:
            if field not in c:
                rep.error(f"{where}: 최상위 필드 '{field}' 없음")
        if not isinstance(c.get("metadata"), dict):
            rep.error(f"{where}: metadata 가 객체가 아님")
            continue

        m = c["metadata"]
        for field in REQUIRED_META:
            if field not in m:
                rep.error(f"{where}: metadata.{field} 없음")

        # Chroma 타입 제약
        for key, value in m.items():
            if value is None:
                rep.error(
                    f"{where}: metadata.{key} 가 None. "
                    f"Chroma 가 거부한다 — 빈 문자열 \"\" 로 넣을 것"
                )
            elif isinstance(value, (list, dict)):
                rep.error(
                    f"{where}: metadata.{key} 가 {type(value).__name__}. "
                    f"Chroma 는 스칼라만 받는다 — \"a|b|c\" 처럼 직렬화할 것"
                )
            elif not isinstance(value, SCALARS):
                rep.error(f"{where}: metadata.{key} 타입이 {type(value).__name__}")

        if isinstance(c.get("chunk_id"), str):
            seen_ids[c["chunk_id"]] += 1

        text = c.get("text", "")
        if not isinstance(text, str) or not text.strip():
            rep.error(f"{where}: text 가 비어 있음")
        elif not HEADER.match(text):
            rep.warn(
                f"{where}: text 가 [법령명 제○조(제목)] 헤더로 시작하지 않음. "
                f"청크만 떼어 봐도 출처를 알 수 있어야 한다"
            )

        aid = m.get("article_id", "")
        if isinstance(aid, str) and aid and not ARTICLE_ID.match(aid):
            # 가이드 등 조문이 아닌 문서는 형식이 달라도 된다
            if m.get("doc_type") in {"law", "decree", "rule"}:
                rep.error(
                    f"{where}: article_id '{aid}' 형식이 다름. "
                    f"'{{법령명}}-제N조' 또는 '{{법령명}}-제N조의M' 이어야 한다"
                )

        dt = m.get("doc_type")
        if dt is not None and dt not in DOC_TYPES:
            rep.error(f"{where}: doc_type '{dt}' 은 허용값이 아님 {sorted(DOC_TYPES)}")

        st = m.get("status")
        if st is not None and st not in STATUSES:
            rep.error(f"{where}: status '{st}' 은 허용값이 아님 {sorted(STATUSES)}")

        for field in ("effective_date", "expiry_date"):
            v = m.get(field)
            if isinstance(v, str) and v and not DATE.match(v):
                rep.error(f"{where}: {field} '{v}' 는 YYYY-MM-DD 형식이 아님")

    for cid, n in seen_ids.items():
        if n > 1:
            rep.error(f"chunk_id 중복 {n}회: {cid}")


def check_consistency(chunks: list[dict], rep: Report) -> None:
    """같은 article_id 를 가진 청크끼리 메타데이터가 어긋나지 않는지."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        m = c.get("metadata")
        if isinstance(m, dict) and isinstance(m.get("article_id"), str):
            groups[m["article_id"]].append(m)

    for aid, metas in groups.items():
        for field in ("title", "article_no", "doc_type"):
            values = {m.get(field) for m in metas}
            if len(values) > 1:
                rep.error(f"article_id '{aid}' 안에서 {field} 가 엇갈림: {values}")


def check_against_eval(chunks: list[dict], eval_path: Path, rep: Report) -> None:
    """평가셋의 정답 조문이 코퍼스에 실재하는지.

    이게 어긋나면 검색기가 아무리 좋아도 그 문항은 영구히 오답이 된다.
    """
    available = {
        c["metadata"]["article_id"]
        for c in chunks
        if isinstance(c.get("metadata"), dict) and c["metadata"].get("article_id")
    }
    missing: dict[str, list[str]] = defaultdict(list)
    total = 0

    for line in eval_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        q = json.loads(line)
        for gold in q.get("gold_articles") or []:
            total += 1
            if gold not in available:
                missing[gold].append(q["qid"])

    if missing:
        rep.error(
            f"평가셋 정답 조문 {len(missing)}종이 코퍼스에 없음 "
            f"(전체 {total}개 중). 해당 문항은 절대 맞출 수 없다:"
        )
        for gold, qids in sorted(missing.items()):
            rep.errors.append(f"    {gold}  ← {', '.join(qids)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="청크 규격 검사")
    ap.add_argument("path", help="검사할 chunks jsonl")
    ap.add_argument("--eval-set", default=None, help="정답 조문 존재 여부까지 확인")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"파일이 없습니다: {path}")
        return 1

    rep = Report()
    chunks = load(path, rep)
    if chunks:
        check_structure(chunks, rep)
        check_consistency(chunks, rep)
        if args.eval_set:
            check_against_eval(chunks, Path(args.eval_set), rep)

    print()
    print(f"검사 대상: {path}  ({len(chunks)}개 청크)")
    print("-" * 66)

    if rep.errors:
        print(f"\n오류 {len(rep.errors)}건 — 고쳐야 인덱싱할 수 있습니다\n")
        for e in rep.errors[:40]:
            print(f"  X {e}")
        if len(rep.errors) > 40:
            print(f"  ... 외 {len(rep.errors) - 40}건")

    if rep.warnings:
        print(f"\n경고 {len(rep.warnings)}건 — 동작은 하지만 검색 품질에 영향\n")
        for w in rep.warnings[:15]:
            print(f"  ! {w}")
        if len(rep.warnings) > 15:
            print(f"  ... 외 {len(rep.warnings) - 15}건")

    if not rep.errors and not rep.warnings:
        print("\n  통과. 규격에 맞습니다.\n")
    elif not rep.errors:
        print("\n  오류 없음. 경고만 확인하면 됩니다.\n")
    else:
        print()

    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
