# LENS

> **Lease Evidence Navigation System**
>
> 임대차 관련 근거를 찾아 이해하기 쉽게 연결하는 주택임대차 상담 서비스
> SKN33 3차 단위 프로젝트 · Team 4

LENS는 전세와 월세 계약을 준비하거나 거주 중인 사용자가 주택임대차 질문을 하면,
관련 법령·판례·공공기관 안내를 찾아 근거가 있는 답변을 제공하는 RAG 챗봇입니다.
등기사항증명서와 임대차계약서를 올리면 OCR로 읽은 문서 내용도 현재 브라우저 세션의
질문 근거로 사용할 수 있습니다.

이 서비스는 특정 집이나 계약이 안전하다고 확정하지 않으며, 법률 전문가의 판단을
대신하지 않습니다. 근거가 없거나 생성된 답변이 검증을 통과하지 못하면 답변을
보류합니다.

## 팀 소개

| 이름 | 역할 | 담당 파트 | 주요 작업 |
| --- | --- | --- | --- |
| 김혜진 | 팀장 | LLM·RAG 생성 Part 1 | 생성 체인 구성, 모델 실행·통합 테스트, 발표 |
| 김정재 | 팀원 | LLM·RAG 생성 Part 2 | 프롬프트 설계, 모델 개선·파인튜닝 검토 |
| 송지섭 | 팀원 | Retriever Part 1 | 관련 법령·기관 안내 검색 연결과 성능 평가 |
| 윤지환 | 팀원 | Retriever Part 2 | 관련 판례 검색 연결과 성능 평가 |
| 신진호 | 팀원 | 문서 분석·서비스 최적화 | 계약서·등기 OCR, 문서 점검, 챗봇 성능 최적화 |

## 1. 프로젝트 한눈에 보기

| 구분 | 내용 |
| --- | --- |
| 주요 사용자 | 전세·월세 계약을 준비하거나 거주 중인 임차인 |
| 해결하려는 문제 | 법령·판례·기관 안내가 흩어져 있고 일반 사용자가 계약 문구와 법률 근거를 연결하기 어려움 |
| 주요 기능 | 임대차 상담, 공식 근거 검색, 계약서·등기 OCR, 위험 신호·작성 항목 확인 |
| 검색 방식 | BM25 키워드 검색 + KURE-v1 의미 검색 + RRF 순위 결합 |
| 답변 모델 | Qwen3-8B Q4 · Ollama |
| 화면 | Streamlit |
| 저장소 | SQLite 원문·관계 정보 + Chroma 검색 인덱스 |
| 답변 원칙 | 검색 근거 사용, 출처 표시, 검증 실패 시 답변 보류, 안전 여부 확정 금지 |

### 개발 범위

프로젝트는 기획·설계, 공식 데이터 수집과 정제, 검색 엔진, AI 답변 생성, 사용자 화면,
통합 테스트, 실행 환경 정리, 산출물 문서화까지 한 흐름으로 구현했습니다.

| 영역 | 구현 내용 |
| --- | --- |
| 기획·설계 | 임차인 사용 시나리오, RAG 아키텍처, 협업 구조 |
| 데이터 구축 | 법령·판례·HUG·국세청 자료 수집, 정제, 청크 생성, Chroma 적재 |
| 검색 엔진 | BM25·KURE-v1·RRF, 자료 유형별 검색, Dev·Holdout 평가 |
| AI 답변 | LangChain·LangGraph, Qwen 생성, 출처·숫자·조건 검증 |
| 서비스 화면 | 채팅, PDF·이미지 업로드, 등기·계약서 확인 결과와 출처 표시 |
| 테스트·평가 | 검색 성능, 답변 상태, OCR, 보안, 화면 회귀 테스트 |

## 2. 사용자가 할 수 있는 일

### 주택임대차 상담

- 일반인이 쓰는 표현으로 전세·월세 질문하기
- 관련 법령, 판례, HUG·국세청 안내를 구분해서 확인하기
- 이전 대화가 필요한 짧은 후속 질문 이어서 묻기
- 답변에 실제 사용된 공식 출처와 링크 확인하기

### 등기사항증명서 확인

- PDF 내장 글자를 우선 추출하고 스캔 페이지는 Tesseract OCR로 보완
- 주민등록번호·전화번호·계좌번호 등 민감정보 마스킹
- 갑구·을구의 주요 위험 신호와 해당 문구·페이지 표시
- 확인이 더 필요한 사항과 공식 참고자료 제공

### 임대차계약서 확인

- PDF·JPG·JPEG·PNG 파일에서 주요 작성 항목 확인
- 실제 공란과 OCR로 읽기 어려운 항목을 구분
- 기존 특약 문구와 상황별 협의 항목 확인
- 앞서 확인한 등기 위험 신호와 관련 특약 연결

### 업로드 문서에 이어서 질문

- 같은 브라우저 세션에 여러 계약서·등기 문서 추가
- OCR 내용을 바탕으로 등기사항증명서·임대차계약서를 구분하고, 불명확하면 사용자에게 확인 요청
- “이 계약서 보증금은 얼마야?”, “이 특약은 무슨 뜻이야?”처럼 문서 내용 질문
- 문서에 적힌 사실과 법령·판례의 법적 설명을 답변에서 구분

`이 문서`, `첨부한 등본` 같은 표현도 최근 첨부 문맥과 연결합니다. 여러 문서가 있거나
가리키는 대상이 불명확할 때는 “업로드한 계약서의 보증금은 얼마야?”, “이 등기 문서의
을구를 설명해줘”처럼 문서 종류와 확인할 항목을 직접 적는 것이 정확합니다.

### 실행 상태와 화면 사용성

- 화면과 입력창을 먼저 표시한 뒤 KURE-v1 검색 모델을 백그라운드에서 한 번만 준비
- 첨부 직후 질문과 파일명을 먼저 표시하고 파일별 OCR 진행·완료·실패 상태 제공
- 첫 화면의 서비스 소개 카드를 접거나 펼쳐 대화 영역 확보
- 답변·보류·거절·OCR 실패 뒤에도 입력창을 다시 활성화해 후속 질문 지원
- 답변 상태와 응답 시간은 메시지마다 한 번만 표시

초기 검색 모델 준비 방식과 로컬 캐시·중복 적재 방지 검증은
[`docs/eval-patch039-startup-readiness.md`](docs/eval-patch039-startup-readiness.md)를 참고하세요.

## 3. 프로그램 동작 구조

```text
사용자 질문 또는 업로드 문서
            │
            ├─ 문서 추출: PDF 글자 추출 → 필요 시 OCR → 문서 종류 판별·세션 검색
            │
            └─ 질문 처리: 후속 질문 정리 → 비밀정보 마스킹 → 공격·범위 검사
                                      │
                                      ▼
                              Retriever 검색
                    ┌──────────┼──────────┐
                    ▼          ▼          ▼
                  법령       판례      기관 안내
                    └──────────┼──────────┘
                               + 세션 문서 근거
                                      │
                                      ▼
                         Qwen3-8B 근거 기반 답변 생성
                                      │
                                      ▼
                    출력 정리 → 출처·숫자·조건 검증 → 의미 검증
                                      │
                                      ▼
                         ANSWER / ABSTAIN / REFUSE
```

실행 순서와 조건 분기는 LangGraph가 관리합니다. 각 단계의 검색·생성·검증 규칙은
`src/retrieval/`, `src/generation/`, `src/security/`로 나누어 두었습니다.

### 3.1 Retriever가 근거를 찾는 방법

Retriever는 질문을 받아 법령·판례·기관 안내를 한 줄로 섞지 않고 각각 검색합니다.
자료마다 역할과 평가 기준이 다르고, 한 종류가 다른 종류의 검색 자리를 밀어내지 않게
하기 위해서입니다.

```text
사용자 질문
   ↓
질문 유형 확인
   ├─ 일반 법률 질문 → 법령·관련 기관 안내 우선 검색
   ├─ 판례 직접 요청 → 판례 검색
   └─ 법령·안내 근거 부족 → 판례 추가 검색
   ↓
자료 유형별 Hybrid 검색
   ├─ BM25: 조문번호·법률용어처럼 글자가 직접 겹치는 문서 탐색
   ├─ KURE-v1: 표현은 달라도 의미가 가까운 문서 탐색
   └─ RRF: 두 검색 결과의 순위를 하나로 결합
   ↓
RetrievalResult(laws, cases, guides)
```

`RetrievalResult` 안의 각 `Evidence`에는 다음 정보가 들어갑니다.

| 필드 | 화면과 생성 단계에서의 용도 |
| --- | --- |
| `rank` | 자료 유형 안에서의 검색 순위 |
| `citation` | 법령명·조문 또는 판례 사건번호 등 출처명 |
| `text` | 답변 생성에 사용하는 근거 본문 |
| `score` | 검색 점수 |
| `source_url` | 화면에서 제공하는 공식 출처 링크 |

공개 검색 함수 `RetrievalService.search()`는 필요하면 법령 5건·판례 5건·기관 안내
0~2건을 각각 반환할 수 있습니다. 실제 Qwen 답변에는 작은 모델이 핵심 근거에 집중할 수
있도록 법령 최대 3건·판례 최대 2건·기관 안내 최대 2건만 전달합니다. 일반 법률 질문에
판례를 항상 넣지 않고, 판례를 요청했거나 1차 근거가 부족할 때만 추가합니다.

업로드 문서는 공용 법률 검색 자료와 섞지 않습니다. OCR 페이지를 현재 세션에서만 BM25로
검색하며, 법령 검색에 사용하는 용어 확장도 적용하지 않습니다. 보증금·특약처럼 문서에
적힌 사실만 묻는 질문은 공식 검색을 열지 않아 무관한 법조문이 답변에 붙지 않게 합니다.

### 3.2 LLM이 답변을 만드는 방법

```text
선별된 공식 근거 + 관련 OCR 문서 + 사용자 질문
                     ↓
            LangChain Prompt 구성
                     ↓
        Qwen3-8B가 답변 본문 1회 생성
                     ↓
     reasoning·임의 URL·잘린 문장 정리
                     ↓
       근거의 시점·조건을 제한적으로 교정
                     ↓
       출처·인용·숫자·기간·조문 검사
                     ↓
     위험한 답변만 보조 Qwen으로 의미 검사
                     ↓
          ANSWER / ABSTAIN / REFUSE
```

모델은 답변 본문만 생성합니다. 답변 상태, 면책문구, 출처 링크는 코드가 관리합니다.
검색되지 않은 법령·판례를 인용하거나 근거의 금액·기간·시점·임대인/임차인 역할을
바꾸면 검증에서 차단합니다. 검증을 통과하지 못한 원문을 대체 답변으로 보여주지 않습니다.

| 최종 상태 | 의미 |
| --- | --- |
| `answered` | 검색 근거를 사용한 답변이 검증을 통과함 |
| `abstained` | 근거 없음, 생성 실패, 빈 응답 또는 검증 실패로 답변을 보류함 |
| `refused` | 서비스 범위 밖 요청이나 프롬프트 공격을 생성 전에 차단함 |

#### Qwen 실행 설정

| 설정 | 현재 값 |
| --- | --- |
| 모델 | `qwen3:8b-q4_K_M` |
| API | Ollama native `/api/chat` |
| Temperature | `0.0` |
| 일반 답변 길이 상한 | `256 tokens` |
| 문서 내용만 답하는 경우 | 최소 `384 tokens` |
| 보조 판정 길이 상한 | `160 tokens` |
| Context | `4096` |
| Thinking | 비활성화 |

기본 실행 위치는 Local Ollama입니다. `.env`에 RunPod 주소를 설정하면 원격 Ollama를 먼저
확인하고, 연결 실패·모델 없음·생성 실패 시 같은 모델이 설치된 Local Ollama로 한 번
전환합니다.

## 4. 데이터와 저장 구조

### 최종 제출·평가에 사용한 데이터

| 자료 | 현재 범위 | 용도 |
| --- | ---: | --- |
| 법령 | 133청크 | 주택임대차 관련 조문 검색 |
| 판례 | 26건 | 판례 검색과 최종 평가 |
| 기관 안내 | 2문서·6청크 | HUG 반환보증·국세청 미납국세 열람 절차 안내 |
| 업로드 문서 | 현재 세션 한정 | 계약서·등기 내용 질문 |

판례 최종 결과는 검토하고 재현할 수 있는 26건을 기준으로 합니다. 과거 문서에 나온
207건은 검토 전 수집 기준선이며 현재 적재·검증 완료 건수가 아닙니다. 추가 판례는
출처·사건정보·판결요지와 주택임대차 관련성을 확인한 뒤 넣을 수 있고, 검색 자료가 바뀌면
Dev·Holdout 평가를 다시 수행해야 합니다.

### 저장소 역할

| 저장소 | 기본 경로 | 역할 |
| --- | --- | --- |
| 원천·가공 파일 | `data/raw/`, `data/parsed/`, `data/chunks/` | 수집 원문, 표준 레코드, 검색 청크 |
| SQLite | `data/database/knowledge.sqlite3` | 법령·판례·안내 원문과 관계 정보 |
| Chroma | `data/index/chroma_kurev1_1024/` | KURE-v1 벡터와 검색 메타데이터 |
| 평가셋 | `data/eval/` | Dev·Holdout 질문과 정답 근거 |

### 주요 데이터 관계

README에서는 평가자가 전체 구조를 이해하는 데 필요한 관계만 요약하고, 세부 컬럼과
제약조건은 코드와 상세 문서에서 관리합니다.

| 관계 | 의미 |
| --- | --- |
| `documents` → 법령·판례·안내 | 모든 공식 자료의 출처·URL·수집일·원문 경로를 공통 관리 |
| `laws` → `law_versions` → `law_articles` | 법령의 개정 버전과 조문을 분리해 보존 |
| `cases` ↔ `law_articles` | `case_law_citations`로 판례가 적용·인용한 조문을 연결 |
| `risk_rules` ↔ 공식 근거 | `rule_evidence`로 문서 위험 규칙의 법령·판례·안내 근거를 연결 |
| SQLite `chunks.chunk_id` ↔ Chroma 문서 ID | 원문 관계 정보와 검색 벡터를 같은 청크 ID로 추적 |

SQLite·Chroma·생성 청크는 Git에 올리지 않으며 각 실행 환경에서 다시 만듭니다. 같은
자료를 다시 적재해도 중복 행을 계속 추가하지 않고 해당 자료 유형의 현재 입력 상태로
맞춥니다. 법령만 다시 색인할 때 판례·안내를 지우지 않도록 삭제 범위도 자료 유형별로
제한합니다.

상세 필드와 원문 추적 규칙은 [`docs/chunk-schema.md`](docs/chunk-schema.md), 판례 검증
절차는 [`docs/case-data-handoff.md`](docs/case-data-handoff.md)를 참고합니다.

## 5. 최종 평가 결과

검색 결과에 필요한 정답 근거가 운영 반환 범위 안에 들어왔는지를 측정했습니다.
`Dev`는 개발 과정에서 반복 확인한 질문, `Holdout`은 설정을 고르는 데 사용하지 않고
마지막 회귀 확인에 사용한 질문입니다. `Hit@3`은 정답 근거가 상위 3건 안에 하나 이상
있는지, `Hit@2`는 판례 상위 2건 안에 정답이 있는지를 뜻합니다.

| 평가 대상 | 문항 수 | 운영 기준 | 결과 |
| --- | ---: | ---: | ---: |
| 법령 Dev | 24 | Hit@3 | 24/24 (100.0%) |
| 법령 기존 Holdout | 18 | Hit@3 | 17/18 (94.4%) |
| 판례 Dev | 13 | Hit@2 | 12/13 (92.3%) |
| 판례 대체 Holdout | 8 | Hit@2 | 7/8 (87.5%) |

### 결과를 읽을 때의 주의사항

- 법령과 판례는 정답 단위와 운영 반환 건수가 달라 하나의 “전체 정확도”로 합치지 않음
- 판례 수치는 현재 26건 검색 자료에만 적용됨
- 판례 대체 Holdout은 8문항으로 표본이 작아 일반화 성능을 확정하지 않음
- 기관 안내는 관련 질문에서 0~2건을 반환하고 일반 질문에서 빠지는 기능을 확인했지만,
  독립 평가셋이 충분하지 않아 별도 정확도를 주장하지 않음
- 공개된 Holdout 실패 문항을 보고 검색 규칙을 추가하지 않음

평가 절차와 한계는 [`docs/eval-audit.md`](docs/eval-audit.md), 최초 Holdout 절차는
[`docs/eval-holdout.md`](docs/eval-holdout.md), 검색 재현 방법은
[`docs/retrieval-handoff.md`](docs/retrieval-handoff.md)를 참고합니다.

## 6. 설치부터 실행까지

처음 실행할 때는 아래 순서를 따릅니다.

```text
Python 환경 준비
→ 패키지 설치
→ .env 생성
→ Ollama 모델 준비
→ SQLite·Chroma 기본 저장소 초기화
→ 법령·판례·기관 안내 청크 생성
→ 법령·판례·기관 안내 순서로 Chroma 색인
→ Streamlit 실행
→ 테스트
```

### 6.1 요구 환경

- Python 3.11
- Ollama와 `qwen3:8b-q4_K_M`
- 스캔 PDF·이미지 OCR 사용 시 Tesseract와 한국어 언어 데이터
- 기관 안내 원문을 처음 수집할 때 인터넷 연결

텍스트가 포함된 PDF는 Tesseract 없이도 처리됩니다. Windows·macOS의 Tesseract 설치와
제약은 [`docs/registry-check.md`](docs/registry-check.md)를 참고합니다.

### 6.2 Python 패키지와 환경변수

```bash
pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell에서는 다음 명령을 사용합니다.

```powershell
Copy-Item .env.example .env
```

기본값은 Local Ollama이므로 별도의 API 키 없이 챗봇을 실행할 수 있습니다. 국가법령정보
공동활용 API를 이용한 판례 재수집에는 `.env`의 `LAW_OPEN_API_OC`가 필요하고,
LangSmith 추적은 선택 기능입니다.

필요한 경우 `.env`에서 다음 경로와 선택 기능을 설정할 수 있습니다.

| 환경변수 | 사용하는 경우 |
| --- | --- |
| `JEONSEON_DATABASE_PATH` | 기본 SQLite 저장 위치를 바꿀 때 |
| `JEONSEON_CHROMA_PATH` | 기본 Chroma 인덱스 위치를 바꿀 때 |
| `TESSERACT_CMD` | Windows에서 Tesseract를 자동으로 찾지 못할 때 |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | 개인정보가 없는 개발 실행을 추적할 때 |

`JEONSEON_*` 이름은 기존 실행 환경과의 호환성을 위해 유지한 환경변수입니다. 서비스
표시명은 LENS이며, 환경변수 이름만 바꾸면 기존 `.env`와 배포 설정이 깨질 수 있어 이번
문서 개편에서는 변경하지 않았습니다.

### 6.3 Ollama 준비

```bash
ollama pull qwen3:8b-q4_K_M
ollama serve
```

RunPod를 사용할 때만 `.env`의 주소를 바꿉니다.

```dotenv
JEONSEON_LLM_BASE_URL=https://YOUR_POD_ID-11434.proxy.runpod.net/v1
JEONSEON_LLM_MODEL=qwen3:8b-q4_K_M
```

### 6.4 초기 데이터 생성

DB와 Chroma 인덱스는 Git에 포함되지 않으므로 저장소를 받은 뒤 각자 한 번 만들어야
합니다. 현재 제출·시연·평가에 사용한 26건 판례를 기준으로 한 명령입니다.

```bash
# 0. SQLite와 빈 Chroma 컬렉션 초기화
python scripts/init_databases.py

# 1. 법령 133청크
python -m src.ingestion.fetch_law_mock --records data/parsed/law_records.jsonl
python -m src.ingestion.load_laws \
  --records data/parsed/law_records.jsonl \
  --export data/chunks/chunks.jsonl

# 2. 판례 26건
python scripts/load_case_only_demo_corpus.py

# 3. 공식 안내 2문서·6청크 (인터넷 연결 필요)
python -m src.ingestion.fetch_guides \
  --records data/parsed/guide_records.jsonl
python -m src.ingestion.load_guides \
  --records data/parsed/guide_records.jsonl \
  --export data/chunks/guides.jsonl
```

각 적재 명령도 SQLite 스키마를 자동으로 준비하므로 초기화 명령을 생략해도 적재할 수
있습니다. 다만 처음 실행할 때 `init_databases.py`를 먼저 실행하면 DB·Chroma 경로와
기본 컬렉션이 정상적으로 만들어지는지 생성 전에 확인할 수 있습니다.

`fetch_law_mock`은 평가용 `data/sample/chunks_expanded.jsonl`을 법령 청크로 다시 쓰므로,
실행 뒤 이 샘플 파일을 커밋하지 않습니다. 운영에 사용하는 `data/chunks/chunks.jsonl`은
`load_laws`가 별도로 만듭니다.

### 6.5 Chroma 인덱스 생성

반드시 법령 → 판례 → 안내 순서로 실행합니다.

```bash
python -m src.retrieval.index \
  --chunks data/chunks/chunks.jsonl \
  --path data/index/chroma_kurev1_1024

python -m src.retrieval.index \
  --chunks data/chunks/cases.jsonl \
  --path data/index/chroma_kurev1_1024

python -m src.retrieval.index \
  --chunks data/chunks/guides.jsonl \
  --path data/index/chroma_kurev1_1024
```

각 명령은 입력에 포함된 자료 유형만 갱신합니다. 예를 들어 판례를 다시 색인해도 기존
법령·기관 안내 벡터는 유지됩니다. 컬렉션 전체를 정리하는 `--prune-all`은 다른 자료까지
삭제할 수 있으므로 전체 코퍼스를 교체할 때만 사용합니다.

### 6.6 서비스 실행

```bash
streamlit run app/streamlit_app.py
```

브라우저에서 `http://localhost:8501`로 접속합니다.

## 7. 테스트

### 전체 회귀 테스트

```bash
pytest -q
```

| 테스트 영역 | 확인 내용 |
| --- | --- |
| 데이터·DB | 법령·판례·안내 적재, 청크 규격, SQLite·Chroma 동기화 |
| Retriever | BM25·KURE·RRF, 자료 유형 분리, 상가 라우팅, 조건부 안내 |
| Generation | 검색→Prompt→Qwen 연결, 세 가지 답변 상태, RunPod→Local 전환 |
| Validation | 출처·직접 인용·금액·기간·시점·조건·주체 검사 |
| 문서 처리 | PDF 검증, OCR, 세션 검색, 개인정보 마스킹 |
| 화면 | Streamlit 채팅·업로드·출처·오류 처리 |

실제 OCR 통합 테스트는 Tesseract 설치 여부, 실제 LLM 테스트는 Ollama 실행 여부,
LangSmith 연결 테스트는 관련 환경변수에 따라 달라집니다. Windows에서 긴 PDF 테스트명의
환경변수 한도 문제가 발생할 수 있으며 자세한 검증 기록은
[`docs/windows-verification.md`](docs/windows-verification.md)를 참고합니다.

### 검색 평가 재현

```bash
python -m src.evaluation.compare_hybrid --sweep
python -m src.evaluation.compare_law_top3
```

판례·최종 평가의 실행 방법과 원자료 위치는 [`docs/retrieval-handoff.md`](docs/retrieval-handoff.md)와
[`docs/case-data-handoff.md`](docs/case-data-handoff.md)에 정리되어 있습니다.

## 8. 개인정보 보호와 서비스 한계

### 개인정보 처리

- 업로드한 PDF·이미지 원본을 공용 DB에 저장하지 않음
- OCR 전체 문서를 공용 SQLite·Chroma에 적재하지 않음
- OCR 청크는 현재 Streamlit 세션 메모리에서만 사용
- 문서 근거가 포함된 질문은 LangSmith 추적 비활성화
- 화면과 다운로드 결과에서 주민등록번호 등 민감정보 마스킹
- RunPod 주소를 설정한 경우 답변에 선택된 OCR 근거가 원격 Ollama로 전달될 수 있으므로
  개인정보가 포함된 실제 문서는 Local Ollama 사용 권장

### 서비스 한계

- 특정 집이나 계약이 안전한지 확정하지 않음
- 변호사·법무사 등 전문가의 법률 자문을 대신하지 않음
- 판례는 개별 사건의 판단이므로 법령과 같은 일반 규칙으로 단정하지 않음
- OCR 결과가 흐리거나 페이지 구조가 복잡하면 일부 문구를 읽지 못할 수 있음
- 검색 자료에 정답 근거가 없으면 LLM이 답을 만들지 않고 보류할 수 있음
- 현재 판례 평가 결과는 검토된 26건 범위에 한정됨
- 여러 첨부 문서 중 질문 대상이 불명확하면 문서 종류와 확인할 항목을 직접 밝혀야 함

## 9. 프로젝트 구조

```text
app/
└─ streamlit_app.py          사용자 화면과 세션 관리

src/
├─ ingestion/                법령·판례·안내 수집·정제·청크 생성
├─ database/                 SQLite·Chroma 초기화와 접근
├─ retrieval/                BM25·KURE·RRF와 검색 서비스
├─ generation/               LangChain·LangGraph·Qwen·답변 검증
├─ security/                 비밀정보·Prompt Injection 검사
├─ document_check/           등기 추출·OCR·위험 신호·세션 문서 검색
├─ contract_check/           계약서 작성 항목·특약 확인
└─ evaluation/               검색·생성 평가

scripts/                     데이터 적재·평가·문서 생성 명령
tests/                       단위·통합·회귀 테스트
docs/                        상세 설계·실행·평가 문서
data/                        샘플·평가셋과 로컬 생성 데이터
```

## 10. 상세 문서

| 문서 | 내용 |
| --- | --- |
| [`docs/retrieval-handoff.md`](docs/retrieval-handoff.md) | 검색 구조, 데이터 준비, 실행·평가 방법 |
| [`docs/case-data-handoff.md`](docs/case-data-handoff.md) | 판례 수집·검증·적재 절차 |
| [`docs/ocr-session-rag-connection.md`](docs/ocr-session-rag-connection.md) | 업로드 문서 RAG와 개인정보 처리 경계 |
| [`docs/chunk-schema.md`](docs/chunk-schema.md) | 검색 청크와 메타데이터 규격 |
| [`docs/eval-audit.md`](docs/eval-audit.md) | 평가 절차, 수치 해석과 한계 |
| [`docs/eval-holdout.md`](docs/eval-holdout.md) | Holdout 봉인·측정 절차 |
| [`docs/registry-check.md`](docs/registry-check.md) | 등기사항증명서 분석과 OCR 실행법 |
| [`docs/contract-check.md`](docs/contract-check.md) | 임대차계약서 점검 기준과 한계 |
| [`docs/windows-verification.md`](docs/windows-verification.md) | Windows 설치·통합 테스트 기록 |
| [`docs/planning/project-plan.md`](docs/planning/project-plan.md) | 프로젝트 기획·아키텍처·역할별 실행 계획 |

패치별 작업 상태와 후속 과제는 [`LIST.md`](LIST.md)에서 관리합니다.

### 기획서 PDF 재생성

기획서 원문이나 PDF 생성 스크립트를 수정한 경우 다음 명령으로 제출용 PDF를 다시
만듭니다.

```bash
python scripts/build_project_plan_pdf.py
```

- 원문: `docs/planning/project-plan.md`
- 생성 파일: `docs/planning/jeonseon-project-plan.pdf`
- 생성 코드: `scripts/build_project_plan_pdf.py`

PDF 파일명은 기존 제출 경로와 회귀 테스트 호환성을 위해 유지하며, PDF 내부의 서비스
표시명은 LENS를 사용합니다.

## 11. 향후 개선

- 별도로 수집된 약 200건의 판례 후보는 출처·사건정보·판결요지·주택임대차 관련성을
  검토한 뒤 검색 자료 확대 여부를 결정하고, 채택 시 Dev·Holdout 전체 재평가
- 결과를 보지 않고 봉인한 법령 Holdout-v2 구축
- 기관 안내 문서·평가 질문 확대와 독립 정량 평가
- TOP3·TOP2 리랭커의 정확도·응답시간 비교
- 임대차 관련 민법 조항은 기존 법령 순위를 해치지 않는지 독립 평가한 뒤 조건부 검색
  도입 여부 판단
- 상가 질문의 정답 기반 검색 순위 평가
- `fetch_law_mock` 실행 시 비법령 샘플 청크 보존

완료된 기반과 실제 남은 조건은 [`LIST.md`](LIST.md)의 「검색 파트 후속 과제」와
[`docs/retrieval-handoff.md`](docs/retrieval-handoff.md) 6절에 구분해 기록했습니다.
