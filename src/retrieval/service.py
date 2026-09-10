"""질문 하나를 받아 LLM 에 넘길 근거를 뽑는 진입점.

생성 쪽이 검색 내부를 몰라도 되게 하는 것이 목적이다. 돌려주는 것은 청크
딕셔너리가 아니라 본문과 출처가 붙은 `Evidence` 다.

**법령과 판례를 따로 뽑는다.** 한 통에 넣고 뽑으면 서로를 밀어낸다. 두 종류를
섞어 측정했을 때 법령 Hit@5 가 17.4%p 떨어졌다. 질문 하나에 필요한 것은 "근거
조문 몇 개"와 "그 쟁점을 다룬 판례 몇 개"이지 둘을 섞은 상위 5개가 아니다.

나누는 방식이 검색기마다 다르다. 어휘 검색(BM25)은 **코퍼스를 쪼개서** 만든다.
IDF 가 코퍼스 전체 기준이라 "대항력"의 희소성이 법령 133 조문 안에서와 판례
26 건 안에서 다르기 때문이다. 임베딩 쪽은 Chroma 컬렉션 하나를 공유하고
`doc_type` 필터로 가른다. 벡터는 문서마다 독립적이라 쪼갤 이유가 없고, 쪼개면
컬렉션이 늘어 적재만 번거로워진다.

묶음별 파라미터는 독립적이다. 생성 모델이 법령 상위 3건만 쓰는 현재 계약에서는
법령 RRF의 상위 순위 집중도를 높여야 정답 조문이 4위에서 잘리지 않았다. 판례 쪽은
공식 원문 평가가 끝나지 않았으므로 기존값을 유지한다. 이후 튜닝도 아래 `LAW`/`CASE`
설정값만 바꾸면 된다.
"""

from __future__ import annotations

import logging
import re

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from src.retrieval.dense import (
    DenseRetriever,
    EmbeddingBackend,
    SentenceTransformerEmbedding,
)
from src.retrieval.hybrid import DEFAULT_RRF_K, HybridRetriever, Member
from src.retrieval.retriever import (
    _WORD_RE,
    BM25Retriever,
    Retriever,
    load_chunks,
)
from src.retrieval.terms import expand, expand_civil, expand_law

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "nlpai-lab/KURE-v1"
DEFAULT_INDEX = Path("data/index/chroma_kurev1_1024")
DEFAULT_CIVIL_INDEX = Path("data/index/chroma_civil_kurev1_1024")
LAW_CHUNKS = Path("data/chunks/chunks.jsonl")
CASE_CHUNKS = Path("data/chunks/cases.jsonl")
GUIDE_CHUNKS = Path("data/chunks/guides.jsonl")
# 배포용 샘플 코퍼스. 청크 산출물이 없는 새 클론에서도 기존 Chroma 인덱스와 짝을 이룬다.
SAMPLE_CHUNKS = Path("data/sample/chunks_expanded.jsonl")

# 어느 doc_type 이 어느 묶음인지. 시행령·시행규칙은 법령과 함께 다뤄야 한다.
LAW_TYPES = ("law", "decree", "rule")
CASE_TYPES = ("case",)
# 법령해석(interp)은 여기 넣지 않는다. 기관의 실무 안내와 달리 해석례는 조문의
# 뜻을 정하는 자료라 무게가 다르다. 코퍼스에 들어오면 그때 따로 다룬다.
GUIDE_TYPES = ("guide",)

# 청크 규격의 출처 헤더. docs/chunk-schema.md 와 validate_chunks 가 쓰는 것과 같다.
_HEADER = re.compile(r"^\[.+?\]")


@dataclass(frozen=True)
class Corpus:
    """한 묶음의 검색 설정.

    검색 파라미터와 질의 확장을 묶음별로 분리한다. 법령에 필요한 보강이 평가되지
    않은 판례·안내 순위에 번지지 않아야 한다.
    """

    name: str
    doc_types: tuple[str, ...]
    bm25_b: float = 0.75
    expand_weight: float = 1.0
    bm25_weight: float = 1.0
    dense_weight: float = 1.0
    rrf_k: int = DEFAULT_RRF_K
    query_expander: Callable[[str], list[str]] = expand
    exclude_titles: tuple[str, ...] = ()
    status: str = "current"
    include_ids: tuple[str, ...] = ()   # 이 article_id 만 (안내 주제 한정에 쓴다)

    def where(self) -> dict:
        """이 묶음만 남기는 메타데이터 필터.

        status 를 거르는 이유는 이것이 법률 서비스이기 때문이다. 폐지되거나 옛
        버전인 조문을 근거로 답하면 사용자가 지금 없는 권리를 믿게 된다. 청크 규격
        (docs/chunk-schema.md)도 검색 기본 필터를 {"status": "current"} 로 정하고 있다.

        조건이 둘 이상이면 $and 로 묶는다. Chroma 가 키를 나란히 두는 것을 거부하고,
        메모리 필터도 같은 문법을 받도록 맞춰 두었다.
        """
        conditions: list[dict] = [{"doc_type": {"$in": list(self.doc_types)}}]
        if self.status:
            conditions.append({"status": self.status})
        if self.exclude_titles:
            conditions.append({"title": {"$nin": list(self.exclude_titles)}})
        if self.include_ids:
            conditions.append({"article_id": {"$in": list(self.include_ids)}})
        return conditions[0] if len(conditions) == 1 else {"$and": conditions}


# 안내 묶음 제목에 사용 지침을 함께 싣는다. 안내 코퍼스가 작아 관련 없는 질문에도
# 상위 몇 건이 항상 딸려 나오는데, 생성 쪽 프롬프트가 "아래 근거를 바탕으로 답하라"
# 라고만 쓰면 모델이 무관한 안내를 억지로 끼워 넣는다. 지시가 근거와 함께 가야
# 프롬프트를 누가 쓰든 지켜진다.
GUIDE_HEADER = (
    "## 참고 안내 (법적 근거가 아닌 기관 안내)\n"
    "아래 자료는 질문과 관련될 때만 사용하세요. 관련이 없으면 무시하고 언급하지 "
    "마세요. 법령이 아니므로 \"법에 따르면\" 이라고 인용하지 말고 어느 기관의 "
    "안내인지 밝혀 주세요.\n"
)

LAW_RRF_K = 5

# 생성 모델에 법령 3건만 넘길 때 dev-003·008의 정답이 기존 k=60에서는 4위로
# 잘렸다. 후보 깊이와 가중치는 그대로 두고 k만 5로 낮추면 두 문항이 3위가 되며,
# 현재 법령 채점 24문항의 Hit@1은 유지되고 Hit@3은 22/24 -> 24/24가 된다.
# 이후 법령 문맥 확장은 Hit@3을 유지하면서 Recall@3을 97.9% -> 100%로 만든다.
# 판례는 공식 원문 평가 전이므로 이 값을 공유하지 않는다.
LAW = Corpus("법령", LAW_TYPES, rrf_k=LAW_RRF_K, query_expander=expand_law)
CASE = Corpus("판례", CASE_TYPES)

# 민법은 기존 법령과 같은 BM25 묶음에 넣지 않는다. 검색 때 필터로만 빼면 IDF는
# 이미 민법을 포함해 계산되어 민법이 결과에 보이지 않아도 기존 순위가 흔들린다.
# 생성 모델에 넘기는 법령 3칸 중 질문 의도가 확인될 때만 0~2칸을 사용한다.
CIVIL_TITLE = "민법"
CIVIL_ARTICLE_IDS = (
    "민법-제623조",
    "민법-제626조",
    "민법-제627조",
    "민법-제629조",
    "민법-제632조",
    "민법-제634조",
    "민법-제640조",
)
CIVIL = Corpus(
    "민법",
    LAW_TYPES,
    rrf_k=LAW_RRF_K,
    query_expander=expand_civil,
    include_ids=CIVIL_ARTICLE_IDS,
)

# 공식 안내를 따로 두는 이유가 있다. HUG 상품안내나 국세청 민원안내는 **법적 근거가
# 아니라 실무 안내**다. 법령과 한 묶음으로 넘기면 모델이 "법에 따르면 보증 한도는…"
# 같은 문장을 쓴다. 조문 5칸 중 하나를 안내가 먹는 문제도 있다.
#
# 그래도 넣어야 한다. "전세보증금반환보증이 뭔가요?" 는 조문으로 답할 수 없는데,
# 안내가 없으면 검색기가 엉뚱한 조문을 내놓고 is_empty() 도 False 라 ABSTAIN 으로
# 걸러지지 않는다. 못 찾는 것보다 나쁘다.
GUIDE = Corpus("안내", GUIDE_TYPES)

# 코퍼스에 들어 있는 상가 법령. 주택 질문에서는 빼야 한다.
COMMERCIAL_LAWS = ("상가건물 임대차보호법", "상가건물 임대차보호법 시행령")

# 상가 임대차에서만 쓰는 말들. 주택 질문에는 거의 나오지 않는다.
# "영업" 같은 넓은 말은 넣지 않았다. 오탐이 나면 주택 질문에 상가 조문이 섞인다.
COMMERCIAL_SIGNS = ("상가", "점포", "가게", "사무실", "권리금", "환산보증금")


def mentions_commercial(question: str) -> bool:
    return any(sign in question for sign in COMMERCIAL_SIGNS)


def route_law_corpus(question: str, base: Corpus = LAW) -> Corpus:
    """질문에 맞는 법령 범위를 고른다.

    **기본은 주택이다.** LENS는 주택임대차 서비스이고, 코퍼스의 법령 133청크 중
    57청크(43%)가 상가 법령이라 그대로 두면 주택 질문에서 상가 조문이 상위를
    차지한다. 실제로 "집주인이 바뀌면" 질문에 상가건물 임대차보호법 제5조가
    1위로 올라왔다.

    상가 신호가 있으면 빼지 않는다. **상가로 바꾸는 것이 아니라 제외를 푸는 것**이다.
    "상가주택"처럼 둘 다 걸린 질문에서 주택 조문이 사라지면 안 된다.

    한계: 낱말 표에 없는 표현은 잡지 못한다. 평가셋 27문항에 상가 질문이 하나도
    없어 이 분기는 검색 성능으로 검증하지 못했다. 판정 자체는 테스트로 잠갔다.
    """
    # 민법은 상가 여부와 관계없이 별도 검색한다. 기본 법령 검색에 섞이면 질문과
    # 무관한 민법이 생성 근거를 차지하고, BM25 IDF도 바뀐다.
    if mentions_commercial(question):
        return replace(base, exclude_titles=(CIVIL_TITLE,))
    return replace(base, exclude_titles=COMMERCIAL_LAWS + (CIVIL_TITLE,))


@dataclass(frozen=True)
class CivilTopic:
    """질문에서 확인된 민법 임대차 주제와 가져올 단일 조문."""

    name: str
    article_id: str


def _has_any(question: str, signals: tuple[str, ...]) -> bool:
    return any(signal in question for signal in signals)


def detect_civil_topics(question: str) -> tuple[CivilTopic, ...]:
    """민법 임대차가 직접 필요한 질문만 최대 두 주제로 분류한다.

    넓은 단어 하나로 발동시키지 않는다. 예를 들어 ``월세가 밀렸다``만으로는
    제640조를 내지 않고, 계약 해지 의도까지 있어야 한다. 갱신·재계약 질문은
    주택임대차보호법 제6조의3이 중심이므로 제640조를 명시적으로 막는다.
    """
    q = question.strip()
    if not q:
        return ()

    topics: list[CivilTopic] = []
    # 명시적인 고장·수리 표현은 그 자체로 충분하다. 반면 "작동" 같은 일반어와
    # 설비 이름은 각각 단독으로 쓰일 수 있으므로 반드시 **설비 + 고장 증상**을
    # 함께 확인한다. 그렇지 않으면 "확정일자 제도는 어떻게 작동하나요?"까지
    # 제623조로 잘못 분류된다.
    explicit_repair = _has_any(
        q, ("고장", "수리", "고치", "고쳐", "고쳤", "망가", "하자가", "하자를", "하자 발생"),
    )
    equipment = _has_any(
        q,
        ("보일러", "온수", "난방", "에어컨", "세탁기", "냉장고", "싱크대", "변기",
         "인덕션", "가스레인지", "전등"),
    )
    malfunction = _has_any(
        q,
        ("안 나오", "안 나와", "작동을 안", "작동이 안", "작동 안", "작동하지 않",
         "안 켜", "안 돌아가", "안 내려가", "막히", "막혀", "멈췄", "멈추"),
    )
    repair = explicit_repair or (equipment and malfunction)
    # 수리와 지출은 같은 문장에 있어야 한다. "수리는 끝냈어요. 계약금을
    # 냈습니다"를 연결하지 않고, "끝냈어요"도 지출 표현으로 세지 않는다.
    payment_sentences = [
        sentence for sentence in re.split(r"[.!?\n]+", q)
        if re.search(r"(?<![가-힣])(?:냈어요|냈습니다|냈는데|지불했|결제했)", sentence)
    ]
    other_money = ("계약금", "보증금", "예약금", "잔금", "월세", "차임", "관리비", "중개수수료", "중개보수")
    repair_payment = any(
        _has_any(sentence, ("수리", "수선", "고치", "고쳐", "고쳤"))
        and not _has_any(sentence, other_money)
        for sentence in payment_sentences
    )
    explicit_reclaim = _has_any(q, ("수리비 달라", "수리비를 달라", "수선비 달라", "수선비를 달라"))
    # 다른 비용도 지급했다면 "이 돈"의 대상을 추정하지 않는다.
    implicit_reclaim = _has_any(q, ("이 돈 달라", "이 돈을 달라", "이 돈 받을", "이 돈을 받을")) and not any(
        _has_any(sentence, other_money) for sentence in payment_sentences
    )
    paid_repair_reclaim = repair_payment and (explicit_reclaim or implicit_reclaim)
    # 새 간접 표현은 수리 완료/지출과 반환 대상을 함께 확인한다. 다른 돈이
    # 등장하면 일반적인 "비용", "먼저 낸 돈"의 대상을 임의로 연결하지 않는다.
    sentences = re.split(r"[.!?\n]+", q)
    completed_repair = repair_payment or any(
        re.search(r"(?:수리|수선)(?:를)?\s*(?:마쳤|끝냈|했)|고쳤|교체했", sentence)
        and not _has_any(sentence, other_money)
        for sentence in sentences
    )
    cost_return = any(
        re.search(r"(?:수리비|수선비|교체비(?:용)?|비용)(?:을|를)?\s*돌려달라", sentence)
        and not _has_any(sentence, other_money)
        and not re.search(r"돌려달라.*(?:않|아니|안\s*했)", sentence)
        for sentence in sentences
    )
    prepaid_question = any(
        re.search(r"먼저\s*낸\s*(?:[\d,]+\s*만?\s*원|돈|비용).*누구(?:한테|에게)", sentence)
        for sentence in sentences
    )
    # 임대인 주어 뒤의 수리 명사만으로는 부족하다. "수리를 안 해줘서 제가"
    # 같은 문장은 빼고, 주어 전환 전의 완료/지출/반환 요구 동사를 확인한다.
    landlord_expense_actor = any(
        re.search(
            r"(?:수리|수선)(?:를)?\s*(?:했|마쳤|끝냈|해\s*줬|해주었|완료했)"
            r"|교체했|고쳤"
            r"|(?:수리비|수선비|교체비(?:용)?|비용|돈)(?:을|를)?\s*"
            r"(?:돌려달라|냈|지불했|결제했)", actor_clause,
        )
        and not re.search(r"(?:했다고|했다는).*아니", actor_clause)
        for sentence in sentences
        for actor_clause in re.findall(
            r"(?:집주인|임대인)(?:이|은|가)((?:(?!제가|저는|임차인).)*)", sentence,
        )
    )
    linked_reclaim = not landlord_expense_actor and not _has_any(q, other_money) and (
        (completed_repair and cost_return) or (repair_payment and prepaid_question)
    )
    reimbursement = repair and (linked_reclaim or paid_repair_reclaim or _has_any(
        q,
        ("제 돈", "먼저 내", "먼저 냈", "먼저 지불", "비용을 받", "비용 받을",
         "돌려받", "청구", "업체 불러서 고쳤", "사람 불러 고쳤"),
    ))
    unusable = _has_any(q, ("누수", "물이 새", "물 새", "곰팡이", "침수")) and _has_any(
        q,
        ("못 쓰", "쓰지 못", "사용할 수 없", "사용하지 못", "살 수 없", "생활이 안",
         "월세를 깎", "월세 깎", "월세를 줄", "월세 줄", "감액", "중간에 나가",
         "계약을 정리", "해지"),
    )
    # 공사·단수 언급만으로 감액 조문을 추가하지 않는다. 물 사용의 불능이
    # 같은 문장에 명시된 경우만 확장하며, 숙박비 자체의 배상 판단과 구분한다.
    water_unusable = any(
        _has_any(sentence, ("단수", "배관 공사", "배관공사"))
        and re.search(
            r"물(?:을|은|이)?(?:\s*(?:약|일주일|이주일|한 달|\d+\s*(?:일|시간)|동안|간))*"
            r"\s*(?:못\s*쓰|못\s*쓴|쓰지\s*못|사용할\s*수\s*없)", sentence,
        )
        and not re.search(r"(?:못\s*쓰|못\s*쓴|사용할\s*수\s*없).*(?:아니|않)", sentence)
        for sentence in sentences
    )
    unusable = unusable or water_unusable
    # "금이"를 부분 문자열로 찾으면 "보증금이/계약금이"도 균열로 오인한다.
    crack = bool(re.search(r"(?<![가-힣A-Za-z0-9])금이", q))
    notice = (crack or _has_any(q, ("고장", "하자", "누수", "물이 새", "물 새", "곰팡이", "수리"))) and _has_any(
        q, ("알려", "말해야", "말 안 하고", "통지", "연락해야", "연락 안 하고")
    )
    arrears = _has_any(q, ("월세", "차임")) and _has_any(q, ("밀", "연체"))
    termination = _has_any(q, ("계약을 끝", "계약 끝내", "계약 해지", "해지하", "나가라", "바로 나가", "쫓아내"))
    renewal = _has_any(q, ("갱신", "재계약", "연장", "다음 계약"))

    # 전대는 "누구에게" 와 "어떻게" 를 따로 본다. 한 덩어리 문구로 잡으면 사이에
    # 말이 끼는 순간 놓친다 — "친구에게 빌려" 는 "친구에게 **방을 다시** 빌려" 를
    # 잡지 못했다.
    sublet_target = _has_any(
        q, ("친구", "다른 사람", "다른사람", "제3자", "타인", "방 하나", "방하나"),
    )
    sublet_action = _has_any(
        q, ("빌려주", "빌려줘", "빌려 주", "세를 놓", "세를 주", "들여",
            "쓰게 하", "살게 하", "넘겨", "넘기", "돈을 받고 살", "돈을 조금 받"),
    )
    # **집주인이** 남에게 세를 놓는 경우는 전대가 아니다. 갱신 거절 후 실거주
    # 위반(주임법 제6조의3)이라 민법을 넣으면 근거가 틀린다.
    landlord_relet = _has_any(
        q, ("실제로 살", "직접 산다", "직접 살", "내보낸", "내보내", "손해배상"),
    )
    sublet = (
        _has_any(q, ("전대", "재임대"))
        or (sublet_target and sublet_action and not landlord_relet and not renewal)
    )

    # 더 구체적인 권리부터 담고, 하나의 수리 질문에서 필요비와 수선의무가 함께
    # 필요한 경우에만 두 조문을 쓴다.
    if sublet:
        topics.append(CivilTopic("무단 전대", "민법-제629조"))
        topics.append(CivilTopic("소부분 사용 예외", "민법-제632조"))
    if arrears and termination and not renewal and not mentions_commercial(q):
        topics.append(CivilTopic("차임 연체 해지", "민법-제640조"))
    if reimbursement:
        topics.append(CivilTopic("필요비 상환", "민법-제626조"))
    if unusable:
        topics.append(CivilTopic("사용불능·차임 감액", "민법-제627조"))
    if notice:
        topics.append(CivilTopic("하자 통지", "민법-제634조"))
    if repair and not unusable and not notice:
        topics.append(CivilTopic("수선의무", "민법-제623조"))
    elif reimbursement and len(topics) < 2:
        topics.append(CivilTopic("수선의무", "민법-제623조"))

    deduplicated: list[CivilTopic] = []
    for topic in topics:
        if topic.article_id not in {item.article_id for item in deduplicated}:
            deduplicated.append(topic)
    return tuple(deduplicated[:2])


@dataclass(frozen=True)
class GuideTopic:
    """안내 문서 하나와 그 문서를 부르는 질문의 신호어.

    안내는 법령·판례와 달리 **질문이 그 주제일 때만** 내보낸다. 전체 안내가
    6청크뿐이라 아무 질문에나 검색하면 항상 상위 몇 건이 나온다. 임계 유사도로
    거르는 방법도 있지만 문서 2건·표본 5개로 문턱을 정하면 그 표본에 맞춘 값이
    된다. 지금 단계에서는 재현 가능한 주제 조건이 안전하다.
    """

    name: str
    guide_id: str
    signals: tuple[str, ...]


GUIDE_TOPICS: tuple[GuideTopic, ...] = (
    GuideTopic(
        "보증보험",
        "guide-HUG-전세보증금반환보증",
        # "보증금" 단독은 넣지 않는다. 전세 질문 대부분에 나와 모든 질문이 걸린다.
        # "전세보증" 은 넣지 않는다. "전세보증금은 언제 돌려받나요" 까지 걸린다.
        ("전세보증금반환보증", "전세보증금 반환보증", "반환보증", "보증보험",
         "HUG", "hug", "주택도시보증",
         "보증 가입", "보증가입", "보증료", "보증 신청", "보증신청",
         "보증한도", "보증 한도", "보증대상", "보증 대상", "위탁 금융기관"),
    ),
    GuideTopic(
        "미납국세",
        "guide-국세청-미납국세열람",
        # "체납" 단독은 넣지 않는다. 차임·월세·관리비 체납 질문까지 끌어와
        # 임대인의 세금 안내가 붙는다. 세금 맥락이 드러나는 말만 신호로 쓴다.
        ("미납국세", "미납 국세", "미납 세금", "밀린 세금", "국세 열람", "국세열람",
         "세금 체납", "국세 체납", "세금 체납액", "국세 체납액", "체납 국세", "납세증명",
         "세금을 안 낸", "세금 안 낸", "세금은 안 낸",
         "임대인 세금", "집주인 세금", "임대인의 세금", "집주인의 세금"),
    ),
)

# 두 번째 청크를 넣을지 볼 때 무시하는 낱말. 어디에나 나와서 신호가 되지 못한다.
_COMMON = frozenset({"경우", "해당", "가능", "안내", "확인", "필요", "내용", "제도",
                     "어떻게", "무엇", "얼마", "언제", "어디"})

# 낱말 끝의 조사. 떼지 않으면 "절차가" 가 본문의 "절차" 와 맞지 않는다.
# 긴 것부터 본다 ("으로" 를 "로" 보다 먼저).
_PARTICLES = ("으로부터", "에서부터", "이라도", "으로", "까지", "부터", "에서", "보다",
              "에게", "한테", "라도", "이나", "이란", "이라", "은", "는", "이", "가",
              "을", "를", "과", "와", "의", "에", "도", "로", "만", "랑")


def detect_guide_topics(question: str) -> tuple[GuideTopic, ...]:
    """질문이 어느 안내 주제인지. 해당 없으면 빈 튜플."""
    return tuple(
        topic
        for topic in GUIDE_TOPICS
        if any(signal in question for signal in topic.signals)
    )


def _adds_to(second: str, first: str, question: str) -> bool:
    """두 번째 청크가 첫 번째에 없는 내용을 더하는지.

    순위가 2위라는 이유만으로 넣지 않는다. 질문의 낱말 중 **첫 청크에는 없고 두
    번째에는 있는** 것이 있을 때만 넣는다. 그래야 "신청 조건과 절차" 처럼 두
    부분이 필요한 질문에서만 두 건이 나간다.

    질문 낱말을 그대로 쓰면 "보증" 같은 주제어가 모든 청크에 있어 항상 통과한다.
    첫 청크와 대조하는 방식이 그 문제를 함께 푼다.
    """
    return any(
        word in second and word not in first for word in _query_stems(question)
    )


def _query_stems(question: str) -> set[str]:
    """질문에서 대조에 쓸 낱말. 끝의 조사를 뗀다."""
    stems: set[str] = set()
    for word in _WORD_RE.findall(question):
        for particle in _PARTICLES:
            if word.endswith(particle) and len(word) - len(particle) >= 2:
                word = word[: -len(particle)]
                break
        if len(word) >= 2 and word not in _COMMON:
            stems.add(word)
    return stems


@dataclass(frozen=True)
class Evidence:
    """LLM 에 넘길 근거 한 조각.

    citation 을 따로 두는 이유는 답변에 출처를 적게 하기 위해서다. 본문만 넘기면
    모델이 "관련 법에 따르면" 같은 문장을 쓰고, 사용자는 확인할 방법이 없다.
    """

    rank: int
    chunk_id: str
    doc_type: str
    citation: str
    text: str
    score: float
    source_url: str = ""

    def as_prompt_block(self) -> str:
        """프롬프트에 그대로 넣을 수 있는 한 덩어리.

        청크는 규격상 `[법령명 제N조(제목)]` 헤더로 시작한다 — 현재 코퍼스
        165건 전부가 그렇다. 그 앞에 citation 을 또 붙이면 같은 문장이 두 번
        들어간다. 헤더가 있으면 본문을 그대로 쓴다.

        citation 필드 자체는 남긴다. 화면에 출처만 따로 보여주거나 링크를 걸 때
        본문에서 헤더를 다시 떼어내지 않아도 되기 때문이다.
        """
        if _HEADER.match(self.text):
            return f"[{self.rank}] {self.text}"
        return f"[{self.rank}] {self.citation}\n{self.text}"


@dataclass
class RetrievalResult:
    question: str
    laws: list[Evidence] = field(default_factory=list)
    cases: list[Evidence] = field(default_factory=list)
    guides: list[Evidence] = field(default_factory=list)
    civil_topics: tuple[str, ...] = ()

    def as_prompt_context(self) -> str:
        """법령과 판례를 구분해 붙인 근거 묶음.

        구분을 유지하는 이유가 있다. 판례는 그 사건의 사실관계 위에서 나온 판단이라
        조문과 같은 무게로 읽으면 안 된다. 섞어서 넘기면 모델이 판례의 문장을
        법조문처럼 인용한다.
        """
        parts: list[str] = []
        if self.laws:
            parts.append(
                "## 관련 법령\n" + "\n\n".join(e.as_prompt_block() for e in self.laws)
            )
        if self.cases:
            parts.append(
                "## 관련 판례\n" + "\n\n".join(e.as_prompt_block() for e in self.cases)
            )
        if self.guides:
            # 안내는 맨 뒤에 두고 법적 근거가 아님을 제목에 박는다. 조문과 같은
            # 무게로 읽으면 모델이 "법에 따르면 보증 한도는…" 같은 문장을 쓴다.
            parts.append(
                GUIDE_HEADER
                + "\n\n".join(e.as_prompt_block() for e in self.guides)
            )
        return "\n\n".join(parts)

    def is_empty(self) -> bool:
        return not self.laws and not self.cases and not self.guides


def citation_of(metadata: dict) -> str:
    """청크만 떼어 봐도 어디서 왔는지 알 수 있는 한 줄.

    판례는 사건명만으로 무의미하다 — "추심금", "배당이의"는 여럿이다. 법원과
    사건번호가 있어야 답변에서 인용할 수 있다.
    """
    if metadata.get("doc_type") in GUIDE_TYPES:
        agency_title = metadata.get("title", "")
        topic = metadata.get("topic", "")
        return f"{agency_title}({topic})" if topic else agency_title

    if metadata.get("doc_type") in CASE_TYPES:
        head = " ".join(
            x for x in (metadata.get("court_name", ""), metadata.get("case_number", "")) if x
        )
        decided = metadata.get("decision_date", "")
        name = metadata.get("title", "")
        tail = f" ({decided} 선고)" if decided else ""
        return f"{head} {name}{tail}".strip()

    label = f"{metadata.get('title', '')} {metadata.get('article_no', '')}".strip()
    article_title = metadata.get("article_title", "")
    return f"{label}({article_title})" if article_title else label


def _to_evidence(rank: int, chunk: dict, score: float) -> Evidence:
    metadata = chunk["metadata"]
    return Evidence(
        rank=rank,
        chunk_id=chunk["chunk_id"],
        doc_type=str(metadata.get("doc_type", "")),
        citation=citation_of(metadata),
        text=chunk["text"],
        score=round(float(score), 4),
        source_url=str(metadata.get("source_url", "")),
    )


def split_by_type(chunks: list[dict], doc_types: tuple[str, ...]) -> list[dict]:
    return [c for c in chunks if c["metadata"].get("doc_type") in doc_types]


class RetrievalService:
    """질문 하나에 법령 TOP-k 와 판례 TOP-k 를 돌려준다.

    임베딩 모델은 한 번만 올린다. KURE-v1 은 2.3GB 라 질의마다 올리면 쓸 수 없다.
    Streamlit 에서는 이 객체를 `@st.cache_resource` 로 감싸면 된다.
    """

    def __init__(
        self,
        chunks: list[dict],
        dense: Retriever | None = None,
        law: Corpus = LAW,
        case: Corpus = CASE,
        guide: Corpus = GUIDE,
        civil: Corpus = CIVIL,
        civil_dense: Retriever | None = None,
    ) -> None:
        """chunks 는 법령·판례가 섞여 있어도 된다. doc_type 으로 갈라 쓴다.

        dense 가 None 이면 어휘 검색만으로 동작한다. 임베딩 없이도 결과가 나와야
        인덱스가 아직 없는 환경에서 앱을 띄울 수 있다.
        """
        self.dense = dense
        self.civil_dense = civil_dense
        self.corpora = (law, case, guide)
        self.civil = civil
        self._chunks = {c["chunk_id"]: c for c in chunks}
        self._retrievers = {}
        for corpus in self.corpora:
            corpus_chunks = split_by_type(chunks, corpus.doc_types)
            if corpus.name == law.name:
                # 민법이 결과에 안 보여도 같은 BM25 색인에 있으면 IDF가 바뀐다.
                # 기존 법령 순위를 그대로 보존하려고 색인 단계부터 분리한다.
                corpus_chunks = [
                    chunk for chunk in corpus_chunks
                    if chunk["metadata"].get("title") != CIVIL_TITLE
                ]
            self._retrievers[corpus.name] = self._build(corpus, corpus_chunks)

        civil_chunks = [
            chunk for chunk in split_by_type(chunks, civil.doc_types)
            if chunk["metadata"].get("article_id") in set(civil.include_ids)
        ]
        self._retrievers[civil.name] = self._build(civil, civil_chunks)
        self._warn_if_civil_missing(civil, civil_chunks)

    @staticmethod
    def _warn_if_civil_missing(civil: Corpus, civil_chunks: list[dict]) -> None:
        """민법 조문이 코퍼스에 없으면 알린다.

        없으면 조건부 검색이 **조용히** 아무것도 내지 않는다. 주제 판정은 정상
        동작하므로 로그에도 흔적이 남지 않고, 재적재를 빠뜨린 사람은 "민법이 안
        나온다" 는 증상만 보게 된다. 서비스는 계속 떠야 하므로 예외는 던지지
        않고 경고만 남긴다.
        """
        found = {
            chunk["metadata"].get("article_id") for chunk in civil_chunks
        }
        missing = [article for article in civil.include_ids if article not in found]
        if not missing:
            return
        logger.warning(
            "민법 조문 %d/%d건이 코퍼스에 없어 조건부 검색이 동작하지 않습니다: %s\n"
            "  python -m src.ingestion.fetch_minbeop --records data/parsed/minbeop_records.jsonl\n"
            "  python -m src.ingestion.load_laws --records data/parsed/minbeop_records.jsonl "
            "--export data/chunks/chunks.jsonl\n"
            "  민법 벡터는 기본 인덱스와 분리해야 합니다. docs/patch006-civil-routing.md를 확인하세요.",
            len(missing), len(civil.include_ids), ", ".join(missing),
        )

    def _build(self, corpus: Corpus, chunks: list[dict]) -> HybridRetriever | None:
        """묶음 하나에 대한 검색기. 청크가 없으면 만들지 않는다."""
        if not chunks:
            return None
        members = [
            Member(
                BM25Retriever(
                    chunks,
                    b=corpus.bm25_b,
                    query_expander=corpus.query_expander,
                ),
                f"{corpus.name}-bm25",
                corpus.bm25_weight,
                corpus.expand_weight,
            )
        ]
        dense = self.civil_dense if corpus.name == self.civil.name and self.civil_dense is not None else self.dense
        if dense is not None:
            members.append(
                Member(dense, f"{corpus.name}-dense", corpus.dense_weight, 0.0)
            )
        return HybridRetriever(members, rrf_k=corpus.rrf_k)

    @classmethod
    def from_index(
        cls,
        chunk_paths: tuple[Path | str, ...] = (LAW_CHUNKS, CASE_CHUNKS, GUIDE_CHUNKS),
        index_path: Path | str = DEFAULT_INDEX,
        model: str = DEFAULT_MODEL,
        civil_index_path: Path | str = DEFAULT_CIVIL_INDEX,
    ) -> "RetrievalService":
        """앱에서 쓰는 방식. 벡터는 Chroma 에서 읽으므로 재임베딩이 없다."""
        from src.retrieval.dense import ChromaRetriever

        chunks = _load_index_chunks(chunk_paths)
        backend = SentenceTransformerEmbedding(model)
        dense = ChromaRetriever(backend, index_path)
        if dense.collection.get(where={"title": CIVIL_TITLE}, include=[])["ids"]:
            raise ValueError("기본 인덱스에 민법이 섞여 있습니다. 기존 기본 인덱스를 복구하고 민법을 별도 색인하세요.")
        civil_dense = None
        if any(c["metadata"].get("article_id") in CIVIL_ARTICLE_IDS for c in chunks):
            if not (Path(civil_index_path) / "chroma.sqlite3").is_file():
                raise ValueError("민법 청크가 있으나 별도 민법 인덱스가 없습니다. PATCH-006 적재 절차를 확인하세요.")
            civil_dense = ChromaRetriever(backend, civil_index_path)
        return cls(chunks, dense, civil_dense=civil_dense)

    @classmethod
    def from_files(
        cls,
        chunk_paths: tuple[Path | str, ...] = (LAW_CHUNKS, CASE_CHUNKS, GUIDE_CHUNKS),
        backend: EmbeddingBackend | None = None,
    ) -> "RetrievalService":
        """Chroma 없이 메모리에서 임베딩한다. 평가·실험용."""
        chunks = _load_all(chunk_paths)
        backend = backend or SentenceTransformerEmbedding(DEFAULT_MODEL)
        return cls(chunks, DenseRetriever(chunks, backend))

    def _search_one(self, corpus: Corpus, question: str, k: int) -> list[Evidence]:
        # 라우팅으로 만든 사본도 이름은 같다. 검색기는 이름으로 찾는다.
        retriever = self._retrievers.get(corpus.name)
        if retriever is None or k <= 0:
            return []
        # 필터를 함께 넘긴다. BM25 는 이미 쪼갠 코퍼스를 보지만, 공유 Chroma 는
        # 이 필터가 없으면 다른 묶음의 문서까지 끌어온다.
        hits = retriever.search(question, k, corpus.where())
        return [
            _to_evidence(rank, self._chunks[chunk_id], score)
            for rank, (chunk_id, score) in enumerate(hits, start=1)
            if chunk_id in self._chunks
        ]

    def search(
        self,
        question: str,
        k_law: int = 5,
        k_case: int = 5,
        k_guide: int = 2,
    ) -> RetrievalResult:
        """법령 k_law 건, 판례 k_case 건, 공식 안내 k_guide 건을 각각 뽑는다.

        k_guide 는 **상한**이다. 실제 건수는 질문 주제에 따라 0~k_guide 로 달라진다
        (`_search_guides` 참고). 안내가 법적 근거가 아니라 실무 절차를 보태는
        자리이므로 상한을 낮게 둔다. 0 으로 주면 안내를 끄는 것이다.

        질문이 비어 있으면 빈 결과를 준다. BM25 는 토큰이 없어 스스로 아무것도
        내지 않지만, 임베딩은 공백도 벡터로 바꿔 아무 문서나 가장 가까운 것으로
        돌려준다. 그대로 두면 사용자가 엔터만 쳐도 무관한 근거 10건이 LLM 에
        넘어간다.
        """
        if not question or not question.strip():
            return RetrievalResult(question=question)

        law, case, guide = self.corpora
        topics = detect_civil_topics(question) if k_law > 0 else ()
        civil_laws = self._search_civil(question, topics, min(2, k_law))
        standard_laws = self._search_one(
            route_law_corpus(question, law),
            question,
            max(0, k_law - len(civil_laws)),
        )
        # 민법은 질문 의도를 직접 설명하는 조문이라 앞에 둔다. Evidence.rank는
        # 각 검색기의 내부 순위가 아니라 LLM이 보는 최종 순위로 다시 매긴다.
        laws = [
            replace(evidence, rank=rank)
            for rank, evidence in enumerate(civil_laws + standard_laws, start=1)
        ]
        return RetrievalResult(
            question=question,
            laws=laws,
            cases=self._search_one(case, question, k_case),
            guides=self._search_guides(guide, question, k_guide),
            civil_topics=tuple(topic.name for topic in topics if any(
                evidence.citation.startswith(CIVIL_TITLE)
                and topic.article_id == self._chunks[evidence.chunk_id]["metadata"].get("article_id", "")
                for evidence in civil_laws
            )),
        )

    def _search_civil(
        self,
        question: str,
        topics: tuple[CivilTopic, ...],
        limit: int,
    ) -> list[Evidence]:
        """감지된 민법 주제마다 정확히 한 조문씩, 최대 ``limit``건을 가져온다."""
        if not topics or limit <= 0:
            return []

        picked: list[Evidence] = []
        for topic in topics:
            found = self._search_one(
                replace(self.civil, include_ids=(topic.article_id,)), question, 1
            )
            if found:
                picked.append(found[0])
            if len(picked) >= limit:
                break
        return picked

    def _search_guides(
        self, corpus: Corpus, question: str, limit: int
    ) -> list[Evidence]:
        """안내는 질문이 그 주제일 때만, 0~limit 건을 가변으로 낸다.

        법령·판례처럼 고정 개수로 내보내면 관련 없는 질문에도 항상 따라붙는다.
        안내가 6청크뿐이라 어떤 질문에도 상위 몇 건이 나오기 때문이다.

        규칙:
          주제 없음        -> 0건
          주제 하나        -> 가장 관련 있는 1건. 두 번째는 질문의 낱말을 직접
                              담고 있을 때만 넣는다(순위 2위라는 이유만으로는 안 넣음)
          주제 둘          -> 각 주제에서 1건씩
        """
        topics = detect_guide_topics(question)
        if not topics or limit <= 0:
            return []

        if len(topics) == 1:
            found = self._search_one(
                replace(corpus, include_ids=(topics[0].guide_id,)), question, min(2, limit)
            )
            if len(found) > 1 and not _adds_to(found[1].text, found[0].text, question):
                found = found[:1]
            return found[:limit]

        picked: list[Evidence] = []
        for topic in topics[:limit]:
            hit = self._search_one(
                replace(corpus, include_ids=(topic.guide_id,)), question, 1
            )
            picked.extend(hit)
        # 순위는 묶음 안에서 다시 매긴다.
        return [
            Evidence(i, e.chunk_id, e.doc_type, e.citation, e.text, e.score, e.source_url)
            for i, e in enumerate(picked[:limit], start=1)
        ]


def _load_all(paths: tuple[Path | str, ...]) -> list[dict]:
    """있는 파일만 읽어 합친다. 한쪽이 없어도 나머지로 동작해야 한다."""
    chunks: list[dict] = []
    for path in paths:
        path = Path(path)
        if path.exists():
            chunks.extend(load_chunks(path))
    return chunks


def _load_index_chunks(paths: tuple[Path | str, ...]) -> list[dict]:
    """색인 ID를 원문 청크와 연결한다.

    새 클론에는 data/chunks 산출물이 없지만, 샘플 청크를 색인한 경우는 지원한다.
    기본 청크 파일이 하나라도 있으면 그 파일만 사용해 기존 운영 경로를 보존한다.
    """
    chunks = _load_all(paths)
    if chunks or not SAMPLE_CHUNKS.exists():
        return chunks

    logger.info("생성 청크가 없어 샘플 청크를 검색 원문으로 사용합니다: %s", SAMPLE_CHUNKS)
    return load_chunks(SAMPLE_CHUNKS)
