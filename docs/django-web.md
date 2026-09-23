# PATCH-013 · Django 웹 전환과 팀원 연결 안내

4차 프로젝트의 기본 웹 실행 경로를 Django 5.2 + HTML·CSS·JavaScript로 전환했다.
채팅, 공식 출처, 계약서·등기 PDF/이미지 분석, 문서 선택·삭제, 새 대화를 제공한다.
기존 `src/` 검색·생성·법률 검증 함수는 수정하지 않고 `chat/services.py`에서 호출한다.
PATCH-062에서 기존 Streamlit 화면과 전용 설정·테스트를 제거했다. 웹 실행 경로는 Django만 사용한다.

PATCH-062의 검색 데이터는 `data/mysql-search/active.json`이 가리키는 검증된 portable
release를 사용한다. 현재 작업 트리의 `django-runtime-20260922`는 로컬 검증 release이며,
새 팀 배포 ZIP은 확정 소스에서 별도로 생성해야 한다. PATCH-043 내부 판례 실험 프로필은
제품에서 지원하지 않으며, 재현용 코드는 `experiments/patch043_case_internal/`에만 둔다.

## 실행

[로컬 설치 안내](local-setup.md)에 따라 OS별 Python 환경·검색 데이터·`.env`·Ollama를
준비한다. 그 절차에서 활성화한 가상환경으로 다음 명령을 실행한다.

```bash
python manage.py migrate
python manage.py check
python manage.py runserver 127.0.0.1:8000 --noreload
```

로컬 HTTP 실행에서는 `DJANGO_DEBUG=true`,
`DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,[::1]`를 사용한다. 기존 `.env`와 API 키를
덮어쓰거나 저장소에 올리지 않는다.
팀원은 브랜치를 받거나 `git pull`한 뒤마다 `.env.example`과 로컬 `.env`를 비교해
새 환경변수를 각자 추가한다. 개인 키와 PC별 경로는 유지하고 변경 후 앱을 재시작한다.
http://127.0.0.1:8000 에 접속한다. Python 변경 후 서버를 재시작한다.
HTML·CSS·JS 변경은 브라우저를 새로고침한다.

첫 HTML 응답에서 모델을 로딩하지 않는다. 브라우저가 준비 상태 API를 호출하면
프로세스별 백그라운드 로더가 기존 검색 서비스를 초기화한다. 초기화가 실패하면
화면에서 재시도한다. 모델 응답 토큰 스트리밍은 제공하지 않으며 처리 중 표시와
경과 시간을 보여준다. 문서 업로드는 파일별로 순차 처리한다.
응답 중지는 늦게 끝난 결과의 저장과 화면 반영을 차단하고 입력창을 즉시 연다. 이미
시작된 Ollama HTTP 호출 자체는 종료하지 않으므로 단일 GPU에서는 새 질문이 해당 호출의
종료를 기다릴 수 있다.

## 역할 분리

| 위치 | 담당 범위 |
| --- | --- |
| `config/` | 환경변수, URL, Django 설정, WSGI·ASGI |
| `accounts/models.py` | `AbstractUser`를 상속한 초기 사용자 모델 |
| `chat/views.py` | HTTP 입력 검증, 세션 소유권, CSRF, 동시 요청 관리 |
| `chat/services.py` | 기존 검색·문서 분석·생성 함수와 웹 상태 사이의 연결 |
| `chat/models.py` | 세션별 대화·마스킹된 문서 문맥과 만료 시각 |
| `templates/` | Django HTML, 폼, 화면 문구 |
| `static/chat/` | 반응형 CSS와 fetch 기반 JavaScript |
| `src/generation/graph.py` | 기존 답변 생성·검증 진입점 (이번 패치에서 수정 없음) |

브라우저 입력 → Django API → 웹 세션 확인 → 기존 질문 해석/문서 선택 →
기존 RAG·검증 → 공개 답변·출처만 JSON 응답 → JS 화면 갱신 순서다.
검증을 통과하지 못한 원시 답변을 대체 답변으로 노출하지 않는다.

## 회원가입 담당자가 이어서 작업할 부분

`AUTH_USER_MODEL=accounts.User`를 처음부터 지정했다. 회원가입·로그인 URL, 폼,
템플릿은 `accounts/`에 추가하면 된다. 관계 모델은 직접 기본 User를 가져오기보다
`settings.AUTH_USER_MODEL` 또는 `get_user_model()`을 사용한다.
관리자 페이지는 `/admin/`이며 필요하면 `python manage.py createsuperuser`로 계정을 만든다.
이번 패치는 관리자 계정을 임의 생성하지 않는다.

현재 채팅은 로그인 여부와 관계없이 브라우저 세션에 귀속된다. 계정별 영구 대화 저장,
다른 기기에서 이어보기, 회원가입·로그인 화면은 아직 없다. 계정에 대화를 귀속하려면
별도 마이그레이션과 소유권 검사가 필요하다. 로그인·로그아웃 때 익명 대화를 유지할지
삭제할지도 팀에서 정해야 한다. 새 대화는 다른 Django 세션 값이나 로그인 상태를 지우지 않는다.
공유 `config/urls.py`, 설정, 공통 템플릿을 수정할 때 작업 경계를 먼저 맞춘다.

웹 DB는 `data/database/web.sqlite3`, 기존 RAG DB는 `knowledge.sqlite3`이다.
두 DB는 별개이고 모두 로컬 생성 자료다. 회원 기능용 migrate를 실행해도 RAG 데이터나
Chroma를 재생성하지 않는다. 팀원은 새 마이그레이션을 받은 뒤 `manage.py migrate`를 실행한다.

## API

| 메서드·경로 | 용도 |
| --- | --- |
| `GET /` | HTML·세션·CSRF 쿠키 준비 |
| `GET /api/state/` | 현재 대화, 문서 공개 요약, 처리 중 여부 |
| `GET /api/readiness/` | 검색 서비스 초기화·준비 상태 |
| `POST /api/readiness/retry/` | 초기화 재시도 |
| `POST /api/chat/` | 질문·선택 문서로 답변 생성 |
| `POST /api/documents/` | multipart `file` 한 개 분석 |
| `POST /api/documents/<id>/delete/` | 문서와 기존 대화 이력 삭제 |
| `POST /api/reset/` | 문서·대화 초기화 |

변경 API에는 CSRF 헤더가 필요하다. 채팅·문서·초기화 요청에는 현재 `conversation_id`와
UUID `request_id`를 보낸다. 같은 대화의 동시 요청은 409로 거부하고 최근 완료 요청
32개의 ID는 재처리하지 않는다. 서버 작업의 15분 임대 잠금이 만료되면 후속 요청이
진행할 수 있으며, 이전 작업은 새 잠금의 결과를 덮어쓸 수 없다.
문서 최대 5개, 파일당 20MB, PDF·PNG·JPG·JPEG, 질문 최대 2,000자,
대화 최대 100개 메시지(질문·답변 50쌍)다. 만료 세션은 410으로 알려 새로고침을 안내한다.

## 저장과 정리

업로드 원본은 웹 저장소에 보관하지 않는다. 업로드 메모리 한도를 적용하며 OCR 라이브러리는
처리 과정에서 임시 파일을 사용할 수 있다. 원문을 공용 RAG DB·Chroma에 적재하지 않는다.
마스킹된 문서 검색 문맥·대화는 서버 웹 DB에 저장한다. 브라우저 상태 API에서는 내부 문맥과
체크섬을 제외하고 공개 분석·답변·출처만 반환한다. 마스킹이 모든 개인정보를 제거한다는
보장은 없으므로 교육 시연에는 가상 문서를 사용한다.

세션 쿠키와 대화는 마지막 성공한 변경 요청으로부터 1시간 유효하다. 브라우저를 닫는
행위 자체가 서버 자료 삭제를 의미하지 않는다. 만료 대화는 홈페이지 접근 시 정리되며,
아무도 접속하지 않으면 디스크에 남는다. 운영 시 다음 명령을 정기 실행해야 한다.
이번 패치에서 자동 스케줄러나 배포는 만들지 않았다.

```bash
python manage.py purge_chats
python manage.py clearsessions
```

문서를 삭제하면 그 문서의 내용이 후속 질문에 남지 않도록 기존 대화도 지운다.
새 대화는 첨부 문서와 메시지를 모두 비운다. SQLite 파일·백업·운영 로그 접근 권한은
서버 관리 대상이며 DB 암호화 기능은 구현하지 않았다.

## 검증과 한계

```bash
pip install -r requirements-dev.txt
python -m pytest tests/test_django_web.py -q
python -m pytest -q
python manage.py makemigrations --check --dry-run
```

개발용 requirements는 Django 웹·검색·문서 처리 회귀 테스트를 포함한다.
Django는 BSD 라이선스의 5.2 LTS 계열로 제한했고 Python 3.11 환경에서 검증했다.
웹 테스트는 CSRF, 세션 격리, 문서 문맥 비공개, 입력 제한, 중복·동시 요청, 잠금 회복,
예외 처리, 문서 삭제·초기화와 기존 RAG 진입점 호출을 확인한다.

2026-09-10: 웹 테스트 27개 통과. 전체 693 passed, 3 skipped, 124 subtests passed.
스킵은 선택적 LangSmith 연결 2개와 로컬 비공개 PDF 1개다.
실제 브라우저에서 검색 준비 완료, 질문 전송, 보류 답변과 공식 출처 3건 표시,
가상 계약서 PNG의 실제 Tesseract OCR·분류·작성 항목 분석, 새로고침 후 상태 유지,
새 대화 확인창과 대화·문서 초기화를 확인했다.
실제 질문은 33.6초 후 기존 검증기가 답변을 보류했다. 이는 웹 연결 확인이며
법률 답변 정확도 개선이나 LLM 품질 평가 결과가 아니다.

현재는 교육·로컬 실행용이며 SQLite 및 동기식 모델/OCR 호출을 사용한다. 여러 사용자의
부하 시험은 하지 않았다. 운영 확장에는 HTTPS·운영 웹 서버·정적 파일 제공·DB 전환·요청
제한·작업 큐 등을 환경에 맞게 준비해야 한다. DEBUG=false에서는 보안 쿠키가 HTTPS를
요구한다. 프로세스를 늘리면 모델 초기화·메모리도 프로세스별로 늘어난다.
