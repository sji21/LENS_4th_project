# PATCH-027 제품 연결·로컬 반영

## 목적과 범위

최종 시험에서 선택한 `record_lookup` 정책을 제품 검색기에 연결한다. 후보 데이터는 143→204조문(민법 10→26)이며 일반 법령 TOP5·민법 TOP3·판례 TOP5·안내 최대2 반환 형식을 유지한다. DEV 고정 근거 확보는 질문 28→43/75, 문맥 30→43/75이고, 반환 근거 손실 7입력과 일반 TOP3 손실 7입력이 남는다. 이 손실을 포함한 순이득 기준으로 채택한다. 상세한 분모·문항·제약은 [최종 결과서](patch027-final-test-result.md)를 따른다.

현재 작업 디렉터리는 `C:/team_project/patch026-worktree`, 브랜치는 `feat/patch-027-law-expansion`이다. 이 문서의 기본 데이터는 **이 작업 디렉터리의 `data/`**다. `C:/team_project/4th_project/data/`는 별도 데이터이므로 병합 이후 동일 절차로 전환해야 한다. 이미 실행 중인 앱은 전환 전에 종료하고, 전환 후 새 프로세스로 시작한다.

## 제품 연결

- `RetrievalService.from_index()`가 `data/index/retrieval-profile.json`을 읽어 확대 정책을 선택한다. 마커가 없는 기존 데이터는 기존 검색 설정으로 동작한다.
- 프로필은 청크·DB 6개 파일, 기본/민법 인덱스의 내용·메타데이터·벡터 해시, 민법 26개 식별자, 모델명에 연결된다. 일부 자료만 복사하거나 경로가 섞이면 검증 실패다.
- 제품의 개념 확장·준용 연결은 `src/retrieval/context_policy.py`, 검색 조합은 `expanded.py`에 있다. 제품 코드는 평가 질문·정답·저장 순위를 읽지 않는다. 민법의 조문 목록은 코퍼스 선택 설정이며 문항별 정답 매핑이 아니다.
- 기존 주제 검색을 민법 후보의 시작점으로 유지하고, 문맥 확장 및 같은 판본의 명시적 준용 관계로 보완한다. 확정일자 기록 조회와 보호 효과 질문을 구분한다.
- 같은 요청에서 반복하는 임베딩은 요청별 캐시로 재사용한다. 서비스 공유 시에도 다른 요청의 캐시와 섞이지 않는다.
- 임베딩을 사용할 수 없는 경우의 BM25 경로도 같은 프로필과 민법 26개를 사용한다. **최종 품질 수치는 BM25+KURE 실행 결과**이며 BM25 단독에 같은 성능을 주장하지 않는다.
- 평가 CLI는 제품 팩터리를 사용하고, 활성 검색기의 실제 깊이·확장 함수·가중치와 프로필을 기록한다. 직접 생성하는 `RetrievalService(...)` 및 메모리 실험용 `from_files()`는 프로필 자동 전환 대상이 아니다.

## 재현·적재·복구

저장소 루트에서 실행한다. Python은 해당 저장소의 가상환경을 사용하며, 이 작업 폴더에서는 `C:/team_project/4th_project/.venv/Scripts/python`을 사용했다. KURE 로컬 모델과 최종 시험의 후보 산출물이 있어야 한다. 새 클론에 바이너리 데이터가 포함되지는 않는다. 후보 생성은 [전체 적재 결과](patch027-full-evaluation.md)와 [출처·재현 절차](patch027-full-sources.md)를 확인한다.

```powershell
$py = 'C:/team_project/4th_project/.venv/Scripts/python'
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:HF_HUB_DISABLE_TELEMETRY='1'
$env:ANONYMIZED_TELEMETRY='False'
& $py -X utf8 -m scripts.patch027_rollout stage --data tmp/patch026-full-evaluation-v2/candidate/data --out tmp/patch027-rollout-stage
& $py -X utf8 -m scripts.patch027_rollout verify --data tmp/patch027-rollout-stage/data --out tmp/patch027-product-preflight-v2
& $py -X utf8 -m scripts.patch027_rollout apply --data tmp/patch027-rollout-stage/data --verification tmp/patch027-product-preflight-v2 --out tmp/patch027-operating-backup-20260913
& $py -X utf8 -m scripts.patch027_rollout verify --data data --out tmp/patch027-product-adopted
```

출력 폴더는 새 경로여야 한다. 검증 전 소스는 커밋하고 작업 트리를 정리해야 한다. 적재 명령은 사전 검증과 현재 소스·기존 데이터가 일치하는지 검사한 뒤 전체 대상 파일·인덱스를 바이트 단위로 검증해 백업한다. 교체 실패뿐 아니라 `receipt.json` 기록 저장·재확인 실패도 이미 교체한 경로를 복구한다. 실패한 기록에 의존하지 않고 원본 파일을 되돌리므로 이때 사용자가 별도 restore를 실행할 필요는 없다. 프로필은 데이터 교체의 마지막에 설치하고 기록 검증까지 끝나야 성공으로 처리한다.

문서·실행기·자료 폴더는 PATCH-027로 통일했다. 원본 측정 기록 내부의 옛 경로와 로컬 실행 위치 보존 기준은 [번호 정정 안내](patch027-renumbering.md)를 따른다.

기본 경로의 사후 검증은 앱과 같은 `RetrievalService.from_index()`를 통해 235입력의 네 채널 순위·본문 해시·출처를 최종 시험과 대조한다. 이 확인이 실패하면 앱을 시작하지 않고 아래 명령으로 되돌린다. 교체됐던 데이터도 별도 보관한다.

```powershell
& $py -X utf8 -m scripts.patch027_rollout restore --data tmp/patch027-operating-backup-20260913 --out tmp/patch027-rollback-preserved
```

## 실행 결과

**제품 연결과 작업 폴더의 기본 데이터 경로 적재·검증을 완료했다.** 제품 구현은 `15a3461`, 실행 기록의 줄바꿈 처리 보완은 `4457916`이며 아래 두 실행은 모두 `d084527` clean에서 수행했다.

| 검사 | 사전 검증 | 기본 경로 적재 후 |
| --- | ---: | ---: |
| 최종 시험과 네 채널 결과 일치 | 235/235 | 235/235 |
| KURE 실제 호출 | 375 | 375 |
| 검색 평균 시간 | 0.386초 | 0.378초 |
| 청크·DB·인덱스 무결성 | 통과 | 통과 |

별도 실험 서비스 대신 제품 팩터리를 사용했으며, 사후 실행은 매개변수 없는 `RetrievalService.from_index()`다. 임베딩 캐시도 측정 도구가 아닌 제품의 요청별 캐시다. 단일 로컬 실행으로 시작 시 모델 로딩과 웹·LLM·동시 부하 시간을 제외한 수치다.

- 데이터: 일반 법령178·민법26 = **204조문**, 판례26·안내6. 기본 인덱스210개와 민법 인덱스26개를 분리 유지했다.
- 백업: `tmp/patch027-operating-backup-20260913/snapshot`. 교체 전 원본은 `previous`에도 남아 있다. [백업 기록](../data/eval/patch027-rollout/receipt.json)을 공유한다.
- [사전 실행 자료](../data/eval/patch027-rollout/preflight/audit.json), [기본 경로 실행 자료](../data/eval/patch027-rollout/adopted/audit.json), [입력별 결과](../data/eval/patch027-rollout/adopted/rows.json)를 보존했다.
- 첫 사전 실행도235입력의 검색 결과는 일치했지만 Windows 혼합 줄바꿈으로 실행 소스 해시 검사가 실패했다. 해당 실행은 채택 근거에서 제외하고 줄바꿈 정규화 보완 후235입력을 새로 실행했다. 기존 코드는 amend하지 않았다.
- 이전 최종 시험의 DEV 점수·손실·분모는 변경하지 않았다. 신규235개 질문을 만든 것이 아니라 같은235입력의 제품 경로 재현 확인이다.
- `C:/team_project/4th_project/data/`와 원격 저장소에는 반영하지 않았다. 병합 후 그 폴더에서도 검증된 데이터·프로필을 함께 전환하고 앱을 재시작해야 한다.

```powershell
& $py -X utf8 -m scripts.patch027_rollout check --data data/eval/patch027-rollout/preflight
& $py -X utf8 -m scripts.patch027_rollout check --data data/eval/patch027-rollout/adopted
```

적재 후 전체 테스트는 **1,042 passed, 3 skipped, 124 subtests passed**다. 스킵은 선택 LangSmith2개·로컬 PDF 표본 미지정1개다. 소스·출처·프로필 변조, 필수 파일 누락, 이전 데이터 호환, 임베딩 불가 시 같은 프로필 선택, 요청별 캐시 분리, 설치 실패 복구와 명시적 복원 테스트를 포함한다.

PR #25 정정 후 전체 테스트는 **1,046 passed, 3 skipped, 124 subtests passed**다. 기록 미저장·부분 저장·잘못된 저장에서 원본 복구와 정상 apply→restore 검사를 추가했다. 검색 정책·기존 실행 결과·현재 DB/인덱스는 변경하지 않았고 실제 벡터 검색은 재실행하지 않았다.

이후 팀원 PR #24가 병합된 main `6e40d21`을 통합했다. LIST·README의 문서 충돌을 양쪽 내용 보존으로 해결했고 통합 후 전체 **1,111 passed, 3 skipped, 172 subtests passed**다. 리트리버 코드에는 통합 변경이 없다.
