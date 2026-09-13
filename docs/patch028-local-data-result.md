# PATCH-028 로컬 DB 적용 결과

## 완료 상태

`C:/team_project/4th_project/data/`에 일반 법령178·민법26, 총204조문을 실제 적용했습니다. 판례26·안내6은 유지합니다. 사용 코드는 최신 main 기반 `feat/patch-028-local-data-setup`입니다. 간편 명령 사용법은 [로컬 데이터 적용](local-retrieval-data.md)을 참고하세요.

## 실행·검증

- 구현: `b3669e1`. 실제 적재·검색 실행: `50c4ed8` clean. 상태 검사의 Chroma 사본 사용 보완: `e8e3a61`.
- 명령: `.\manage-data.ps1 apply -Source C:/team_project/patch026-worktree/data`.
- 사전235입력·기본 경로 사후235입력 모두 PATCH-027 최종 결과와 네 채널의 순위·본문·출처 일치. 각각 KURE375호출, 평균 약0.396초. 새 독립 평가나 LLM 답변 평가는 아닙니다.
- 기존 데이터 백업: `tmp/retrieval-data/20260913T065737Z-21385343/backup`. 사전/사후 결과와 백업 영수증은 [공유 검증 자료](../data/eval/patch028-rollout/summary.json)에 연결했습니다.
- 사본 검사 보완 후 같은 `apply`를 원본 인자 없이 재실행했습니다. 전체 DB·청크·Chroma·프로필 바이트가 동일하고 새 실행 폴더·백업 생성이 없었습니다.
- 최초 재실행 검사에서는 조문 수·논리 인덱스가 유지됐지만 Chroma 조회가 내부 파일을 다시 저장했습니다. 해당 방식의 바이트 무변경 검증은 실패했으며, 사본 검사로 수정한 후 재검증 통과했습니다. 사전/사후235입력 실측은 이 보완 전 커밋에서 실행된 기록이며 덮어쓰지 않았습니다.
- 메인 적용 후 전체 **1,135 passed, 3 skipped, 172 subtests passed**. 이후 사본 검사 보완을 포함한 관련 **20 passed**. 최종 전체 검사에 새 테스트1개가 포함됐다고 합산하지 않습니다. 건너뜀은 LangSmith미설정2·로컬PDF미제공1입니다.
- 이번 코드는 검증된143→204 묶음 적용을 단순화합니다. 기본 자료가 없는 새 클론은 최초 데이터 준비가 필요하고, 다른 데이터/판본은 명시적인 검토 없이 교체하지 않습니다.

## 기존 로컬 변경 보존

원래 기본 작업 폴더는 PATCH-024 브랜치에 미커밋 변경5개가 있었습니다. 해당 파일 원본·diff·브랜치정보는 `tmp/patch028-preserved-patch024/`에 백업했고 `preserve PATCH-024 local review before PATCH-028 rollout` Git stash에도 보관했습니다. 이번 패치에 섞거나 삭제하지 않았습니다.
