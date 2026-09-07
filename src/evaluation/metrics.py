"""검색 품질 지표.

전부 LLM 호출 없이 계산된다. 즉 비용 0으로 무제한 반복할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def hit_at_k(retrieved_articles: list[str], gold_articles: list[str], k: int) -> bool:
    """상위 k개 안에 정답 조문이 하나라도 있으면 성공.

    주의: k를 키우면 정의상 절대 나빠지지 않는다. "k=8이 k=5보다 낫다"는
    발견이 아니므로 항상 MRR과 함께 볼 것.
    """
    return bool(set(retrieved_articles[:k]) & set(gold_articles))


def recall_at_k(retrieved_articles: list[str], gold_articles: list[str], k: int) -> float:
    """정답 조문 전체 중 상위 k개 안에서 찾아낸 비율."""
    if not gold_articles:
        return 0.0
    found = set(retrieved_articles[:k]) & set(gold_articles)
    return len(found) / len(set(gold_articles))


def reciprocal_rank(retrieved_articles: list[str], gold_articles: list[str]) -> float:
    """첫 번째 정답이 몇 등으로 나왔는지의 역수. 1등이면 1.0, 5등이면 0.2, 없으면 0."""
    gold = set(gold_articles)
    for rank, article in enumerate(retrieved_articles, start=1):
        if article in gold:
            return 1.0 / rank
    return 0.0


@dataclass
class QuestionResult:
    qid: str
    question: str
    gold_articles: list[str]
    retrieved_articles: list[str]
    hit: bool
    rr: float
    recall: float


@dataclass
class RunResult:
    run_id: str
    k: int
    results: list[QuestionResult] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def hit_rate(self) -> float:
        return sum(r.hit for r in self.results) / self.n if self.n else 0.0

    @property
    def mrr(self) -> float:
        return sum(r.rr for r in self.results) / self.n if self.n else 0.0

    @property
    def mean_recall(self) -> float:
        return sum(r.recall for r in self.results) / self.n if self.n else 0.0

    @property
    def failures(self) -> list[str]:
        return [r.qid for r in self.results if not r.hit]

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "k": self.k,
            "n": self.n,
            f"hit@{self.k}": round(self.hit_rate, 4),
            "mrr": round(self.mrr, 4),
            f"recall@{self.k}": round(self.mean_recall, 4),
            "failures": self.failures,
        }


def standard_error(p: float, n: int) -> float:
    """비율 지표의 표준오차.

    n이 작으면 이 값이 커진다. 두 실험의 차이가 이 값의 2배 안쪽이면
    "우연히 그럴 수도 있는 차이"로 보고 개선이라고 주장하지 않는다.
    """
    if n == 0:
        return 0.0
    return (p * (1 - p) / n) ** 0.5


def compare(before: RunResult, after: RunResult) -> dict:
    """같은 문항들에 대해 짝지어 비교한다.

    총계(0.78 -> 0.81)만 보면 문항 수가 적을 때 노이즈와 구분이 안 된다.
    어떤 문항이 실패에서 성공으로 뒤집혔는지 세는 쪽이 훨씬 민감하다.
    """
    before_map = {r.qid: r for r in before.results}
    after_map = {r.qid: r for r in after.results}
    common = sorted(set(before_map) & set(after_map))

    fixed = [q for q in common if not before_map[q].hit and after_map[q].hit]
    broken = [q for q in common if before_map[q].hit and not after_map[q].hit]

    return {
        "n_common": len(common),
        "fixed": fixed,      # 실패 -> 성공
        "broken": broken,    # 성공 -> 실패
        "net": len(fixed) - len(broken),
        "hit_before": round(before.hit_rate, 4),
        "hit_after": round(after.hit_rate, 4),
        "hit_delta": round(after.hit_rate - before.hit_rate, 4),
        "se_before": round(standard_error(before.hit_rate, before.n), 4),
    }
