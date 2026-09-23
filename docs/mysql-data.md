# MySQL 지식 데이터 저장·이전·청크 내보내기

## 현재 구현 범위

MySQL 8.4 이상에서 기존 SQLite 원문·관계·판례 이력 및 검색 JSONL 메타데이터를 코퍼스 버전별로 보존한다. 이후 검색용 청크는 MySQL만 읽어 생성한다. MySQL 연결은 Django 회원·대화 DB와 별개다.

현재 구현: 스키마 생성, 이전 원본 점검, 원문·청크·이력 이전, 재실행 검증, MySQL 단독 JSONL 내보내기, 청크에서 원문으로 출처 추적.

2026-09-18 공용 `Lens_knowledge` DB에 기본·민법·판례의 세 스냅샷을 적재했다. 판례 스냅샷에는 판례 8,377건과 버전 8,559건, 관찰/별칭 각각 8,586건이 보존됐다. 전체 원문 컬럼·행과 검색 메타데이터 검증, 공용/로컬 내보내기 파일 일치 검증이 통과했다. 근거는 [검증 기록](planning/mysql-transfer-validation.json)에 있으며 접속 비밀번호는 포함하지 않는다.

MySQL 배포본에서 BM25·Chroma를 생성·검증·활성화하는 앱 연결도 구현했다. 이전 JSONL과 MySQL 배포본을 같은 검증 색인으로 비교한 89개 질문에서 검색 결과가 모두 일치했고, 원본 DB가 없는 별도 폴더에서 앱 검색 진입점 실행과 모의 LLM 입력 전달을 확인했다. [검색 설치 안내](mysql-search.md)를 따른다.

신규 원문·문서 교체·삭제·재청킹은 [원문 갱신 안내](mysql-ingest.md)의 `mysql_ingest` 명령을 사용한다. 실제 MySQL에서 변경·재실행·실패 시 미반영을 확인했고, 공용 HUG 원문 재청킹과 검색 배포본 갱신·되돌리기도 검증했다. [갱신 검증 기록](planning/mysql-update-validation.json)에 결과를 남겼다.

2026-09-18 이전 검증 당시에는 실제 LLM 연결과 다른 팀원 PC 재현이 남아 있었다. 이후 r2 배포본은 Windows·Apple Silicon macOS·Linux에서 설치·검증·검색을 확인했지만, 현재 코드의 새 팀 ZIP은 아직 없다. EC2에는 현재 코드와 서버 환경에 맞춰 같은 MySQL 스냅샷에서 별도 release를 구축했다([검색 배포 안내](mysql-search.md), [EC2 전환 기록](../deploy/aws/MYSQL-SEARCH.md)). 기존 판례 평가 20문항 중 8문항은 정답 ID가 당시 코퍼스에 없어 현재 코퍼스용 평가 자료 보완이 필요하다. JSONL 내보내기만 성공한 배포본에는 `index_status: not_built`가 기록되며 검색 배포본 생성이 성공하면 별도 `release.json`에 `ready`가 기록된다. 전체 실행 계획은 [실행 계획서](planning/mysql-retrieval-execution-plan.md)를 따른다.

2026-09-19 재확인에서도 세 스냅샷 전체 행·스트림 해시와 유형별 원문 조회가 통과했다. [실제 DB 재검증](mysql-live-verification.md)에 이번 실행 범위와 접속 설정 주의점을 기록한다.

## 설치 및 설정

```powershell
python -m pip install -r requirements-mysql.txt
```

`.env` 또는 프로세스 환경변수에 다음을 설정한다. 실제 비밀번호를 명령행·Git·공유 문서에 넣지 않는다.

```dotenv
LENS_MYSQL_HOST=YOUR_MYSQL_HOST
LENS_MYSQL_PORT=3306
LENS_MYSQL_DATABASE=lens_knowledge
LENS_MYSQL_USER=YOUR_INGEST_USER
LENS_MYSQL_PASSWORD=YOUR_PASSWORD
LENS_MYSQL_SSL_CA=
```

설정 파일을 별도로 보관한다면 모든 명령 앞에 `--env-file`을 지정할 수 있다. 명시한 파일의 값이 프로세스 환경보다 우선한다.

```powershell
python -m src.ingestion.mysql_transfer --env-file tmp/mysql-shared.env init
```

이번 공용 서버의 확인된 포트는 `33064`, DB명은 대소문자를 포함해 `Lens_knowledge`다. 위 예시의 기본값을 그대로 사용하지 말고 공유받은 설정을 적용한다. `MYSQL_HOST`·`MYSQL_UESR` 등 다른 이름은 인식하지 않으므로 `LENS_MYSQL_*` 키를 사용한다.

DB명은 서버 관리자가 만든 이름을 사용한다. 프로그램은 서버나 DB 자체를 만들지 않고 지정한 DB 안에 `knowledge_` 접두어의 테이블을 생성한다. 초기화 계정은 해당 DB의 스키마 생성 권한, 이전 계정은 INSERT/SELECT 권한, 내보내기·검증·추적 계정은 SELECT 권한이 필요하다. 앱 실행 계정에 스키마 변경 권한을 공유하지 않는다.

SQLAlchemy URL 객체로 접속값을 전달하므로 비밀번호의 특수문자를 URL에 직접 조합하지 않는다. 파라미터 바인딩, 트랜잭션 rollback, 연결 재확인, UTF-8, strict SQL mode를 사용한다. TLS CA를 지정하면 인증서와 호스트를 함께 검증한다.

## 저장 방식

- `knowledge_snapshots`: 코퍼스 이름·버전·테이블/청크 논리 해시 manifest.
- `knowledge_documents`, `knowledge_laws`, `knowledge_law_versions`, `knowledge_law_articles`, `knowledge_cases`, `knowledge_guides`, `knowledge_chunks`: 기존 원문과 청크.
- `knowledge_case_versions`, `knowledge_case_observations`, `knowledge_case_aliases`: 실제 판례 배포의 버전·수집 이력·별칭.
- 법령 원문 스냅샷·항호 구조 및 기존 관계·평가 테이블도 원본에 있을 때 함께 이전한다.
- `knowledge_chunk_exports`: stream·순서·원래 JSONL 필드 및 메타데이터. 본문은 `knowledge_chunks.content`를 조인한다.

모든 원문 테이블의 PK와 FK에는 `_snapshot_id`가 포함된다. 법령 원수집본의 기존 `snapshot_id`와 구분한다. 기본·민법·판례의 같은 문서 ID가 서로 충돌하거나 다른 코퍼스의 부모 행에 연결되지 않는다.

같은 코퍼스·버전의 내용은 불변이다. 같은 내용으로 재실행하면 기존 DB 데이터를 다시 검증한다. 내용이 달라졌다면 새 버전을 사용해야 한다. 추가·수정·삭제를 포함한 새 전체 스냅샷을 적재해도 이전 스냅샷은 남는다. 현재 단계에서는 최신 버전을 자동 활성화하지 않는다.

MySQL의 색인 길이 제한을 고려해 키·색인 문자열은 최대 180자로 제한하며 긴 값은 명시적으로 거절한다. 본문과 URL은 LONGTEXT다. 문서 URL+체크섬의 유일성에는 URL SHA-256 생성 컬럼을 사용한다. 원래 URL은 자르지 않는다. ID 비교는 대소문자·후행 공백을 구분하는 `utf8mb4_0900_bin`을 사용한다. 빈 날짜 문자열을 MySQL DATE로 강제 변환하지 않는다.

스키마는 저장소의 기본 DDL과 판례 확장 정의에서 생성한다. 알려지지 않은 원본 테이블·컬럼·타입은 버리고 진행하지 않고 실패시킨다. 스키마 버전과 생성 SQL 해시가 달라지면 명시적인 마이그레이션이 필요하다.

## 실행 순서

명령은 저장소 루트에서 실행한다.

### 1. 초기화

```powershell
python -m src.ingestion.mysql_transfer init
python -m src.ingestion.mysql_transfer schema
```

`schema`는 생성할 SQL을 출력한다. MySQL DDL의 암묵적 commit과 데이터 이전 트랜잭션을 분리했다.

### 2. 판례 배포본 사전 점검

```powershell
python -m src.ingestion.mysql_transfer inspect-source --database data/case_corpus/database/knowledge.sqlite3 --chunks laws=data/case_corpus/chunks/laws.jsonl --chunks cases=data/case_corpus/chunks/cases.jsonl --chunks guides=data/case_corpus/chunks/guides.jsonl --corpus cases --version data-dev-v2-20260915
```

이 명령은 MySQL 접속 없이 원본을 읽기 전용으로 검사한다. SQLite 무결성·외래키, 타입/길이, 청크의 문서·본문·순번·유형, 중복 ID, 누락 청크, JSON 중복 키를 검사하고 내용 해시를 출력한다. 원문·출처의 공식 사이트 대비 의미적 완전성을 인증하는 검사는 아니다.

### 3. 이전

```powershell
python -m src.ingestion.mysql_transfer import --database data/case_corpus/database/knowledge.sqlite3 --chunks laws=data/case_corpus/chunks/laws.jsonl --chunks cases=data/case_corpus/chunks/cases.jsonl --chunks guides=data/case_corpus/chunks/guides.jsonl --corpus cases --version data-dev-v2-20260915
```

모든 테이블과 메타데이터를 하나의 트랜잭션으로 적재하고 원본의 각 컬럼과 전체 행 집합을 MySQL 조회 결과로 대조한다. 마지막 검증까지 통과해야 commit한다. 출력의 `snapshot_id`를 다음 단계에 사용한다.

기본 법령·안내와 민법은 판례와 별도 코퍼스로 이전한다. 기본 `chunks.jsonl`에는 민법도 포함될 수 있으므로 파일명으로 법령 채널 건수를 판단하지 않는다. 아래 이전 예시는 당시 기본 레시피(법령 178·민법 26·시드 판례 26·안내 6)의 스냅샷을 대상으로 한다. 최신 승인 원천 구축 범위(법령 185·민법 31·기본 판례 28·안내 10청크)는 [서버 데이터 준비](server-data-setup.md)를 따른다. 앱의 확대 판례 검색에는 별도의 8,377건 코퍼스와 승인 보완 2건을 사용한다.

### 4. MySQL 검증·내보내기·출처 추적

아래 `SNAPSHOT_SHA256`와 `CHUNK_ID`는 이전 결과의 실제 값으로 바꾼다.

```powershell
python -m src.ingestion.mysql_transfer verify --snapshot SNAPSHOT_SHA256
python -m src.ingestion.mysql_transfer export --snapshot SNAPSHOT_SHA256 --output tmp/mysql-export/cases-v1
python -m src.ingestion.mysql_transfer trace --snapshot SNAPSHOT_SHA256 --chunk CHUNK_ID
```

내보내기는 기존 SQLite와 JSONL 경로를 받지 않는다. MySQL의 원문·이력·메타데이터를 검증한 뒤 같은 트랜잭션에서 stream별 JSONL과 파일 해시 manifest를 생성한다. 기존 출력 디렉터리는 덮어쓰지 않는다. 임시 디렉터리를 완성한 뒤 새 경로로 이동하므로 실패한 중간 결과를 완성된 배포본으로 사용하지 않는다.

JSON 필드 순서·줄바꿈 등 파일 바이트는 초기 JSONL과 다를 수 있다. 원문 문자열·청크 ID·원래 메타데이터 값·배열 순서·필드 유무를 보존하고 JSON 의미 기준으로 비교한다. `None`·빈 문자열·false·누락된 필드를 임의로 합치지 않는다.

`trace`는 지정한 스냅샷의 청크·문서와 조문/판례/안내 원문, 법령 판본·원수집본 연결을 반환한다. 같은 `chunk_id`라도 다른 스냅샷의 현재 원문으로 치환하지 않는다. 단건 추적은 전체 스냅샷 무결성 검증을 대체하지 않으므로 배포 전에 `verify`를 실행한다.

## 검증

일반 테스트:

```powershell
python -m pytest tests/test_mysql_transfer.py -q
```

MySQL 통합 테스트는 별도 서버·관리용 테스트 계정을 설정한 뒤 실행한다.

```powershell
$env:LENS_RUN_MYSQL_TESTS = '1'
python -m pytest tests/test_mysql_transfer.py -q
```

통합 테스트는 `lens_test_`와 무작위 ID를 조합한 새 DB를 만들고 종료 시 그 DB만 삭제한다. 기존 데이터 DB를 비우지 않는다. DB 생성·삭제 권한이 필요하며, 이 플래그가 없으면 실제 MySQL 테스트는 skip된다. `.env`의 접속값을 사용할 때는 먼저 해당 값을 프로세스 환경에 로드해야 한다.

검증 범위는 원본 파일 제거 후 복원, 한글·긴 URL·줄바꿈·JSON 전용 필드·정수 타입, 코퍼스 분리, 멱등성, 버전 덮어쓰기 거절, 늦은 실패의 전체 rollback, FK 위반 및 DB 본문 변조 검출이다.

접속/DDL 구현 참고: [SQLAlchemy MySQL 공식 문서](https://docs.sqlalchemy.org/en/20/dialects/mysql.html), [MySQL Windows ZIP 설치 공식 문서](https://dev.mysql.com/doc/refman/8.4/en/windows-install-archive.html).
