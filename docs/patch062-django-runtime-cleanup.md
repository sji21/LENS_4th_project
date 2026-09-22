# PATCH-062 Django 전용 운영 경로·PATCH-043 실험 보관

## 결정과 범위

PATCH-043 내부 판례 후보는 제품에 채택하지 않는다. 후보 8,395건의 생성 데이터와 모델,
새 독립 평가는 Git 체크아웃에 없으므로 실제 후보 성능이나 법률 답변 품질 통과로 해석하지
않는다. 제품 로더는 `lens-case-internal-v1`을 명시적으로 거부한다.

재현에 필요한 내부 판례 코드·평가 도구는 삭제하지 않고
`experiments/patch043_case_internal/`에 보관한다. 제품 경로인 `src/retrieval`, Django
서비스, 생성 그래프는 이 패키지를 import하지 않는다.

## 구현

- `src/retrieval/case_profile.py`가 PATCH-043 내부 프로필을 명시적으로 거부한다.
- 내부 판례 retriever·profile·metrics, 23개 실행 스크립트, 5개 테스트를 실험 패키지로
  이동하고 모듈 import·해시 검증 경로를 새 패키지에 맞췄다.
- Streamlit 앱·설정·전용 테스트를 제거하고 Django를 유일한 웹 실행 경로로 문서와 구조
  테스트에 고정했다.
- Django 웹 SQLite의 상위 디렉터리를 설정 단계에서 준비하고, 원천 MySQL 설정은
  `.env.example`의 `LENS_MYSQL_*` 키로 정리했다.

## 검색 release와 검증

리트리버 코드 해시가 바뀌었으므로 기존 `patch060-r2-20260921` 배포본을 현재 소스와
혼용하지 않았다. 같은 MySQL export와 case release로 `django-runtime-20260922` 로컬
portable release를 build·verify·activate했다.

| 항목 | 결과 |
| --- | --- |
| release ID | `a5cd80062e499b75435de248297097cf96d38f4c8d98923d8c32007b10c5d7b9` |
| release verify | `verified: true` |
| 인덱스 | 일반 223, 민법 31, 판례 package 8,516 |
| 전입신고 질의 | 법령 5, 민법 3, 판례 2 반환; 생성 호출은 `not_requested` |
| 회귀 | 269 통과, 조건부 생략 5, subtests 97 통과 |
| Django | `manage.py check` 통과, migration drift 없음 |

조건부 생략은 비공개 로컬 PDF 1건과 `LENS_RUN_MYSQL_TESTS=1`이 필요한 실제 MySQL
전송 테스트 4건이다. 이 결과는 현 작업 트리와 로컬 release의 호환을 확인한 것이며,
다른 팀원 환경·새 팀 배포 ZIP·독립 법률 답변 품질 평가를 의미하지 않는다.

## 후속 작업

확정 소스에서 portable release를 다시 build·verify한 뒤 모델과 함께 새 팀 배포 ZIP을
생성해야 한다. 그 전까지 `django-runtime-20260922`는 팀 배포본이 아닌 로컬 검증
release로만 기록한다. 미추적 DB·Retriever 분석 산출물과 평가 데이터는 이 PATCH의
코드·문서 커밋에 포함하지 않는다.
