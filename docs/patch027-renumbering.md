# PATCH-027 경로 통일

팀원의 PATCH-026과 구분하기 위해 이번 법령 확대 작업의 문서·실행기·테스트·자료 폴더를 PATCH-027로 통일했습니다.

| 이전 경로 | 현재 경로 |
| --- | --- |
| `data/eval/patch026-expansion/` | `data/eval/patch027-expansion/` |
| `data/eval/patch026-scope/` | `data/eval/patch027-scope/` |
| `data/eval/patch026-full/` | `data/eval/patch027-full/` |
| `scripts/patch026_{expand,full_eval,full_report,full_sources,sources,subsets}.py` | 같은 이름의 `scripts/patch027_*.py` |
| `scripts/patch026_report.py` | `scripts/patch027_expansion_report.py` (기존 partition 보고기와 이름 충돌 방지) |
| `tests/test_patch026_full.py`, `test_patch026_sources.py` | `test_patch027_full.py`, `test_patch027_sources.py` |
| `tests/test_patch026_report.py` | `tests/test_patch027_expansion_report.py` |

문서 파일은 앞선 정정에서 이미 `docs/patch027-*.md`로 이동했습니다. 현재 실행 명령과 문서 링크도 새 이름을 사용합니다.

## 측정 기록 보존

저장된 JSON·JSONL·HTML은 이동만 했으며 바이트·해시·측정 수치는 변경하지 않습니다. 따라서 **원본 자료 내부의 옛 경로와 당시 실행 커밋·스크립트 해시는 그대로 남습니다.**

`scripts/patch027_paths.py`는 자료를 읽을 때 위 세 데이터 폴더의 경로만 메모리에서 변환합니다. 파일 자체의 해시는 원본 바이트로 검사합니다. 중복되는 옛/새 경로는 오류로 처리하고, 실행 스크립트 해시는 당시 Git 커밋을 기준으로 검증합니다. 새 실행 결과와 과거 실측 기록을 혼동하지 않습니다.

로컬 작업 디렉터리 `C:/team_project/patch026-worktree`와 `tmp/patch026-*`는 Git에 포함되지 않은 실제 작업·기존 실행 위치입니다. 이번 저장소 경로 정리에서는 이동하지 않았으며, 해당 위치를 가리키는 기록과 후보 데이터 사용 예시는 유지합니다.

이번 변경은 경로와 재현 도구 연결만 정리합니다. 제품 검색 코드·운영 DB·인덱스·법령 내용·측정 결과는 바꾸지 않습니다.

## 검증

- 추적되는 `patch026` 파일·폴더명 0개. 원문·측정 자료 136개는 이동 전 Git 객체와 동일.
- 제품 `src/` 변경 0개. PATCH-027 문서 내부 링크 39개 확인.
- 전체 테스트: **1,116 passed, 3 skipped, 172 subtests passed**. 건너뜀은 LangSmith 미설정 2건·로컬 등기 PDF 미제공 1건.
- 자료 경로 변환·원본 해시 보존·경로 충돌 거부·검증 중 원문 비수정 회귀 검사 포함. 실제 벡터 검색·LLM 평가·운영 재적재는 수행하지 않음.
