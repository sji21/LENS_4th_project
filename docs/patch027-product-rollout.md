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

저장소 루트에서 실행한다. Python은 해당 저장소의 가상환경을 사용하며, 이 작업 폴더에서는 `C:/team_project/4th_project/.venv/Scripts/python`을 사용했다. KURE 로컬 모델과 최종 시험의 후보 산출물이 있어야 한다. 새 클론에 바이너리 데이터가 포함되지는 않는다. 후보 생성은 [전체 적재 결과](patch026-full-evaluation.md)와 [출처·재현 절차](patch026-full-sources.md)를 확인한다.

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

출력 폴더는 새 경로여야 한다. 검증 전 소스는 커밋하고 작업 트리를 정리해야 한다. 적재 명령은 사전 검증과 현재 소스·기존 데이터가 일치하는지 검사한 뒤 전체 대상 파일·인덱스를 바이트 단위로 검증해 백업한다. 교체 실패 시 이미 교체한 경로를 복구한다. 프로필은 마지막에 설치한다. 기존 데이터 외의 파일은 건드리지 않는다.

기본 경로의 사후 검증은 앱과 같은 `RetrievalService.from_index()`를 통해 235입력의 네 채널 순위·본문 해시·출처를 최종 시험과 대조한다. 이 확인이 실패하면 앱을 시작하지 않고 아래 명령으로 되돌린다. 교체됐던 데이터도 별도 보관한다.

```powershell
& $py -X utf8 -m scripts.patch027_rollout restore --data tmp/patch027-operating-backup-20260913 --out tmp/patch027-rollback-preserved
```

## 실행 결과

제품 연결 검증·작업 폴더 적재 및 사후 검증 진행 중. 완료 후 실행 커밋과 235입력 대조 결과를 기록한다.
