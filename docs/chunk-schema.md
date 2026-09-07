# Chroma 인덱싱 사양

SQLite 지식 DB의 청크를 Chroma 컬렉션에 적재할 때 지켜야 할 규격이다.
인덱싱 담당과 검색 담당 사이의 인터페이스 약속이며, 이 형식만 지키면
검색기와 평가 하네스는 **연결 대상만 바꿔서 그대로 돌아간다.**

- 원본: `src/database/schema.sql` (SQLite, 관계 보존)
- 파생: `data/index/chroma` 컬렉션 `knowledge_chunks` (검색용)

---

## 1. 핵심 — Chroma 에는 JOIN 이 없다

SQLite 에서는 조인으로 가져오던 정보를, Chroma 에서는 **적재 시점에 미리
평평하게 펼쳐 넣어야** 한다. 검색할 때 조인해서 채울 수 없다.

```
chunks ─→ documents(title, status, source_url)
       └─→ law_articles(article_number, article_title)
              └─→ law_versions(effective_from)
```

위 값들이 Chroma metadata 에 들어 있지 않으면 **필터를 아예 걸 수 없다.**
목업 코퍼스 135청크에서 잰 필터 효과는 다음과 같다.

| | Hit@5 | MRR |
| --- | --- | --- |
| 필터 없음 | 69.6% | 0.446 |
| 문서 단위 필터 적용 | 73.9% | 0.581 |

빠뜨리면 재인덱싱이고, 재인덱싱은 **임베딩을 전부 다시 계산**하는 것이라
비용과 시간이 다시 든다.

---

## 2. 변환표

`collection.add()` 에 넘길 값을 아래와 같이 구성한다.

### ids

```
chunks.chunk_id
```

`upsert` 로 적재해 재실행해도 중복이 생기지 않게 한다.

### documents (임베딩·검색 대상 본문)

```
chunks.content
```

본문 앞에 `[법령명 제○조(조문제목)]` 헤더를 붙인다.

```
[주택임대차보호법 제3조의2(보증금의 회수)]
② 제3조제1항ㆍ제2항 또는 제3항의 대항요건과 ...
```

청크만 떼어 봐도 출처를 알 수 있어야 한다. LLM 에 넘길 때 이 헤더가
근거 표시의 1차 정보가 된다.

### metadatas

| Chroma 키 | 출처 | 필수 | 쓰이는 곳 |
| --- | --- | --- | --- |
| `article_id` | 아래 3번 규칙으로 조합 | ★ | **채점 기준** |
| `title` | `documents.title` | ★ | 법령 단위 필터 |
| `doc_type` | `chunks.source_type` | ★ | 문서 유형 필터 |
| `article_no` | `law_articles.article_number` | ★ | 출처 표시 |
| `article_title` | `law_articles.article_title` | ★ | 출처 표시 |
| `source_url` | `documents.source_url` | ★ | 원문 링크 |
| `status` | `documents.status` | ★ | 현행/폐지 필터 |
| `effective_date` | `law_versions.effective_from` | ★ | 시행일 필터 |
| `expiry_date` | `law_versions.effective_to` | | 한시법 |
| `doc_id` | `chunks.document_id` | | 추적 |
| `chunk_index` | `chunks.chunk_index` | | 순서 복원 |
| `checksum` | `chunks.checksum` | | 추적 |

판례·가이드 청크는 `article_no` / `article_title` / `effective_date` 가
없을 수 있다. **`None` 대신 빈 문자열 `""`** 을 넣는다 (5번 참조).

`status` 는 SQLite 의 `current | historical | repealed` 를 그대로 쓴다.
검색 기본 필터는 `{"status": "current"}` 다.

---

## 3. `article_id` 조합 규칙

평가셋과 코퍼스를 잇는 **유일한 열쇠**다. 다른 건 몰라도 이건 어긋나면 안 된다.

```
data/eval/dev.jsonl     "gold_articles": ["주택임대차보호법-제3조"]
                                              ║  문자열이 정확히
                                              ║  같아야 채점된다
Chroma metadata         "article_id":    "주택임대차보호법-제3조"
```

한 글자만 달라도 검색기가 정답을 가져와도 **전부 오답으로 채점된다.**
검색 성능이 아니라 채점이 깨지는 것이라 수치만 보고는 원인을 알 수 없다.

### 형식

```
{laws.law_name}-{law_articles.article_number}
```

| 규칙 | 예 |
| --- | --- |
| 하이픈 하나로 연결 | `주택임대차보호법-제3조` |
| 조의N 도 그대로 | `주택임대차보호법-제3조의2` |
| 법령명의 공백은 원문 유지 | `주택임대차보호법 시행령-제10조` |
| 항·호는 붙이지 않는다 | `제3조의2` (O) / `제3조의2제2항` (X) |

> `tests/test_knowledge_databases.py` 의 `"article-3-3"` 은 테스트용 더미값이다.
> 법령명이 없으면 다른 법의 같은 조문과 구분되지 않으므로, 실제 적재 시에는
> 위 규칙으로 조합한다. SQLite 의 `law_articles.article_id` (대리키)는 그대로
> 두고, **Chroma metadata 에만 이 논리 ID 를 넣으면 된다.**

### 버전 처리

`law_articles` 는 `law_version_id` 에 종속되므로 같은 제3조라도 개정 버전마다
행이 다르다. 반면 평가 정답 라벨은 **버전 무관**하게 유지한다. 법령이 개정될
때마다 평가셋을 다시 만들 수는 없기 때문이다.

따라서 `article_id` 에 버전을 넣지 않고, 버전 구분은 `effective_date` 와
`status` 로 한다. 기본 검색이 `status = current` 를 걸므로 현행 버전만 잡힌다.

### 항 단위로 쪼갤 때

장문 조문을 항 단위로 나누더라도 **`article_id` 는 조 단위를 유지**한다.
정답 판정 단위가 조문이기 때문이다. 쪼갠 구분은 `chunk_id` 로 표현한다.

```
chunk_id "…#제3조의2#0"  article_id "주택임대차보호법-제3조의2"
chunk_id "…#제3조의2#1"  article_id "주택임대차보호법-제3조의2"
```

이렇게 해두면 청킹 전략을 바꿔도 평가셋을 다시 만들 필요가 없다.

---

## 4. `doc_type` 허용값

```
law | decree | rule | case | interp | guide
```

`schema.sql` 의 CHECK 제약을 이 6종으로 확장 요청한 상태다
(`documents.document_type`, `chunks.source_type` 두 곳).

시행령이 `law` 에 뭉뚱그려지면 "법률만" / "시행령만" 같은 필터를 걸 수 없다.
예를 들어 최우선변제 **금액**은 시행령에만 있고 법률 본문에는 없으므로,
질문 유형에 따라 좁히는 것이 검색 품질에 직접 영향을 준다.

---

## 5. Chroma 타입 제약 — 여기서 제일 많이 터진다

Chroma 는 metadata 값으로 **문자열·정수·실수·불리언만** 받는다.

| 하면 안 되는 것 | 대신 |
| --- | --- |
| `"refs": ["민사집행법-제88조", "국세기본법-제35조"]` | `"refs": "민사집행법-제88조\|국세기본법-제35조"` |
| `"expiry_date": None` | `"expiry_date": ""` |
| `"paragraphs": {...}` | 중첩 객체는 넣지 않는다 |

리스트나 `None` 은 **적재 시점에 예외로 터진다.** SQLite 의 `NULL` 을 그대로
넘기면 이 문제가 생기므로, 조회 결과를 `or ""` 로 감싸 넣는다.
조회 후 복원은 `split("|")`.

---

## 6. 임베딩 모델 — `nlpai-lab/KURE-v1` (1024차원)

```
data/index/chroma_kure_1024/
```

같은 코퍼스·평가셋으로 비교한 결과 한국어 특화 모델이 뚜렷하게 앞섰다.
법령 135청크, 채점 25문항 기준이다.

| 모델 | Hit@1 | Hit@5 | MRR | 질의(초) |
| --- | --- | --- | --- | --- |
| **nlpai-lab/KURE-v1** | **80.0%** | **96.0%** | **0.860** | 0.141 |
| BM25 (용어사전 적용) | 76.0% | 96.0% | 0.847 | 0.000 |
| BAAI/bge-m3 | 76.0% | 84.0% | 0.793 | 0.131 |
| text-embedding-3-small | 52.0% | 80.0% | 0.647 | 0.207 |

Hit@1 차이가 28%p 로 표준오차(3.9%p)를 크게 넘는다. KURE-v1 은 bge-m3 를
한국어로 추가 학습한 모델이고, 순위가 한국어 학습량 순서와 일치한다.
CPU 에서 0.141초/질의로 API 보다 오히려 빠르므로 GPU 가 없어도 된다.

`Qwen3-Embedding-4B` 는 fp16 기준 약 8GB VRAM 이 필요해 6GB 장비에서는 올라가지
않는다. 재현 실행은 `python -m src.evaluation.compare_embeddings --local KURE bge`.

### 적재 시 지킬 것

**임베딩은 직접 계산해 `embeddings=` 로 넘긴다.** 이 값을 생략하면 Chroma 가
기본 모델(`all-MiniLM-L6-v2`, 영어 중심)을 자동으로 쓴다.

**차원은 컬렉션 전체에 하나만 존재한다.** 첫 벡터를 넣는 순간 고정되므로 법령과
판례가 같은 컬렉션에 들어가려면 같은 모델이어야 한다. 모델을 바꾸면 SQLite 원문은
그대로 두고 컬렉션만 다시 만든다. 인덱스 디렉토리 이름에 모델명과 차원을 넣는
이유가 이것이다.

**조회 결과는 거리다.** Chroma 는 `distances` 를 돌려주고 값이 작을수록 가깝다.
검색기는 점수(클수록 유사)를 기대하므로 `score = 1 - distance` 로 뒤집는다.
빠뜨리면 순위가 정확히 반대가 되는데, 증상이 "검색 품질 저하"로만 보여 원인을
찾기 어렵다.

---

## 7. 적재 예시

```python
collection.upsert(
    ids=["law-주택임대차보호법-20260102#제3조의2#0"],
    documents=[
        "[주택임대차보호법 제3조의2(보증금의 회수)]\n"
        "② 제3조제1항ㆍ제2항 또는 제3항의 대항요건과 임대차계약증서상의 "
        "확정일자를 갖춘 임차인은 ..."
    ],
    metadatas=[{
        "article_id": "주택임대차보호법-제3조의2",
        "title": "주택임대차보호법",
        "doc_type": "law",
        "article_no": "제3조의2",
        "article_title": "보증금의 회수",
        "source_url": "https://www.law.go.kr/법령/주택임대차보호법/제3조의2",
        "status": "current",
        "effective_date": "2026-01-02",
        "expiry_date": "",
        "doc_id": "law-주택임대차보호법-20260102",
        "chunk_index": 0,
        "checksum": "9f2c1a...",
    }],
)
```

---

## 8. 적재 전 검사

문서를 읽는 것보다 **돌려보는 게 확실하다.**

```bash
python -m src.ingestion.validate_chunks <청크 jsonl> --eval-set data/eval/dev.jsonl
```

통과하면 종료 코드 0, 문제가 있으면 1 과 함께 무엇이 왜 틀렸는지 출력한다.
CI 에 그대로 걸 수 있다.

검사 항목:

- 필수 필드 존재 여부
- Chroma 타입 제약 (리스트 · `None` 금지)
- `article_id` 형식
- `chunk_id` 중복
- `doc_type` · `status` 허용값, 날짜 형식
- 같은 `article_id` 끼리 메타데이터가 엇갈리지 않는지
- `--eval-set` 을 주면 **평가셋 정답 조문이 코퍼스에 실재하는지**

마지막 항목이 특히 중요하다. 정답 조문이 없으면 그 문항은 검색기가 아무리
좋아도 영구히 오답이고, 이건 검색 성능이 아니라 데이터 결함이다.

---

## 9. 바꿔야 할 일이 생기면

`article_id` 형식이나 metadata 키 이름을 바꿔야 하면 **먼저 알려달라.**
평가셋 정답 라벨 전체와 검색 필터가 이 형식에 묶여 있어서, 말 없이 바뀌면
검색 지표가 통째로 0 에 가깝게 떨어지고 원인 찾는 데 시간이 든다.

바꾸기로 하면 이 문서와 `validate_chunks.py`, `dev.jsonl` 의 `gold_articles`
를 같이 고친다.
