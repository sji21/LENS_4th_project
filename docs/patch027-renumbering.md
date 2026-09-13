# PATCH-027 파일명 정리와 과거 경로 보존

팀원 PR #24에서 PATCH-026을 먼저 사용했으므로 우리 법령 확대 작업 전체는 **PATCH-027**입니다.
초기 로컬26·27 실험 통합 이력 때문에 이름이 섞였지만, 별도 패치 두 개를 제출하는 것이 아닙니다.
현재 PR 제목·본문은 [PATCH-027 PR 문서](patch027-pr.md), 적용 상태는 [제품 반영 결과](patch027-product-rollout.md)를 따릅니다.

## 문서 이름 정리

| 이전 파일명 | 현재 파일명 |
| --- | --- |
| `patch026-procedure-law-expansion.md` | [patch027-procedure-law-expansion.md](patch027-procedure-law-expansion.md) |
| `patch026-full-scope-plan.md` | [patch027-full-scope-plan.md](patch027-full-scope-plan.md) |
| `patch026-full-sources.md` | [patch027-full-sources.md](patch027-full-sources.md) |
| `patch026-full-evaluation.md` | [patch027-full-evaluation.md](patch027-full-evaluation.md) |
| `patch026-pr.md` | 이 문서 `patch027-renumbering.md` |

문서 제목·연결을 PATCH-027로 정리했습니다. 초기 실험의 보류 판정과 수치는 당시 기록으로 표시하고 현재 반영 결과와 구분합니다.

## 이름을 유지한 재현 자산

- `scripts/patch026_*.py` 7개와 `tests/test_patch026_*.py` 3개: 번호 변경 전 적재·평가 실행기 및 해당 회귀 검사입니다.
- `data/eval/patch026-expansion/`, `patch026-scope/`, `patch026-full/`: 원문·고정 수집 명세·실측 결과·해시 자료입니다.
- `tmp/patch026-full-evaluation-v2/` 등 로컬 경로: 기존 실행 당시 산출물 위치입니다.

캡처의 `source_hashes`에는 실제 실행한 `scripts/patch026_*.py` 경로가 포함되어 있고, manifest·후속 평가도 이 경로를 참조합니다. 이 자료를 이름만 일괄 치환하거나 해시를 다시 써서 원래 실행 기록처럼 만들지 않습니다. 위 경로는 **PATCH-027의 번호 변경 전 재현 자산**이며 팀원의 PATCH-026 코드가 아닙니다.
