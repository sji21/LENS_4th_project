# LENS

> **Lease Evidence Navigation System**
>
> 임대차 관련 근거를 찾아 이해하기 쉽게 연결하는 주택임대차 상담 서비스
> SKN33 4차 단위 프로젝트 · Team 4

LENS는 전세·월세 계약을 준비하거나 거주 중인 임차인이 자신의 상황을 질문하면 법령·판례·HUG·국세청 등
공식 자료에서 근거를 찾아 출처와 함께 설명하는 RAG 기반 상담 챗봇입니다. 등기사항증명서와 임대차계약서를
올리면 OCR로 읽은 내용을 같은 상담의 문서 근거로 이어서 사용할 수 있습니다.

LENS는 특정 집이나 계약이 안전하다고 확정하지 않으며 법률 전문가의 판단을 대신하지 않습니다. 근거가
없거나 생성된 답변이 검증을 통과하지 못하면 답변을 보류합니다.

[3차 프로젝트](https://github.com/sji21/3rd_project_team4)의 Streamlit 화면을 Django로 전환하고 회원·계약별 상담,
민법 전용 검색과 판례 코퍼스 확대, 검증 가능한 검색 배포 방식을 더했습니다. 답변 모델도 Qwen3-8B에서
Qwen3.8-27B로 변경했습니다.

**바로 사용하기:** [LENS 서비스](https://d3kro62a2qvg04.cloudfront.net/)에 접속하세요.

## 팀 소개

| 이름 | 담당 업무 |
| --- | --- |
| 김혜진 (팀장) | LLM 생성 및 체인 구성, 모델 실행·통합 테스트, 인용·검증 로직 점검 |
| 김정재 | 추가 기능 기획 및 구현, LLM 엔지니어링 |
| 송지섭 | 법령 데이터·리트리버 담당. 법령 데이터 수집·구조화 및 DB 구축, 하이브리드 검색 개선·성능 평가, 법령 인용 검증 보완 |
| 윤지환 | 판례 DB 개발 및 데이터 확장, API 기반 대량 판례 수집·정제, 벡터 DB·인덱스 구축, 검색 결과·성능 확인 |
| 신진호 | Streamlit → Django 웹 전환, 사용자 의도 판단, 대화형 챗봇(대화 연결·요약·후속질문) 개선 |

## 1. 주요 기능

| 기능 | 내용 |
| --- | --- |
| 임대차 상담 | 일상적인 질문과 후속 질문에 법령·판례·기관 안내의 출처를 붙여 답변<br>“쉽게 다시 설명”, 생성 중지 지원 |
| 등기사항증명서 확인 | 갑구·을구 위험 신호를 문구·페이지와 함께 표시<br>답변 생성이 보류돼도 확인된 문서 내용은 별도 안내 |
| 임대차계약서 확인 | 주소·당사자·보증금·기간·특약 점검<br>촬영본 표·날짜 보완 판독, 불확실한 값은 원본 확인 요청 |
| 문서 기반 질문 | 한 상담에 최대 5개 문서 첨부<br>문서 기재 사실과 법령·판례 설명을 구분 |
| 회원 기능 | 회원가입·로그인, 계약별 채팅방·체크리스트·일정 후보·PDF 리포트<br>게스트 질문은 로그인·가입 시 새 채팅방으로 이전 |
| 대화형 상담 (선택) | 상담 상태를 이어받아 정정·주제 전환·확인 질문 처리<br>`CHAT_CONVERSATION_ENABLED=true`로 활성화, 기본값은 꺼짐 |

문서는 PDF·JPG·JPEG·PNG를 파일당 20MB까지 받습니다. 여러 문서가 있거나 “이 문서”가 가리키는 대상이
모호하면 “업로드한 계약서의 보증금은 얼마야?”, “이 등기 문서의 을구를 설명해줘”처럼 문서 종류와
확인할 항목을 적는 것이 정확합니다.

## 2. 동작 구조

| 구분 | 사용 기술 |
| --- | --- |
| 웹 | Django 5.2 LTS, HTML·CSS·JavaScript |
| 검색 | BM25 + KURE-v1 의미 검색 + RRF, Chroma |
| 생성·검증 | LangChain·LangGraph, Qwen3.8-27B (`qwen3.8:27b`, Ollama) |
| 문서 처리 | pdfplumber·pypdfium2 글자 추출, Tesseract OCR |
| 저장 | 검색 배포본은 JSONL·Chroma, 원천 구축은 SQLite·Chroma, 웹은 SQLite. 담당자는 별도 MySQL에서 원문 관리 |

```text
사용자 질문 · 업로드 문서
  │
  ├─ 질문: 후속 질문 정리 → 비밀값·민감정보 마스킹 → 프롬프트 공격·서비스 범위 검사
  └─ 문서: PDF 글자 추출 → 필요 시 OCR → 문서 종류 판별·마스킹 → 현재 상담의 문서 근거
  │
  ▼
공식 근거 검색: 일반 법령 · 민법 · 판례 · 기관 안내 (BM25 + KURE-v1 + RRF)
  │   문서 내용만 묻는 질문은 공식 검색을 생략
  ▼
근거별 답변 계획(JSON, 실패하면 생략) → Qwen3.8-27B 답변 생성 (공식 근거 + 관련 문서 근거)
  │
  ▼
출처·인용·금액·기간·조건·당사자 역할 검증 → 단일 법령의 단순 설명이 아니면 보조 의미 검증
  │   검증에 실패하면 같은 근거 안에서 1회 보정 후 재검증
  │   (결정론 보정 뒤 의미 검증에서 실패한 경우에만 1회 추가)
  ▼
LLM 생성 결과: answered(답변) · abstained(보류) · refused(거절)
```

실행 순서와 조건 분기는 LangGraph가 관리하고, 규칙은 `src/security/`, `src/retrieval/`,
`src/generation/`에 나뉘어 있습니다. 문서 표시를 별도로 안내하는 `document_review` 경로는
[첨부 문서 상담 기록](docs/patch063-document-consultation.md)을 참고하세요.

### 2.1 공식 근거 검색

자료 유형마다 역할과 평가 기준이 달라 채널을 나눠 검색합니다. 각 채널은 조문 번호·법률 용어처럼 글자가
겹치는 문서를 찾는 BM25와 표현이 달라도 의미가 가까운 문서를 찾는 KURE-v1 의미 검색을 RRF로 결합합니다.
답변 경로에서 채널별로 전달하는 근거는 다음과 같습니다.

| 채널 | 건수 | 전달 조건 |
| --- | ---: | --- |
| 일반 법령·시행령 | 3 | 모든 법률 질문 |
| 민법 | 3 | 일반 법령과 별도. 감지한 조문을 먼저 두고 검색 후보로 채움 |
| 기관 안내·공식 서식 | 2 | 해당 절차·서식 질문에만 전달 (0~2건) |
| 판례 | 2 | 판례 요청·구체적 법적 효과 질문, 또는 다른 근거가 부족할 때 |

업로드 문서는 공용 검색 자료와 섞지 않습니다. 현재 상담의 OCR 페이지만 BM25로 검색하고, 보증금·특약처럼
문서에 적힌 사실만 묻는 질문은 공식 검색을 열지 않아 무관한 조문이 답변에 붙지 않게 합니다.

### 2.2 답변 생성과 검증

생성 전에 보조 호출이 검색된 출처 목록 안에서 “답할 항목–직접 출처”와 근거가 부족한 항목을 나눈 답변
계획을 만들고, 계획이 형식에 맞지 않으면 계획 없이 생성합니다. 모델은 답변 본문만 생성하며 답변 상태·면책
문구·출처 링크는 코드가 관리합니다. 검색되지 않은 법령·판례를 인용하거나 근거의 금액·기간·시점·임대인/임차인
역할을 바꾸면 검증에서 차단합니다. 검증에 실패하면 원래 근거만으로 보정한 초안을 같은 검증에 다시 통과시키며,
끝내 통과하지 못한 원문을 대체 답변으로 보여 주지 않습니다. 한 질문의 생성·검증 호출은 보통 최대 5회,
결정론 보정 뒤 의미 검증까지 실패하면 최대 7회입니다.

| 최종 상태 | 의미 |
| --- | --- |
| `answered` | 검색 근거를 사용한 답변이 검증을 통과함 |
| `abstained` | 근거 없음, 생성 실패, 빈 응답 또는 검증 실패로 답변을 보류함 |
| `refused` | 서비스 범위 밖 요청이나 프롬프트 공격을 생성 전에 차단함 |

| Qwen 설정 | 현재 값 |
| --- | --- |
| 모델·API | `qwen3.8:27b`, Ollama native `/api/chat` |
| Temperature | `0.0` |
| 답변 길이 상한 | 512 tokens (문서 내용만 묻는 질문은 최소 384) |
| 보조 호출 길이 상한 | 공격·범위 판정 160 tokens, 답변 계획·의미 검증(JSON) 400 tokens |
| Context | 8192 |
| Thinking | 비활성화 |

팀 기준 생성 모델은 27B이며, 보통 RunPod GPU의 Ollama로 실행합니다. `.env`에
원격 주소를 설정하면 원격 Ollama를 먼저 확인하고, 연결 실패·모델 없음·생성 실패 시 같은 모델이 설치된 로컬
Ollama로 한 번 전환합니다. 노트북에서 화면 흐름만 확인할 때는 `JEONSEON_LLM_MODEL`을 작은 모델로 바꿀 수
있지만, 그 결과는 답변 품질·지연 판단의 기준으로 쓰지 않습니다.

## 3. 데이터와 저장 위치

다음은 현재 검색 배포본의 자료 범위입니다. EC2는 검증한 MySQL 스냅샷에서 내보낸 자료로
서버용 인덱스를 만들었으며, 로컬에서 승인 원천으로 재구축할 수도 있습니다.

| 자료 | 현재 구성 | 용도 |
| --- | --- | --- |
| 일반 법령·시행령 | 185개 조문 | 주택·상가 임대차보호법과 시행령, 민간·공공임대, 확정일자, 민사집행·세금 징수 등 |
| 민법 | 31개 조문 (별도 채널·인덱스) | 계약·채무·임대차 일반 원칙 |
| 판례 | 8,377건 release + 공식 보완 2건 | 사실관계 해석이 필요한 질문의 판례 근거 |
| 기관 안내·공식 서식 | 3개 문서·10청크 | HUG 전세보증금반환보증, 국세청 미납국세 열람, 공공주택 금융정보 제공 동의서 |
| 업로드 문서 | 현재 상담에 한정 | 등기사항증명서·임대차계약서의 기재 사실 |

검색 자료는 검토한 원천 스냅샷이며 최신 법령·판례를 자동으로 수집·채택하지 않습니다. 기본 코퍼스에는 판례
28건(3차 시드 26건 + 보완 2건)이 들어 있지만, 판례 검색은 8,377건 release에 보완 2건만 더해 사용합니다.

| 구분 | 위치 | 내용 |
| --- | --- | --- |
| 검색 배포본 | `data/mysql-search/`, `data/models/kure-mysql/` | 검증한 JSONL·Chroma·KURE 모델. 환경별 `active.json`이 활성 release를 가리킴 |
| 로컬 원천 구축 결과 | `data/database/`, `data/chunks/`, `data/index/` | 원문·관계 SQLite(`knowledge.sqlite3`, `civil.sqlite3`), 청크 JSONL, 일반·민법 Chroma |
| 판례 release | `data/case_corpus/` | Git LFS로 받는 판례 SQLite·JSONL·Chroma와 매니페스트 |
| 승인 원천 | `data/sources/` 등 | 로컬 구축에 쓰는 법령 원문·안내·보완 자료와 해시 |
| 웹 DB | `data/database/web.sqlite3` | 회원·계약 채팅방·대화·체크리스트·일정·리포트 |
| 업로드 원본 | `data/private/` | 암호화한 업로드 원본 (`LENS_PRIVATE_UPLOAD_ROOT`로 변경) |
| 평가 자료 | `data/eval/` | 평가 질문·필수 근거·실측 기록 |

SQLite는 `documents`에 출처·URL·수집일을 두고 법령(`laws` → `law_versions` → `law_articles`), 판례(`cases`와
인용 조문 `case_law_citations`), 안내(`guides`)를 연결합니다. 검색 청크는 SQLite `chunks.chunk_id`와 같은 ID로
Chroma에 저장해 검색 결과에서 원문까지 추적합니다. 세부 규격은 [`docs/chunk-schema.md`](docs/chunk-schema.md)에
있습니다.

## 4. 리트리버·LLM 평가 결과

### 4.1 리트리버 평가

3차 법령 DEV는 생활어 질문 27개에서 범위 밖·정답 부재 문항을 제외해 최종 24개를 채점했습니다.
별도 법령 Holdout 18개와 판례 DEV 13개·대체 Holdout 8개도 확인했습니다. **관련 근거 하나**가
상위 3건(판례는 2건)에 있으면 성공인 Hit@k 기준이었습니다
([3차 평가 안내](https://github.com/sji21/3rd_project_team4/blob/main/docs/eval-holdout.md)).

| 3차 최종 검색 평가 | DEV | Holdout |
| --- | ---: | ---: |
| 법령 Hit@3 | 24/24 | 17/18 |
| 판례 Hit@2 (26건 코퍼스) | 12/13 | 7/8 |

4차에는 기존 문항을 회귀 검사로 유지하면서 [DEV100](data/eval/dev100/README.md)의 100문항을
질문 단독·제공 문맥 포함으로 나눠 200입력을 만들었습니다. 정답에서 필수·조건부·대체·보조 근거를
구분하고, 채점 대상은 각 입력 방식에서 75개씩 고정했습니다. 새 [HO30](data/eval/holdout-v2-ho30-20260918/README.md)은
첫 검색 전에 질문·필수 근거를 고정했으며, **필수 근거 전부**가 반환돼야 성공입니다. 첫 측정 후
개선에 사용했으므로 최종 수치는 독립 평가가 아닌 회귀 결과입니다.

| 4차 필수 근거 완전 확보 | 개선 전 | 최종 |
| --- | ---: | ---: |
| HO30 · 일반 법령 최대 3건 | 14/30 | **20/30** |
| HO30 · 일반 법령 최대 5건 | 17/30 | **24/30** |
| 전체 채점 가능 266입력 · 최대 3건 | 170/266 | **182/266** |
| 전체 채점 가능 266입력 · 최대 5건 | 195/266 | **204/266** |

민법은 두 설정 모두 별도 최대 3건이며, 운영 답변에는 일반 법령 최대 3건을 전달합니다. 법령·민법
원문과 검색 규칙을 보완했지만, 기존 문항 일부의 근거 손실과 판례 순위 문제는 남았습니다. 3차의
Hit@k와 4차의 **필수 근거 전부 확보**는 기준이 달라 점수를 직접 비교할 수 없습니다. 세부 문항·실행
이력·남은 누락은 [리트리버 평가 기록](docs/patch058-retrieval-coverage.md)에 있습니다.

### 4.2 LLM 답변: DEV100·HO30 공개 회귀 평가

팀에서 확인한 [최종 발표 자료](https://www.canva.com/design/DAHV4o2jayY/Hw_3HivSzKq1lvmN_y83tQ/edit)의
Qwen3.8-27B 평가 결과를 기준으로 정리했습니다.

| 평가 | 지표 | 결과 |
| --- | --- | --- |
| DEV100 | 답변률 | 55% → **66%** |
| DEV100 | 보류율 | 39% → **28%** |
| HO30 공개 회귀 평가 | 답변률 | **86.7%** |
| HO30 공개 회귀 평가 | 완전 정답률 | **76.7%** |

답변률과 정답률은 별도 지표입니다. HO30은 검색·생성 개선에 이미 사용했으므로 독립 홀드아웃이
아닌 공개 회귀 평가입니다. 위 수치는 팀의 최종 집계이며, 이번 문서 정리에서 원본 실행 로그·
문항별 채점을 재검증하거나 현재 `main`에서 다시 실행하지 않았습니다. 전문가의 독립 법률 품질
평가로 해석하지 않습니다.

## 5. 추가 실행 안내

서비스에 접속할 수 없거나 개발 PC에서 실행해야 한다면 [로컬 설치 안내](docs/local-setup.md)를
따르세요.

## 6. 개발자용 자동 검사

코드를 바꾼 뒤 데이터 구축·검색·문서 처리·답변 검증·웹 기능이 기존대로 동작하는지 확인하는
단위·통합·회귀 검사입니다. [4장의 리트리버 평가](#41-리트리버-평가)처럼 고정 질문에서
필수 근거가 검색됐는지 측정하거나, 실제 GPU 답변의 법률적 정확도를 채점하는 시험은 아닙니다.
로컬 구축 가상환경을 활성화한 뒤 저장소 루트에서 실행합니다.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

팀 검색 배포본을 설치했다면 해당 release에서 생성한 제약 파일을 함께 지정해
`python -m pip install -r requirements-dev.txt -c tmp/requirements-search.txt`로 설치합니다.
Windows에서는 UTF-8 모드와 영문 임시 폴더로 실행합니다. 일부 대화형 상담 평가 코드가 인코딩을
지정하지 않고 한글 파일을 읽고 써서 기본 cp949에서 실패하고, 사용자 이름에 한글이 있으면 Chroma가 기본
임시 경로를 쓰지 못하기 때문입니다.

```powershell
New-Item C:\Temp -ItemType Directory -Force | Out-Null
$env:TEMP='C:\Temp'; $env:TMP='C:\Temp'
python -X utf8 -m pytest -q
```

| 영역 | 주요 확인 내용 |
| --- | --- |
| 데이터·배포 | 원천 파싱·적재, 원천 구축·판례 release·팀 배포본 검증, SQLite·Chroma 동기화 |
| 검색 | BM25·KURE·RRF, 채널 분리, 조건부 판례·안내 전달 |
| 생성·검증 | 모의 LLM 응답으로 답변 계획·상태·인용·금액·기간·조건·주체 검사, 검증 보정, 원격→로컬 전환 확인 |
| 문서 | PDF 검증, OCR, 등기·계약서 점검, 세션 문서 검색, 마스킹 |
| 웹 | 채팅·업로드·세션 격리·CSRF·중복 요청·응답 중지, 회원·계약 채팅방·리포트, 대화형 상담 |

화면의 질문 입력·후속 질문·중복 요청 처리는 Node.js로 확인합니다.

```bash
node --test tests/chatting_composer.test.cjs tests/chatting_followup_composer.test.cjs tests/chatting_request_identity.test.cjs
```

- 실제 OCR 샘플은 `REGISTRY_SAMPLE_PDF`, 실제 MySQL 연결은 `LENS_RUN_MYSQL_TESTS=1`과 접속 설정,
  LangSmith 연결은 관련 환경변수를 설정했을 때만 검사합니다. 기본 생성·웹 테스트는 모의 LLM을
  사용하므로 Ollama를 호출하지 않습니다. 테스트는 로컬 `.env`의 `LENS_MYSQL_RELEASE` 값과 관계없이
  통과해야 합니다.
- 최신 통합 기록은 [PATCH-063 검증 기록](docs/patch063-self-test.md)에 있습니다. 전체 검사에는 과거
  PATCH-042 평가 기록과의 불일치가 남아 있으므로, 통과 수만 보고 전체 회귀가 성공했다고 해석하지 않습니다.

Windows 설치·OCR·웹 회귀 확인은 [`docs/windows-verification.md`](docs/windows-verification.md)를 참고합니다.

기존 별도 검증에서는 MySQL 검색 배포본이 89개 질문에서 기존 JSONL과 같은 결과를 냈고
([MySQL 데이터 안내](docs/mysql-data.md)), 대화형 상담의 35턴 상태·라우팅 회귀에서 행동 35/35,
의도 34/35, 필수 사실 27/27을 확인했습니다([대화 회귀 기록](docs/chatting-recovery.md)).
이는 4장의 리트리버 완전 확보 점수나 실제 생성 답변의 법률 정확도 점수가 아닙니다.

## 7. 개인정보 보호와 서비스 한계

### 개인정보 처리

- 업로드 원본은 게스트·회원 모두 `LENS_FILE_ENCRYPTION_KEY`로 암호화해 `LENS_PRIVATE_UPLOAD_ROOT`(기본
  `data/private/`)에 저장합니다. OCR 문맥은 현재 상담·계약에만 연결하며 공용 지식 DB·Chroma에 넣지 않습니다.
- 주민등록번호·전화번호·계좌번호 형태는 질문·화면·문서 문맥에서 마스킹합니다. 모든 개인정보 제거를
  보장하지는 않습니다.
- 게스트 대화와 문서는 마지막 변경 후 1시간, 로그인 사용자의 채팅방 전 임시 대화는 1일 뒤 만료됩니다. 만료
  자료는 홈 화면 접속(게스트) 또는 `python manage.py purge_chats` 실행 때 삭제되므로 그 전까지 디스크에 남을
  수 있습니다. 회원의 계약 채팅방과 첨부는 사용자가 채팅방이나 문서를 삭제할 때까지 보관합니다.
- 답변 생성에서 문서 근거가 포함된 요청은 LangSmith 추적에서 제외합니다.
- 원격 Ollama를 설정하면 답변에 선택된 문서 근거가 원격 서버로 전달될 수 있으므로, 실제 개인정보가 담긴
  문서는 로컬 Ollama 사용을 권장합니다.

### 서비스 한계

- 판례는 개별 사건의 판단이므로 법령처럼 일반 규칙으로 단정하지 않습니다.
- 검색 자료는 검토한 스냅샷입니다. 법령 개정 감시는 관리자 알림만 만들고 검색 자료를 자동으로 바꾸지 않습니다.
- OCR은 흐린 스캔·도장·복잡한 표를 놓칠 수 있고, 계약서의 `미탐지`는 실제 공란을 확정하지 않습니다.
- 민법 후보는 필요 없는 질문에도 최대 3건이 함께 전달될 수 있습니다.
- 응답 중지는 이미 시작된 Ollama 호출을 끝내지 않으므로 GPU가 하나면 다음 질문이 그 호출을 기다릴 수 있습니다.
- HO30 답변 채점 기록은 있지만 전문가의 독립 법률 품질 평가는 아직 없습니다. 대화형 상담 모드는 대화 계층 회귀만 통과했으며
  전체 답변의 품질·지연 확인 전이라 기본으로 꺼 둡니다.

## 8. 프로젝트 구조

```text
manage.py                 Django 관리·마이그레이션·서버 명령
setup_data.py             로컬 원천 구축 실행기
config/                   Django 설정·루트 URL·WSGI·ASGI
accounts/                 사용자 모델·회원가입·로그인
cases/                    계약 채팅방·첨부 보관·체크리스트·일정·PDF 리포트
chat/                     채팅 API·문서 업로드·세션·RAG 연결·대화형 상담 모드·관리 명령
templates/, static/chat/  Django HTML과 CSS·JavaScript
src/
├─ security/              비밀값 탐지·프롬프트 공격 검사
├─ ingestion/             원천 파싱·적재, 원천 구축, MySQL 배포본·판례 release 도구
├─ database/              SQLite 스키마·경로
├─ retrieval/             채널별 BM25·KURE·RRF 검색, 배포본·판례 프로필 로딩
├─ generation/            LangChain·LangGraph·Qwen 생성과 답변 검증
├─ document_check/        문서 추출·OCR·마스킹·등기 신호·세션 문서 검색
├─ contract_check/        계약서 작성 항목·특약 점검
└─ evaluation/            검색·생성·대화 평가 실행기

scripts/                  데이터 관리·평가·문서 생성 명령
tests/                    단위·통합·회귀 테스트
docs/                     실행 안내·설계·평가 기록 (docs/planning/: 기획서)
data/                     원천·평가 자료와 로컬 생성 데이터 (3장)
```

## 9. 상세 문서

| 문서 | 읽을 때 |
| --- | --- |
| [`docs/local-setup.md`](docs/local-setup.md) | 서비스 이용이 어렵거나 개발 PC에서 실행할 때 |
| [`docs/mysql-search.md`](docs/mysql-search.md) | 호환 팀 검색 배포본을 받은 경우의 로컬 설치·검증·단일 질의 |
| [`docs/server-data-setup.md`](docs/server-data-setup.md) | 로컬 원천 구축의 옵션·산출물·재실행 |
| [`docs/case-git-release.md`](docs/case-git-release.md) | Git LFS 판례 release 구성과 무결성 검사 |
| [`docs/mysql-data.md`](docs/mysql-data.md), [`docs/mysql-ingest.md`](docs/mysql-ingest.md) | 담당자용 MySQL 적재·원문 갱신·내보내기 |
| [`docs/local-retrieval-data.md`](docs/local-retrieval-data.md) | 과거 평가 DB 묶음 복원 (일반 설치 아님) |
| [`docs/django-web.md`](docs/django-web.md) | Django 구조·API·요청 제한·저장·개인정보 |
| [`docs/chatting-handoff.md`](docs/chatting-handoff.md), [`docs/chatting-recovery.md`](docs/chatting-recovery.md) | 대화형 상담 모드의 구조·실행과 35턴 회귀 |
| [`docs/registry-check.md`](docs/registry-check.md), [`docs/contract-check.md`](docs/contract-check.md) | 등기·계약서 점검 범위, Tesseract 설치, 한계 |
| [`docs/patch058-retrieval-coverage.md`](docs/patch058-retrieval-coverage.md) | 리트리버 평가 방법·원시 결과·남은 손실 |
| [`docs/patch063-document-consultation.md`](docs/patch063-document-consultation.md) | 첨부 문서 상담·촬영본 판독 보완과 한계 |
| [`docs/windows-verification.md`](docs/windows-verification.md) | Windows 설치·OCR·웹 회귀 확인 |
| [`docs/chunk-schema.md`](docs/chunk-schema.md) | 검색 청크·메타데이터 규격 |
| [`docs/planning/project-plan.md`](docs/planning/project-plan.md) | 3차에서 이관한 프로젝트 기획서 원문 |
| [`deploy/aws/README.md`](deploy/aws/README.md) | 현재 AWS 운영 설정·RunPod 연결·백업·갱신 절차 |
| [`LIST.md`](LIST.md) | 패치별 상태·담당·검증 기록·후속 과제 |

3차 Streamlit UI와 전용 설정·테스트는 현재 운영 경로에서 제거했습니다. `docs/`의 `patchNNN-*`·`eval-*` 문서와 3차에서 이관한 인계 문서(`retrieval-handoff.md`, `case-data-handoff.md` 등)는
당시 조건을 보존한 기록이므로 현재 설치 명령으로 사용하지 않습니다.

기획서 PDF는 `python scripts/build_project_plan_pdf.py`로 `docs/planning/project-plan.md`에서
`docs/planning/jeonseon-project-plan.pdf`를 다시 만듭니다. PDF 파일명은 기존 제출 경로와의 호환을 위해 유지합니다.
