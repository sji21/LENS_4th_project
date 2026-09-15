"""Preserved relative RRF band policy from the 8,366-case runtime."""
import math
import re


def court_level(court):
    court = re.sub(r"\s+", "", court).replace("고등법원", "고법").replace("지방법원", "지법")
    if court == "대법원":
        return 0
    if "고법" in court:
        return 1
    if any(t in court for t in ("지법", "가정법원", "행정법원", "회생법원", "특허법원")):
        return 2
    return 3


def rerank_cases(hits, chunks, k: int, tolerance: float = 0.03):
    if k <= 0:
        return []
    if not 0 <= tolerance < 1:
        raise ValueError("구간 비율은 0 이상 1 미만이어야 합니다")
    ranked = [(cid, float(score), rank) for rank, (cid, score) in enumerate(hits)
              if cid in chunks and math.isfinite(float(score))]
    ranked.sort(key=lambda h: (-h[1], h[2], h[0]))

    def order(hit):
        cid, _, original_rank = hit
        meta = chunks[cid]["metadata"]
        level = int(meta.get("court_level", court_level(str(meta.get("court_name", "")))))
        day = str(meta.get("decision_date", "")).replace("-", "")
        return level, -int(day) if day.isdigit() else 0, original_rank, cid

    out = []
    i = 0
    while i < len(ranked):
        anchor = ranked[i][1]
        end = i + 1
        while end < len(ranked):
            delta = anchor - ranked[end][1]
            if not (delta == 0 or anchor > 0 and delta <= tolerance * anchor + 1e-12):
                break
            end += 1
        out.extend(sorted(ranked[i:end], key=order))
        i = end
    return [(cid, score) for cid, score, _ in out[:k]]
