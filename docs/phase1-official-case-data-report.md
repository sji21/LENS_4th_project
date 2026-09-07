# 공식 주택임대차 판례 데이터 스테이징·리뷰 후 재검증 보고서

## 기준과 범위

- 기준 커밋: `origin/main`의 `f4cd49a` (PATCH-021 포함)
- 작업 브랜치: `fix/patch-023-review-hardening`
- 작업 패치: `PATCH-023` (PATCH-022 리뷰 보완)
- 출처: 국가법령정보 공동활용 OPEN API의 공식 판례 상세정보
- 대상: 주택임대차 관련 민사 판례의 공식 판결요지
- 청크 규칙: 판례 1건 = 청크 1개
- 원천·SQLite·청크·Chroma는 `.gitignore` 대상이며, 이 문서는 데이터 자체가 아닌 재현·검증 상태를 기록한다.

## 검토 전 로컬 스테이징 기준선

| 항목 | 실제 수량 | 경로 |
| --- | ---: | --- |
| 최종 공식 후보 목록 | 348건 | 당시 최초 검증 실행의 후보 목록(현재 입력과 다름) |
| 공식 상세 응답 | 517건 | `data/raw/phase1_official_case_details.jsonl` |
| 표준 적재 대상 `CaseRecord` | 207건 | `data/parsed/case_records.jsonl` (검토 전 기준선) |
| 스테이징 SQLite 판례 | 207건 | `data/database/phase1_official_cases.sqlite3` |
| 검색용 판례 청크 | 207건 | `data/chunks/phase1_official_cases.jsonl` |
| 스테이징 Chroma 벡터 | 207건 | `data/index/phase1_official_cases_kurev1_1024/` |

SQLite를 읽기 전용으로 점검한 결과, 사건번호 중복은 0건이며 207건 모두 정확히 하나의
`case:{case_id}#0` 청크를 갖는다. 따라서 기존 프로젝트의 **판례 1건 = 청크 1개** 규칙은 유지된다.
위 207건은 넓은 키워드 범위·사건번호 단독 중복 제거·판결요지의 `full_text` 사용 정책으로
만든 **검토 전 기준선**이다. 리뷰 후 변환 계약의 성공 건수나 운영 적재 대상으로 사용하지 않는다.

## 리뷰 후 안전 변환 재검증

2026-08-30에 `phase1_official_case_details.jsonl`을 새 변환기로 검사했다. 원천 SHA-256은
`66f9f5fe57fb4f3903906c89d7ee371a3739d6a0b5aaee384da5c049681c81e1`이며, 결과는 아래와 같다.

| 항목 | 건수 | 처리 |
| --- | ---: | --- |
| 입력 | 517 | 전체 검사 |
| 자동 적재 후보 | 42 | 출력 보류 |
| 범위·수동·짧은 요지 제외 | 64 | `excluded` 보고 |
| API/필수 필드 오류 | 269 | `errors` 보고, 종료 코드 1 |
| 적용 법령 불명확 | 142 | `needs_review` 보고 |
| 사건번호 동일성 충돌 | 0 | `conflicts` 보고 |

오류가 있으므로 `data/parsed/case_records.reviewed.jsonl`은 생성하지 않았고, 기존
`data/parsed/case_records.jsonl`도 변경하지 않았다. 세부 사유와 판례 ID는 Git 제외 경로의
`data/parsed/case_records.reviewed.report.json` 및
`data/parsed/case_records.reviewed.manifest.json`에서 확인한다. 운영 적재 전에는 오류 269건의
상세 API 응답을 재수집·보정하고, 수동 검토 142건의 포함 여부를 확정해야 한다.

공개 상세 페이지 재조회 표본 20건은 모두 국가법령정보센터 판례 상세가 아닌
국세법령정보시스템 페이지로 이동해 원문을 돌려주지 않았다. 또한 판결요지 누락 표본은
공개 상세 페이지의 `판시사항`·`전문`은 존재해도 공식 `판결요지` 항목 자체가 없었다.
따라서 `판시사항`을 판결요지로 대체하지 않으며, 보고서의 `error_records` 원천 URL 목록으로
정상 국가법령정보 공동활용 API 응답을 다시 수집해야 한다.

## 공개 API 검증 원천 재구축

검토 후에는 기존 상세 응답을 신뢰하지 않고, 최종 후보 348건을 국가법령정보 공동활용 API에
직접 재요청했다. `판결요지`·전문·기본 메타데이터를 모두 반환한 154건만
`phase1_official_case_details.verified.jsonl`에 기록했다. 나머지 194건은 수집 불가 보고서에
분리했다(판결요지 미제공 120건, 판례 상세 응답 미반환 74건).

이 348건은 최초 검증 실행의 역사적 수치다. 상세 응답을 전혀 반환하지 않은 74건은 최종 후보 입력에서
제외해 현재 `data/raw/final_phase_official_case_candidates.jsonl`은 **274건**이다. 이 변경된 데이터로
재검증한 결과는 완전 응답 154건·`information_missing` 120건·수집 불가 0건·동일성 불일치 0건이며
`published: true`다. 남은 120건은 `case_id` 필수 일치와 사건번호·법원명·선고일 중 두 개 이상 일치가
확인될 때만 정보 부족으로 분류한다. 이 상태는 “판결요지가 없습니다. 원문을 확인해주세요.”로 MVP에
표시할 수 있지만, 검색·생성용 판례 코퍼스에는 넣지 않는다.

검증 원천 154건을 엄격 변환한 초기 결과는 자동 적재 13건·범위 제외 68건·수동 검토 73건·오류
0건·충돌 0건이다. 이는 **PATCH-023 공개 API 초기 검증 기준선**이며, 과거 207건은 PATCH-022의
검토 전 역사적 스테이징 기록이다.

제공된 `case_records.residential_review_classification.csv`를 `case_id` 기준으로 적용한 2026-08-30 로컬 재변환에서는
수동 검토 73건 중 승인 7건만 추가했다. 제외 39건은 `excluded`, 보류 27건은 `needs_review`로 남겼다.
그 결과 현재 `case_records.verified.jsonl`은 **20건**(초기 자동 13건 + 수동 승인 7건), 제외 107건,
보류 27건, 오류 0건, 충돌 0건이다. 이 20건 검증 파일만 운영·평가 입력으로 사용하며, 운영 통합
DB에는 아직 반영하지 않았다. 로컬 별도 스테이징 SQLite와 청크도 각각 20건으로 다시 적재했고
청크 규격 검사를 통과했다.

## 최신 main과의 호환성

| 최신 main의 계약 | 판례 스테이징 상태 | 판정 |
| --- | --- | --- |
| 공통 SQLite의 `documents`·`cases`·`chunks` 사용 | `src.ingestion.load_cases`가 기존 테이블만 적재 | 호환 |
| 공통 Chroma `knowledge_chunks`와 KURE-v1 1024차원 경로 | `doc_type=case` 및 동일 메타데이터 규격으로 추출 | 호환 |
| `RetrievalService`가 법령·판례·안내를 분리 반환 | 판례 청크에 `case_id`, `case_number`, `court_name`, `decision_date`, `source_url` 포함 | 호환 |
| LLM 답변은 판례를 사례로 표시하고 출처 링크는 화면이 별도 표시 | 판례 `Evidence`의 citation과 `source_url` 생성 가능 | 호환 |

판례 청크만 색인할 때 Chroma 정리 범위는 `doc_type=case`로 제한된다. 따라서 같은 컬렉션에
들어 있는 법령(`law`·`decree`·`rule`)과 안내(`guide`) 벡터는 삭제되지 않는다.

## 아직 운영 통합이 아닌 이유

현재 `data/database/knowledge.sqlite3`에는 법령·판례·청크가 모두 0건이다. 검토 전 207건은
`phase1_official_cases.sqlite3`와 별도 스테이징 Chroma에만 있다. 즉 최신 검색·생성 코드와
형식은 맞지만, 기본 경로의 운영 통합 SQLite/Chroma에는 아직 동기화하지 않았다.

운영 반영은 아래 순서로만 수행한다. 두 결과물은 Git에 커밋하지 않는다.

```powershell
python -m src.ingestion.load_cases `
  --records data/parsed/case_records.verified.jsonl `
  --database data/database/knowledge.sqlite3 `
  --export data/chunks/cases.jsonl

python -m src.retrieval.index `
  --chunks data/chunks/cases.jsonl `
  --path data/index/chroma_kurev1_1024
```

실행 전에는 같은 기본 SQLite에 적재된 실제 법령 청크와 함께 검색해야 한다. 판례만 있는
스테이징으로는 최신 `RetrievalService`의 법령·판례 결합 답변을 운영 수준으로 판정할 수 없다.

## 평가 기록의 상태

기존 `data/parsed/phase1_case_retrieval_smoke_test.json`은 **129건** 코퍼스와 19개 자동
키워드 정답군 기준의 과거 스모크 테스트다. 이 파일의 Hit@1 73.68%, Hit@5 84.21%, MRR 0.7807은
현재 207건 코퍼스 또는 최신 main의 통합 검색 성능을 의미하지 않는다.

따라서 운영 동기화 전에는 현재 검증된 `case_records.verified.jsonl`과 실제 법령 청크를 함께
사용해 질문·정답 판례번호가 확정된 수동 평가를 다시 실행하고, 그 결과를 별도 보고서에 기록해야
한다. 특히 최우선변제,
확정일자·우선변제, 임차권등기명령은 이전 스모크 테스트에서 정답 누락이 있었으므로 재확인 대상이다.

## 이번 기준 정리의 결론

최신 main에 맞추기 위해 DB 스키마나 검색·생성 코드를 변경할 필요는 없었다. 다만 공식
상세 원천을 표준 입력으로 재생성할 수 있도록 `src.ingestion.parse_cases`를 추가했다. 이
변환기는 오류·제외·수동 검토·충돌을 분리하고, 오류가 있으면 기존 결과물을 보호한다.
따라서 오류가 해소되고 수동 검토가 확정된 뒤에만 새 공식 적재 건수를 확정할 수 있다.
원격 main에는 이 작업으로 인한 변경이나 푸시를 하지 않는다.
