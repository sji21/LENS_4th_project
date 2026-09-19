# PATCH-057 공용 MySQL 검증과 PR 범위

2026-09-19 공용 `Lens_knowledge` DB에 실제 연결해 배포 대상 세 스냅샷의 전체 행·검색 스트림과 원문 조회를 검증했다. 이번 확인은 기존 적재 결과를 읽은 것이며 DB를 새로 적재하거나 수정하지 않았다.

## 코드·원격 기준

- 구현: `ca16e0d0f62fbc2dc5ba6be450c94246d29f2898` (`test/mysql-retriever`).
- PR 브랜치: `feat/patch-057-mysql-knowledge-retrieval`, 대상: `main`.
- 통합 기준 main: `fc87c38627b5be530e5252804b531b887ec7f39a`.
- 구현 브랜치 이후 main에 PR #38(PATCH-055 판례 Git LFS 배포), PR #39(PATCH-056 HO30 봉인)가 병합됐다. 두 작업의 코드·자료·문서를 보존했다.
- 충돌은 `.gitattributes`, `.gitignore`, `docs/case-git-release.md`, `src/retrieval/service.py`, `tests/test_case_profile.py`에서 해결했다. LFS·HO30 원본 보존 규칙, main 설치 검증 보완, MySQL 선택형 검색 진입점을 함께 유지했다.
- MySQL 연결·스키마·전체 데이터 검증 모듈은 이번 main 통합에서 변경하지 않았다. 검증 보고서에 당시 Git blob ID를 기록했다.

## 실제 DB 검증

환경: Python 3.12.14, SQLAlchemy 2.0.54, PyMySQL 1.1.2, MySQL 8.4.11. UTC `2026-09-19T14:29:37`에 검증을 시작했다.

| 스냅샷 | 확인한 주요 데이터 | 전체 행·스트림 해시 | 유형별 조회 |
| --- | --- | --- | --- |
| base | 법령 29개·조문 204개·판례 26건·안내 2건 | 일치 | 법령·판례·안내 3건 성공 |
| cases | 법령 4개·조문 133개·판례 8,377건·안내 2건 | 일치 | 법령·판례·안내 3건 성공 |
| civil | 민법 1개·조문 26개 | 일치 | 민법 1건 성공 |

- `verify_snapshot`으로 모든 원문·관계·판례 이력 테이블의 건수 및 행 해시와 검색 스트림의 건수·순서·본문·메타데이터 해시를 검증했다.
- 서버 매니페스트가 전달받은 `shared-v3` 배포 매니페스트와 모두 일치한다.
- 판례 버전 8,559건, 관찰·별칭 각각 8,586건이 보존됐다.
- `trace_chunk`의 7개 대표 조회에서 원래 JSONL 본문, 연결 문서 및 법령·판례·안내 원문을 확인했다.
- `Len_판례data.zip`의 SQLite·JSONL 원본 무결성과 생성 스냅샷 ID도 서버의 cases 스냅샷과 일치했다.
- 위 표는 스냅샷별 건수다. 중복이 있는 법령·안내를 합산한 고유 데이터 수로 해석하지 않는다.

기계 판독 결과: [실제 DB 검증 JSON](planning/mysql-live-verification-20260919.json). 접속 호스트·계정·비밀번호는 포함하지 않는다.

## 접속 설정

검증에 제공된 `.env`는 `MYSQL_*` 이름과 `MYSQL_UESR` 오타를 사용하고 있었고 포트 값도 잘못되어 있었다. 사용자에게 확인한 포트 `33064`와 코드가 요구하는 `LENS_MYSQL_*` 이름을 프로세스 안에서만 적용했다. 원본 `.env`는 수정하지 않았다.

팀원은 [설정 안내](mysql-data.md)의 이름을 그대로 사용하고, 실제 DB명은 대소문자를 포함해 `Lens_knowledge`로 지정한다. 접속값과 비밀번호는 Git에 넣지 않는다. Django 회원·대화용 `DB_*` 설정과 지식 DB용 `LENS_MYSQL_*` 설정은 별개다.

## 통합 후 회귀 검사

통합 코드의 MySQL 이전·갱신·검색, 판례 프로필, 서버 설치 관련 5개 모듈에서 **72 passed, 9 skipped**를 확인했다. 9개 생략은 별도 DB를 생성·삭제하는 opt-in MySQL 통합 테스트다. 공용 DB에는 쓰기를 수행하지 않았고 위 읽기 전용 실측으로 적재 상태를 별도 확인했다.

```powershell
$env:LENS_RUN_MYSQL_TESTS="0"
python -m pytest -q tests/test_mysql_transfer.py tests/test_mysql_ingest.py tests/test_mysql_search.py tests/test_case_profile.py tests/test_server_build.py -p no:cacheprovider
```

초기 검증용 환경의 의존성·sparse-checkout 자료 누락과 pytest 임시 디렉터리 권한 오류를 해결한 후, 제품 코드 추가 변경 없이 전체 5개 모듈을 다시 실행한 결과다. 마지막 실행에는 작업 공간 내부의 새 `--basetemp`를 지정했다. 실제 Chroma 증분 갱신 검사도 포함하지만 임베딩·생성 연결 테스트의 모의 모델은 실제 KURE·LLM 응답 검증을 대신하지 않는다. [테스트 목록·패키지 버전](planning/mysql-pr-integration-tests-20260919.json)에 실행 조건을 기록했다.

문서 링크, 변경된 Python 14개 파일 구문 검사, `git diff --check`도 통과했다.

## 범위와 남은 확인

- 이번 성공 판정은 실제 DB 연결·적재 상태·원문 조회에 해당한다. 검색 순위·법률 답변 정확도·실제 LLM 응답을 새로 평가한 결과가 아니다.
- 2026-09-18의 이전·갱신·검색 검증 기록은 `docs/planning/mysql-*-validation.json`에 기존 실행으로 보존한다. 89문항 검색 비교와 모의 LLM 전달 결과를 이번 실행 결과로 합치지 않는다.
- MySQL 경로의 실제 LLM, 다른 팀원 PC 설치 및 최종 PR 전체 테스트는 이번 확인 범위에 포함하지 않았다. PATCH-055의 RunPod 기록은 해당 경로의 과거 결과다.
- 기존 `LENS-MySQL-search-shared-v3.zip`은 코드 바이트 해시를 고정한다. Git LF/CRLF 체크아웃이나 변경된 코드에는 다시 검증·생성해야 할 수 있다. 이번 PR은 기존 ZIP의 모든 PC 호환성을 보장하지 않는다.
- 모델 가중치·공용 DB 접속값·개인 `.env`·MySQL 검색 ZIP은 PR에 포함하지 않는다. 판례 LFS 데이터는 main의 기존 배포를 유지한다.
- PATCH-057은 리뷰 중이며 PR 생성은 main 병합이나 서버 배포 완료를 뜻하지 않는다.
