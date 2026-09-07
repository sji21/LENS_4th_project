"""여러 검색기의 결과를 순위로 합치는 Hybrid 검색기.

BM25 와 KURE 는 서로 다른 문항에서 실패한다. 어휘 기반은 조문번호나 법률용어처럼
정확일치가 필요한 질의에 강하고, 임베딩은 사용자가 쓰는 말과 조문의 말이 다를 때
강하다. 실패가 겹치지 않으므로 합치면 서로의 빈틈을 메운다.

**점수가 아니라 순위로 합친다.** BM25 는 0~50 범위의 열린 점수를, 코사인은 0~1을
낸다. 그대로 더하면 BM25 가 압도한다. RRF(Reciprocal Rank Fusion)는 각 검색기에서
몇 등이었는지만 보므로 척도를 맞출 필요가 없다.

    RRF(d) = Σ  weight_i / (rrf_k + rank_i(d))

rrf_k 는 상위 순위의 영향력을 조절한다. 작으면 1등에 크게 쏠리고, 크면 순위 간
차이가 완만해진다. 60 이 관례적인 기본값이다.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field

from src.retrieval.retriever import Retriever

DEFAULT_RRF_K = 60
DEFAULT_DEPTH = 20


def _accepts_expand(retriever: Retriever) -> bool:
    """검색기가 expand_weight 를 받는지 확인한다."""
    try:
        return "expand_weight" in inspect.signature(retriever.search).parameters
    except (TypeError, ValueError):
        return False


@dataclass
class Member:
    """합칠 검색기 하나와 그 설정.

    expand_weight 를 검색기마다 따로 두는 이유가 있다. 용어 사전은 낱말 겹침을
    늘리는 장치라 BM25 에서만 효과가 있고, 의미로 매칭하는 임베딩에서는 측정상
    변화가 없었다. 한 값을 공유하면 둘 중 하나에 맞지 않는 설정이 강요된다.
    """

    retriever: Retriever
    name: str = ""
    weight: float = 1.0
    expand_weight: float = 0.0


@dataclass
class HybridRetriever:
    """RRF 로 여러 검색기의 순위를 합친다.

    `search(query, k, where)` 인터페이스를 지키므로 평가 하네스와 스윕이 그대로
    돌아간다.
    """

    members: list[Member]
    rrf_k: int = DEFAULT_RRF_K
    depth: int = DEFAULT_DEPTH
    _last: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def search(
        self,
        query: str,
        k: int,
        where: dict | None = None,
        expand_weight: float = 0.0,
    ) -> list[tuple[str, float]]:
        """expand_weight 인자는 인터페이스 호환용이며 무시된다.

        각 Member 가 자기 값을 들고 있다. 하나로 강제하면 BM25 와 임베딩 중
        한쪽에 맞지 않는 설정이 된다.
        """
        # 최종 k 보다 깊게 뽑아야 한쪽에서만 상위인 문서가 합류할 기회를 얻는다.
        depth = max(self.depth, k)
        fused: dict[str, float] = {}
        self._last = {}

        for member in self.members:
            hits = self._ask(member, query, depth, where)
            self._last[member.name or str(id(member))] = [cid for cid, _ in hits]
            for rank, (chunk_id, _score) in enumerate(hits, start=1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + member.weight / (
                    self.rrf_k + rank
                )

        # 구성원과 같은 규칙으로 동점을 깨어 재실행 결과가 흔들리지 않게 한다.
        ranked = sorted(fused.items(), key=lambda x: (-x[1], x[0]))
        return ranked[:k]

    @staticmethod
    def _ask(
        member: Member, query: str, depth: int, where: dict | None
    ) -> list[tuple[str, float]]:
        """구성원에게 검색을 요청한다.

        `Retriever` 프로토콜이 요구하는 것은 `search(query, k, where)` 까지다.
        expand_weight 는 어휘 기반 검색기에만 있는 선택 인자이므로, 없는 구현
        (예: TfidfRetriever)에 그대로 넘기면 TypeError 가 난다. 받는 쪽만 넘긴다.
        """
        if member.expand_weight and _accepts_expand(member.retriever):
            return member.retriever.search(query, depth, where, member.expand_weight)
        return member.retriever.search(query, depth, where)

    def last_member_hits(self) -> dict[str, list[str]]:
        """직전 검색에서 각 구성원이 낸 순위. 어느 쪽이 기여했는지 볼 때 쓴다."""
        return dict(self._last)
