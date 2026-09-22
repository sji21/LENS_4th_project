# LENS

> **Lease Evidence Navigation System**
>
> 임대차 관련 근거를 찾아 이해하기 쉽게 연결하는 주택임대차 상담 서비스
> SKN33 4차 단위 프로젝트 · Team 4

LENS는 전세와 월세 계약을 준비하거나 거주 중인 사용자가 주택임대차 질문을 하면,
관련 법령·판례·공공기관 안내를 찾아 근거가 있는 답변을 제공하는 RAG 챗봇입니다.
등기사항증명서와 임대차계약서를 올리면 OCR로 읽은 문서 내용도 현재 브라우저 세션의
질문 근거로 사용할 수 있습니다.

이 서비스는 특정 집이나 계약이 안전하다고 확정하지 않으며, 법률 전문가의 판단을
대신하지 않습니다. 근거가 없거나 생성된 답변이 검증을 통과하지 못하면 답변을
보류합니다.

이 저장소는 3차 단위 프로젝트에서 이관한 코드베이스로 시작합니다. 현재 웹은
Django와 HTML·CSS·JavaScript로 동작합니다. 아래 평가 결과는 별도 표시가 없으면
3차에서 이어받은 기준선이며, 웹 전환에 따른 성능 향상을 의미하지 않습니다.
이관 기준 커밋과 3차 기록 링크는 [`LIST.md`](LIST.md)에 있습니다.

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
| 답변 모델 | Qwen3.8-27B · Ollama · 추론 비활성화 |
| 웹 | Django 5.2 LTS + HTML·CSS·JavaScript |
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
                         Qwen3.8-27B 근거 기반 답변 생성
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

공개 검색 함수 `RetrievalService.search()`는 일반 법령 5건·판례 5건·민법 후보 최대 3건·기관 안내
0~2건을 별도로 반환할 수 있습니다. 민법은 `civil_laws` 필드에 담겨 일반 법령 자리를 차감하지 않습니다.
실제 Qwen 경로는 기존 일반 법령 최대 3건·판례 최대 2건·기관 안내 최대 2건 설정을 유지하고,
민법 후보 최대 3건을 별도 구간으로 전달합니다. 일반 법률 질문에
판례를 항상 넣지 않고, 판례를 요청했거나 1차 근거가 부족할 때만 추가합니다.
민법 후보는 기존 규칙 선택을 보존하고 검색으로 보충하며, 불필요한 질문에서 자동으로 0개를
선택하는 조건은 아직 없습니다. [반환 계약과 검증 결과](docs/patch021-civil-result-channel.md)를 확인하세요.

업로드 문서는 공용 법률 검색 자료와 섞지 않습니다. OCR 페이지를 현재 세션에서만 BM25로
검색하며, 법령 검색에 사용하는 용어 확장도 적용하지 않습니다. 보증금·특약처럼 문서에
적힌 사실만 묻는 질문은 공식 검색을 열지 않아 무관한 법조문이 답변에 붙지 않게 합니다.

### 3.2 LLM이 답변을 만드는 방법

```text
선별된 공식 근거 + 관련 OCR 문서 + 사용자 질문
                     ↓
            LangChain Prompt 구성
                     ↓
       근거별 답변 계획 생성(JSON, 실패 시 생략)
                     ↓
        Qwen3.8-27B가 답변 본문 1회 생성
                     ↓
     reasoning·임의 URL·잘린 문장 정리
                     ↓
       근거의 시점·조건을 제한적으로 교정
                     ↓
       출처·인용·숫자·기간·조문 검사
                     ↓
     위험한 답변만 보조 Qwen으로 의미 검사
                     ↓
   첫 검증 실패 1회 보정·재검증
   (결정론 보정 뒤 의미 실패 시에만 추가 1회)
                     ↓
          ANSWER / ABSTAIN / REFUSE
```

모델은 답변 본문만 생성합니다. 답변 상태, 면책문구, 출처 링크는 코드가 관리합니다.
검색되지 않은 법령·판례를 인용하거나 근거의 금액·기간·시점·임대인/임차인 역할을
바꾸면 검증에서 차단합니다. 검증을 통과하지 못한 원문을 대체 답변으로 보여주지 않습니다.
답변 계획과 의미 검증처럼 JSON을 반환하는 보조 호출은 짧은 분류 호출과 별도의 출력
상한을 사용합니다. 운영 Graph 기준 생성·검증 호출은 일반 semantic 최초 실패에서 최대
5회, 결정론 보정 뒤 semantic 실패까지 이어질 때 최대 7회입니다. 검색 예산과 최종 답변
생성 설정은 이 보조 호출 설정과 무관하게 기존 값을 유지합니다.

로컬 DEV100 전수 실행 `dev100-v2-answer-plan-full-20260922-090412`의 참고 지연은
100문항 평균 38.532초, 중앙값 31.959초, 최대 113.028초였습니다. 당시 상태는
answered 65, abstained 29, refused 6, generation error 0이며, 공개 문항으로 조정한
회귀 측정이라 독립 성능 점수로 사용하지 않습니다.

| 최종 상태 | 의미 |
| --- | --- |
| `answered` | 검색 근거를 사용한 답변이 검증을 통과함 |
| `abstained` | 근거 없음, 생성 실패, 빈 응답 또는 검증 실패로 답변을 보류함 |
| `refused` | 서비스 범위 밖 요청이나 프롬프트 공격을 생성 전에 차단함 |

#### Qwen 실행 설정

| 설정 | 현재 값 |
| --- | --- |
| 모델 | `qwen3.8:27b` |
| API | Ollama native `/api/chat` |
| Temperature | `0.0` |
| 일반 답변 길이 상한 | `512 tokens` |
| 문서 내용만 답하는 경우 | 최소 `384 tokens` |
| 보조 판정 길이 상한 | `160 tokens` |
| Context | `8192` |
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

기본 설치의 SQLite·Chroma·생성 청크는 각 실행 환경에서 다시 만듭니다. 후속 판례
배포는 예외로 `data/case_corpus`의 **data_dev_v2 8,377건 DB·청크·인덱스**를 Git LFS로
전달하고, 설치 시 검증한 판례 프로필을 활성화합니다. 원격 업로드·pull 확인 전에는
배포 완료가 아닙니다. [판례 Git 배포 문서](docs/case-git-release.md)를 참고하세요. 같은
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
[`docs/retrieval-handoff.md`](docs/retrieval-handoff.md)를 참고합니다. PATCH-043 판례 전용 후보의 로컬 실행 코드와 필요한 SQLite·Chroma·모델 자료는
[`docs/patch043-local-retriever.md`](docs/patch043-local-retriever.md)에 구분해 기록했습니다.

## 6. 설치부터 실행까지

### 6.0 검색 데이터 준비 — A와 B 중 하나만

검색에는 법령·민법·판례·안내 데이터와 색인이 필요합니다. **팀원은 A(팀 배포본 ZIP)를 기본으로 사용합니다.** B는 ZIP을 쓰지 않고 각 PC에서 원천부터 직접 구축할 때만 사용합니다. 두 방식은 서로 대체하므로 함께 실행하지 않습니다.

| | A. 팀 배포본 ZIP (기본 권장) | B. 로컬 구축 `setup_data.py` |
| --- | --- | --- |
| 받는 것 | ZIP 하나 (검색 release + KURE 모델) | 저장소 원천 자료 + Git LFS 판례 |
| 시간 | 설치·검증 수 분 | 원천 파싱·임베딩 포함 |
| 결과 | 모든 PC가 같은 release ID | PC별로 구축 |
| MySQL 접속 | 필요 없음 | 필요 없음 |
| `.env` | `LENS_MYSQL_RELEASE=data/mysql-search/active.json`, `LENS_MYSQL_MODEL_DIR=data/models/kure-mysql` | 두 값을 비움 |
| 확인한 환경 | Windows x64, Apple Silicon Mac, Linux x64(RunPod) (r2 실측) | Windows, macOS, RunPod |

`LENS_CASE_RETRIEVAL_PROFILE`은 두 방식 모두 비워 둡니다. A의 값과 B용 경로·프로필을 동시에 지정하면 앱은 다른 데이터로 넘어가지 않고 오류로 중단합니다.

#### A. 팀 배포본 ZIP 설치 (기본 권장)

현재 배포본은 **`LENS-MySQL-search-patch060-r2-20260921.zip`**(release ID `5b1a63a7…`)입니다. 이 release를 만든 커밋의 검색 코드에서만 검증이 통과하므로 PATCH-060이 반영된 `main`을 사용합니다(병합 전에는 `fix/patch-060-cross-platform-search-release` 브랜치의 배포본과 일치하는 코드). 구형 `shared-v3`, `patch059-20260921`, 1차 `patch060-20260921`과 혼용하지 않습니다.

0. **기존 clone이면 필수:** 검색 코드를 LF로 다시 받습니다. 커밋하지 않은 수정이 없을 때 Windows는 `Remove-Item src\retrieval\*.py; git checkout -- src/retrieval`, macOS·Linux는 `rm src/retrieval/*.py && git checkout -- src/retrieval`를 실행합니다. 건너뛰면 `core.autocrlf=true`인 Windows에서 CRLF 파일이 남아 `verify`가 "정책·코드·필수 항목이 다릅니다"로 실패합니다. 새로 clone했다면 생략합니다.
1. `.env`가 없으면 `.env.example`을 복사합니다. 검색 관련 기본값이 A로 설정되어 있습니다. 기존 `.env`는 덮어쓰지 않고 위 표의 A 경로를 설정하며 `LENS_CASE_RETRIEVAL_PROFILE`은 비웁니다.
2. ZIP을 저장소 루트에 풀어 `data` 폴더가 합쳐지게 합니다.
3. Python 3.11 가상환경에서 `requirements` → 의존성 설치 → `verify` → `model` → `activate`를 실행합니다. PyTorch는 PyPI에서 설치합니다.
4. `verify`가 `verified: true`이고 `activate`까지 완료되면, 6.2의 Django 키·환경변수 설정을 확인한 뒤 6.3 Ollama 준비와 6.6 서비스 실행으로 이동합니다. `activate` 뒤에는 앱을 재시작합니다.

OS별 명령, 기존 clone의 줄바꿈 재체크아웃, 오류 대처는 [MySQL 검색 설치 안내](docs/mysql-search.md)와 ZIP 안 `README-KR.md`에 있습니다. A에서는 B의 `setup_data.py` DB 구축을 실행하지 않습니다. A의 가상환경은 `python3.11 -m venv .venv`(Windows: `py -3.11 -m venv .venv`)로 만들고, 패키지는 반드시 배포본의 제약 파일을 적용해 설치합니다. macOS Intel은 지원하지 않습니다.

#### B. 로컬 구축 `setup_data.py` (ZIP을 쓰지 않을 때)

**DB ZIP은 필요 없습니다.** 저장소의 승인 원천 자료를 파싱해 SQLite·일반 법령 인덱스·민법 전용 인덱스를 직접 구축합니다. 서버 관리자나 개발자가 실행하며, 웹 이용자는 사이트에서 질문만 입력합니다. `.env`의 `LENS_MYSQL_RELEASE`와 `LENS_MYSQL_MODEL_DIR`을 비운 뒤 실행하세요.

Python 3.11을 설치하고 저장소 루트에서 실행하세요.

| 환경 | 명령 |
| --- | --- |
| Windows | `py -3.11 setup_data.py` |
| macOS / Linux 로컬 | `python3.11 setup_data.py` |
| RunPod | `python3.11 setup_data.py --venv-dir /opt/lens-venv` |

**가상환경·필요 모듈 설치·KURE 준비 → DB 체크 → 원천 파싱·저장·인덱스 구축 → 확인** 순서로 진행합니다. 같은 원천·코드·모델이면 재구축을 생략하고, 승인 입력이 바뀌면 새 청크·본문 변경분만 임베딩합니다. 기존 DB는 백업하고 Django 계정·대화 DB는 유지합니다.

이미 준비된 환경에서는 `python manage.py prepare_retrieval`로 같은 작업을 실행합니다. 다른 방식으로 만든 기존 DB를 전환하려면 `--rebuild`가 필요합니다. 환경만 준비하려면 `setup_data.py --prepare-only`, 다운로드 없는 환경 점검은 `--check`를 사용합니다.

PATCH-060부터 검색 코드를 모든 OS에서 LF 줄바꿈으로 받습니다. 그 전에 Windows에서 구축한 PC는 `setup_data.py`를 다시 실행하면 코드 기록이 달라 로컬 DB를 한 번 자동 재구축합니다(기본 코퍼스 재구축 여부·처리 건수는 실행 로그로 확인, 별도 판례8,377건 배포는 제외). 이미 구축된 DB는 재실행하지 않아도 그대로 동작합니다.

PATCH-059의 구축 대상은 **일반 법령185·민법31조문(합계216), 안내·공식 서식3문서**입니다. 판례 설치를 완료하면 기존8,377건에 공식 보완2건을 합쳐 검색합니다. 코드 갱신 후 위 구축 명령을 다시 실행해야 새 자료가 반영됩니다. 최초 평가와 개선 후 회귀 결과·남은 근거 손실은 [PATCH-059 기록](docs/patch058-retrieval-coverage.md)을 참고하세요.

[서버 설치·원천 자료·Django·RunPod 실행 안내](docs/server-data-setup.md)를 먼저 확인하세요. RunPod에서는 프로젝트·DB와 `HF_HOME=/workspace/huggingface` 모델 캐시를 영구 볼륨에 두고, 가상환경은 쓰기 가능한 내부 디스크(`/opt/lens-venv` 예시)에 설치합니다. 이후 `source /opt/lens-venv/bin/activate`를 사용하고, setup 재실행에도 같은 `--venv-dir`를 지정합니다. 컨테이너 교체로 내부 디스크가 사라지면 패키지는 재설치해야 합니다. 기본 Windows·Mac `.venv` 동작은 유지합니다. **Mac·RunPod 설치 확인을 완료했습니다. 이전 RunPod에서는 DB 구축·적용·기본 검색을 확인했고, 2026-09-15 새 Pod에서는 README 가상환경의 CUDA 연산·KURE GPU 임베딩을 확인했습니다. 현재 확인한 Pod에는 추가 CUDA 수정이 필요하지 않습니다. 과거 경고의 원인은 미확정이며 새 Pod의 DB 구축·검색·LLM 평가는 미실시입니다.** [GPU 실측 환경과 범위](docs/patch040-runpod-gpu-verification.md)를 참고하세요.

입력은 검토한 원천 스냅샷이며 최신 법령 자동 수집·채택 기능은 아닙니다. 설치 확인은 무결성·중복·기본 검색 검사이고 전체235문항 평가나 LLM 평가는 별도입니다. **기존 ZIP 방식은 [평가 DB 재현·복원 전용](docs/local-retrieval-data.md)으로 유지합니다.**

A 또는 B로 검색 데이터를 준비했으면 환경변수·Ollama 설정 후 **6.6 서비스 실행**으로 이동합니다. 아래 **6.4~6.5는 이전 수동 재생성 경로**이므로 A나 B 이후 다시 실행하지 않습니다.

수동 재생성과 앱 전체 실행 순서는 다음과 같습니다.

```text
Python 환경 준비
→ 패키지 설치
→ .env 생성
→ Ollama 모델 준비
→ SQLite·Chroma 기본 저장소 초기화
→ 법령·판례·기관 안내 청크 생성
→ 법령·판례·기관 안내 순서로 Chroma 색인
→ Django 웹 DB 마이그레이션·서버 실행
→ 테스트
```

### 6.1 요구 환경

- Python 3.11
- Ollama와 `qwen3.8:27b`
- 스캔 PDF·이미지 OCR 사용 시 Tesseract와 한국어 언어 데이터
- 기관 안내 원문을 처음 수집할 때 인터넷 연결

텍스트가 포함된 PDF는 Tesseract 없이도 처리됩니다. Windows·macOS의 Tesseract 설치와
제약은 [`docs/registry-check.md`](docs/registry-check.md)를 참고합니다.

### 6.2 Python 패키지와 환경변수

A 설치를 마쳤다면 아래 패키지 설치 명령은 건너뛰고 환경변수 설정부터 확인합니다. A의 패키지를 다시 설치할 때도 배포본의 제약 파일을 적용해야 합니다. B의 간편 실행기가 이미 패키지를 설치한 경우에도 중복 설치하지 않습니다. 아래 명령은 B를 수동으로 준비할 때 사용합니다.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
source .venv/bin/activate
```

Windows PowerShell에서는 다음 명령을 사용합니다.

```powershell
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pip check
.\.venv\Scripts\Activate.ps1
```

간편 실행으로 준비한 경우에도 이후 `python` 명령 전에 해당 OS의 가상환경을 활성화합니다. `.env`가 아직 없을 때만 macOS에서 `cp .env.example .env`, Windows에서 `Copy-Item .env.example .env`로 만듭니다.

기본값은 Local Ollama이므로 별도의 API 키 없이 챗봇을 실행할 수 있습니다. 국가법령정보
공동활용 API를 이용한 판례 재수집에는 `.env`의 `LAW_OPEN_API_OC`가 필요하고,
LangSmith 추적은 선택 기능입니다.

이미 `.env`가 있으면 덮어쓰지 말고 `.env.example`의 누락 항목만 추가합니다.
**팀원은 브랜치를 받거나 `git pull`한 뒤마다 `.env.example`과 자신의 로컬 `.env`를
비교해 새 항목을 각자 반영해야 합니다.** 개인 키와 PC별 경로는 유지하고, 예시 파일의
비밀값을 그대로 복사하거나 `.env`를 Git에 올리지 않습니다. 변경 후에는 앱을 재시작합니다.
다음 명령으로 Django 키를 생성해 로컬 `.env`의 `DJANGO_SECRET_KEY`에 넣습니다.
키와 `.env`는 Git에 올리지 않습니다. 로컬 HTTP 실행은 `DJANGO_DEBUG=true`를 사용합니다.

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

필요한 경우 `.env`에서 다음 경로와 선택 기능을 설정할 수 있습니다.

| 환경변수 | 사용하는 경우 |
| --- | --- |
| `LENS_MYSQL_RELEASE`, `LENS_MYSQL_MODEL_DIR` | 6.0 A 팀 배포본 사용 시 `.env.example` 기본값 유지, B 로컬 구축 시 비움 |
| `JEONSEON_DATABASE_PATH` | 기본 SQLite 저장 위치를 바꿀 때 (B에서만) |
| `JEONSEON_CHROMA_PATH` | 기본 Chroma 인덱스 위치를 바꿀 때 (B에서만) |
| `TESSERACT_CMD` | Windows에서 Tesseract를 자동으로 찾지 못할 때 |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | 개인정보가 없는 개발 실행을 추적할 때 |

`JEONSEON_*` 이름은 기존 실행 환경과의 호환성을 위해 유지한 환경변수입니다. 서비스
표시명은 LENS이며, 환경변수 이름만 바꾸면 기존 `.env`와 배포 설정이 깨질 수 있어 이번
문서 개편에서는 변경하지 않았습니다.

### 6.3 Ollama 준비

```bash
ollama pull qwen3.8:27b
ollama serve
```

RunPod를 사용할 때만 `.env`의 주소를 바꿉니다.

```dotenv
JEONSEON_LLM_BASE_URL=https://YOUR_POD_ID-11434.proxy.runpod.net/v1
JEONSEON_LLM_MODEL=qwen3.8:27b
```

### 6.4 수동 초기 데이터 생성 (기존 기준선 재생성용)

아래는 기존 3차 자료의 재생성 절차이며, 현재 확대216조문 묶음을 완성하는 명령이 아닙니다.
팀원 설치에는 6.0의 A(팀 배포본 ZIP) 또는 B(로컬 구축)를 사용하세요. 현재 DB가 있는 폴더에서
아래 명령을 실행하면 데이터·인덱스가 바뀔 수 있으므로 별도 작업 폴더에서 재생성합니다.

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

### 6.5 수동 Chroma 인덱스 생성

반드시 법령 → 판례 → 안내 순서로 실행합니다.

```bash
# 민법(民法) 조문은 PATCH-006 절차로 별도 인덱스에만 색인합니다. chunks.jsonl
# 에는 BM25·누락 감지를 위해 민법 청크가 함께 들어 있을 수 있으므로, 기본
# 인덱스를 만들기 전에 민법을 제외한 사본을 먼저 만듭니다. (아래 두 단계는
# scripts/reload_base_index_civil_safe.py 로도 실행할 수 있습니다.)
python - <<'PY'
import json

with open("data/chunks/chunks.jsonl", encoding="utf-8") as src, \
     open("data/chunks/chunks.base-only.jsonl", "w", encoding="utf-8") as dst:
    for line in src:
        chunk = json.loads(line)
        if chunk.get("metadata", {}).get("title") == "민법":
            continue
        dst.write(line)
PY

python -m src.retrieval.index \
  --chunks data/chunks/chunks.base-only.jsonl \
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

### 별도 용도: 검증 DB 묶음 복원

기존 평가와 동일한 DB를 재현할 때만 `setup_data.py --validation-bundle` 또는 `.\manage-data.ps1`을 사용합니다. [검증 DB 복원 안내](docs/local-retrieval-data.md)를 참고하세요. 일반 설치는 위의 원천 기반 서버 구축을 사용합니다.

### 6.6 서비스 실행

민법은 전용 인덱스를 사용하며 일반 법령과 별도의 `civil_laws` 필드로 최대3건을 반환합니다.
[PATCH-006](docs/patch006-civil-routing.md)은 최초7조문 도입 기록이고,
[PATCH-021](docs/patch021-civil-result-channel.md)은 반환 채널 분리 기록입니다.
프로필 없는 PATCH-024 기준 데이터는 민법10조문을 사용합니다.
**PATCH-027의 확대 검색은 일반 법령178·민법26조문과 검색 프로필을 함께 설치**해야 합니다.
[백업·적재·검증·복구 절차](docs/patch027-product-rollout.md)를 따르세요. 프로필 없는 기존 데이터는
기존 방식으로 동작하며 Git 병합만으로 로컬 DB·인덱스가 바뀌지는 않습니다.
공개 개발용 검증 결과이며 독립 평가나 LLM 답변 정확도를 의미하지 않습니다.

```bash
python manage.py migrate
python manage.py runserver 127.0.0.1:8000 --noreload
```

브라우저에서 `http://127.0.0.1:8000`으로 접속합니다. `--noreload`는 모델의 반복 초기화를
피하기 위한 옵션이며 Python 코드를 수정하면 서버를 재시작합니다. 웹 계정·세션 DB는
`data/database/web.sqlite3`이며 RAG 지식 DB와 분리됩니다. 최초 HTML을 연 다음 검색 모델을
백그라운드로 준비합니다. 실패하면 화면에서 재시도할 수 있습니다.

회원가입 담당자의 연결 지점, API, 개인정보 보관·정리, 실행 제약은
[`docs/django-web.md`](docs/django-web.md)를 참고하세요. 회원가입 화면은 아직 구현하지 않았습니다.

### 대화형 챗봇 실험 경로

`CHAT_CONVERSATION_ENABLED=true`로 대화 관리 계층을 활성화할 수 있습니다. 기본값은
`false`입니다. 초기 전체 수용평가 실패 뒤 법률 적용 이전의 대화 계층을 보정하고 [35턴 공개 회귀 검사](docs/chatting-recovery.md)를 완료했습니다. 전체 법률 답변의 품질·지연 승인은 별도이며, 구조·실행·API 연결 지점·기존 경로 복귀 방법은
[`docs/chatting-handoff.md`](docs/chatting-handoff.md)에 정리했습니다. 실제 모델 평가와
고정 응답을 사용하는 UI 검증은 구분하며 검증 통과 전 main 반영을 권장하지 않습니다.

## 7. 테스트

### 전체 회귀 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Windows에서는 UTF-8 모드로 실행합니다. 일부 평가 코드가 인코딩을 지정하지 않고 한글 JSON을 읽고 써서 기본 cp949에서는 실패합니다.

```powershell
python -X utf8 -m pytest -q
```

| 테스트 영역 | 확인 내용 |
| --- | --- |
| 데이터·DB | 법령·판례·안내 적재, 청크 규격, SQLite·Chroma 동기화 |
| Retriever | BM25·KURE·RRF, 자료 유형 분리, 상가 라우팅, 조건부 안내 |
| Generation | 검색→Prompt→Qwen 연결, 세 가지 답변 상태, RunPod→Local 전환 |
| Validation | 출처·직접 인용·금액·기간·시점·조건·주체 검사 |
| 문서 처리 | PDF 검증, OCR, 세션 검색, 개인정보 마스킹 |
| 웹 | Django 채팅·업로드·세션 격리·CSRF·중복 요청·오류 처리, 기존 Streamlit 회귀 |

실제 OCR 통합 테스트는 Tesseract 설치 여부, 실제 LLM 테스트는 Ollama 실행 여부,
LangSmith 연결 테스트는 관련 환경변수에 따라 달라집니다. Windows에서 긴 PDF 테스트명의
환경변수 한도 문제가 발생할 수 있습니다. Windows 사용자 이름에 한글이 있으면 Chroma가 기본 임시 경로를 거부하므로 테스트 전에 `$env:TEMP='C:\Temp'; $env:TMP='C:\Temp'`처럼 영문 경로를 지정합니다. 테스트는 로컬 `.env`의 `LENS_MYSQL_RELEASE` 설정과 무관하게 통과해야 합니다. 자세한 검증 기록은
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
- 마스킹된 문서 청크·대화는 웹 전용 SQLite에 세션별로 보관하며 마지막 변경 후 1시간이 지나면 접근 만료
- 만료 자료는 홈페이지 접속 시 또는 `python manage.py purge_chats`로 삭제하며, 정기 실행 전까지 디스크에 남을 수 있음
- 문서 근거가 포함된 질문은 LangSmith 추적 비활성화
- 화면·문서 문맥에서 주민등록번호 등 민감정보 마스킹 (모든 개인정보 제거를 보장하지 않음)
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
manage.py                    Django 실행·마이그레이션 명령
config/                      Django 설정·루트 URL·WSGI·ASGI
accounts/                    확장 가능한 사용자 모델·관리자 등록
chat/                        채팅 API·세션 DB·기존 RAG 연결
templates/                   Django HTML 템플릿
static/chat/                 CSS·JavaScript
app/streamlit_app.py          3차 화면 보존 (기본 실행 경로 아님)

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
- 추가 독립 평가가 필요하면 개선에 사용하지 않은 새 질문을 평가 전에 봉인
- 기관 안내 문서·평가 질문 확대와 독립 정량 평가
- TOP3·TOP2 리랭커의 정확도·응답시간 비교
- 확대 법령·민법 검색의 남은 근거 손실 개선 및 독립 평가
- 상가 질문의 정답 기반 검색 순위 평가
- `fetch_law_mock` 실행 시 비법령 샘플 청크 보존

완료된 기반과 실제 남은 조건은 [`LIST.md`](LIST.md)의 「검색 파트 후속 과제」와
[`docs/retrieval-handoff.md`](docs/retrieval-handoff.md) 6절에 구분해 기록했습니다.

## 12. MySQL 지식 데이터 관리 (담당자용)

팀원의 검색 설치는 [6.0 A. 팀 배포본 ZIP 설치](#a-팀-배포본-zip-설치-기본-권장)를 따릅니다. 이 절은 공용 MySQL 적재·내보내기·배포본 생성을 맡은 담당자용 기록입니다.

PATCH-057 — 2026-09-19 공용 MySQL의 기본·민법·판례 스냅샷 전체 해시와
유형별 원문 조회를 확인했습니다. 판례는 8,377건입니다.
[실제 DB 검증 및 PR 범위](docs/mysql-live-verification.md)를 참고하세요.

법령·판례·기관 안내 원문, 청크, 판례 이력을 공용 MySQL에 코퍼스 버전별로
저장하고 기존 JSONL 없이 검색 청크를 내보내는 도구를 추가했습니다.
`requirements-mysql.txt`와 별도 MySQL 접속 설정을 사용하며, 회원·대화 DB와는
독립적입니다. [MySQL 실행 안내](docs/mysql-data.md)에 초기화·이전·검증·내보내기·
출처 추적 명령을 정리했습니다.

MySQL 청크로 BM25·Chroma 검색 배포본을 생성·검증·활성화하고 기존 앱 검색기에
연결할 수 있습니다. [팀 검색 실행 안내](docs/mysql-search.md)에 명령을 정리했습니다.
89개 질문의 이전 전후 검색 결과가 일치했고 원본 DB 없는 설치 폴더에서도 검색과
모의 LLM 입력 전달을 확인했습니다. [원문 갱신 안내](docs/mysql-ingest.md)의 문서
추가·교체·삭제·재청킹과 벡터 재사용, 배포 갱신·되돌리기도 검증했습니다.
실제 LLM 및 다른 팀원 PC 검증은
[실행 계획](docs/planning/mysql-retrieval-execution-plan.md)의 남은 항목입니다.


### PATCH-059·060 완료 및 배포본 기록

- **PATCH-059:** 최종 리트리버 종합 평가·법령/판례/서식 보완·검색 전달 개선을 완료했고 PR #42로 병합했습니다. 기존 근거 손실과 과거 평가 테스트 실패는 알려진 한계로 유지합니다. 검색 평가를 LLM 답변 품질 평가로 해석하지 않습니다.
- **PATCH-060:** OS별 코드 줄바꿈 통일, CPU 간 벡터 오차 검증, 본문·메타데이터 타입·임베딩 출처의 정확한 검증, 새 환경의 설치 제약 파일 생성 처리를 완료했습니다. r2 배포본은 Windows x64와 Apple Silicon Mac(arm64, Python 3.11.15)에서 같은 ZIP으로 설치·`verify`·검색·관련 테스트 31개를 모두 통과했습니다. Linux는 미실측입니다.

현재 배포본은 **`LENS-MySQL-search-patch060-r2-20260921.zip`**, release ID `5b1a63a7b004f63ad2dae018d53b9e4e0cdd120a61d623ec3270174c53622703`입니다. 구형 `shared-v3`, `patch059-20260921`, 1차 `patch060-20260921` 배포본과 혼용하지 않습니다. ZIP에는 검색 release와 KURE 모델이 포함되며 팀원 설치 순서는 6.0 A에 있습니다. 검색 코드가 바뀌면 담당자가 배포본을 다시 생성해 새 ZIP으로 배포합니다.
