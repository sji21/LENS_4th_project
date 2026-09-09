# PATCH-008 민법 7개 운영 반영

PATCH-007의 원문·구조 보존을 기반으로, PATCH-006에서 준비한 민법 7개를 로컬 운영 산출물에 반영한다. 새 법령 확대나 LLM 평가는 포함하지 않는다. 적용 대상은 이 작업을 실행한 로컬 환경이며 Git 커밋만으로 다른 팀원의 데이터가 바뀌지는 않는다.

## 준비와 검증 방식

- 통합 DB, 기존 청크, 기본 Chroma 폴더 전체를 `tmp/patch008/before/`에 백업했다. SQLite backup API로 일관된 DB 사본을 생성했다.
- 로컬 민법 원문 `data/raw/law/민법-20260317.txt`의 시행일·공포번호 및 7개 조문의 제목·본문을 PATCH-006 준비 레코드와 대조했다. 이번 작업에서 새로 웹 수집했다고 주장하지 않는다.
- 준비 원문 SHA-256: `d7533cfcad78f583466b11a79bdb6684062c64fea907956a9c79af59c02da5d0`.
- 교체 후보는 `tmp/patch008/after/`에서 만들었다. 통합 DB는 133→140조문, 구조 노드는 루트 포함 790개다. 민법 평문 스냅샷 1개와 조문별 URL을 저장했다. 기존 법령의 원문 스냅샷·조문 출처를 추정해서 채우지는 않았다.
- 기본 법령 청크 133개의 원래 바이트를 그대로 유지하고 민법 청크 7개만 추가했다. 구 DB 전체를 재내보내며 기존 메타데이터를 바꾸지 않았다.
- 민법 전용 벡터는 기존 준비 인덱스의 ID·본문이 새 청크와 일치함을 먼저 확인한 뒤, 복사본의 출처 메타데이터만 동기화했다. 임베딩 배열의 일치도 확인했다. 기본 165건 인덱스는 재색인하지 않았다.

## 운영 반영 전 발견한 오발동 수정

### 최종 로컬 적용 상태

2026-09-09 운영 경로 반영 및 `RetrievalService.from_index()` 기본 경로 검증을 완료했다. 기본 165건 + 민법 7건의 ID·본문·메타데이터 172건 전체 일치, 통합 DB 무결성·외래키 검사 통과, 수선/필요비 민법 반환과 보증금 질문의 오발동 방지를 확인했다.

- `data/chunks/chunks.jsonl` SHA-256: `0c3b03f4e58d9a90ed5c55a4bddfbfdb6a2f6d855a08a396b5290d8f8fdce0c9`
- `data/chunks/civil.jsonl` SHA-256: `d196a2b2f236fcce4a104c6bc03ff2f03b234bb1deb5d784249c37dad165fcdc`
- `data/database/knowledge.sqlite3` 적용 직후 SHA-256: `b7914d6e6e416609561cc2c3812ca390e7a4edbd92527e24862a729ae06961ac`

### 수정 내용

`detect_civil_topics`가 균열 신호 `금이`를 부분 문자열로 검사하여 `보증금이`, `계약금이`도 하자로 인식했다. 문맥에 `알려` 또는 `통지`가 있으면 민법 제634조를 앞에 넣어 필요한 기존 조문을 밀어낼 수 있었다.

균열 신호 앞에 한글·영문·숫자가 붙어 있지 않은 경우만 인정하도록 좁게 수정했다. 돈 관련 표현 8개와 실제 균열 표현 3개를 회귀 검사했다. 다른 수선·통지 조건이나 새 법령 라우팅을 광범위하게 튜닝하지 않았다.

## 검증 결과

- 전체 테스트: **617 passed, 3 skipped, 124 subtests passed**. 스킵은 비활성 LangSmith 검사 2개와 비공개 PDF 통합 검사 1개다.
- 기존 법령 Dev: Hit@3 **100%**, Holdout: **94.444%**, 전후 동일. 기존 공개 질문 47개의 법령 TOP3·판례 TOP5·안내 TOP2 순위 변경은 **0건**이다.
- 공개 민법 혼합셋: Hit@3 **80%**. 이전 PATCH-006 결과와 같은 공개 회귀셋으로 독립 평가가 아니다.
- 판례 Dev 13문항 Hit@2 **92.308%**, 대체 Holdout 8문항 **87.5%**, 전후 동일. 민법 데이터 추가를 판례 데이터 확대나 판례 정답 검증으로 표현하지 않는다.
- 새 DEV 100문항의 두 입력 방식 중 민법 주제가 감지되는 입력을 실제 전후 비교했다. 추가로 DEV-010·100의 문맥 입력은 위 금액 오인 수정 후 주제가 감지되지 않더라도 재검사한다. 나머지 입력을 전부 새로 검색한 것으로 주장하지 않는다.

원시 증거: `tmp/patch008/regression-final.json`, `extra-evaluation-final.json`. 수정 전 결과는 `regression.json`, `extra-evaluation.json`으로 보존한다. 이 파일들과 운영 데이터는 Git 제외 대상이다. 최종 로컬 적용 및 실제 기본 경로 확인은 `rollout.json`, `operating-check.json`에 기록한다.

DEV 진단은 민법 주제가 감지된 23개 입력과 수정 확인용 DEV-010·100 문맥 입력 2개, 총 25개다. 후자 두 입력은 수정 후 민법 주제가 감지되지 않아 기존 법령 순위를 유지했다. 100문항 전체 정답률은 산출하지 않았다.

### 파일 해시와 논리 데이터 검증

첫 적용 시 로컬 보조 스크립트의 Windows 경로 구분자 비교 문제로 백업 복구가 실행됐다. SQLite backup 사본은 물리적 파일 배치가 달라 원본 파일 해시와 다를 수 있으므로 복구 사본 자체의 일치도 함께 확인했다. 적용 실패 상태에서 수행한 첫 기본 경로 검사는 165건을 읽어 172건 조건에 실패했으며, 이를 성공 결과로 사용하지 않았다.

또한 Chroma 클라이언트 사용 후 `acquire_write` 잠금 기록 및 HNSW 직렬화 파일이 변경돼 물리적 해시가 달라졌다. 해시 변화만 무시하지 않고 검증 사본과 **165개 ID·본문·메타데이터·임베딩 배열 전체 일치**, SQLite의 `acquire_write` 외 모든 테이블 내용 일치를 확인했다. `base-vector-audit.json`, `base-index-table-audit.json`에 증거를 기록했다. 따라서 기본 인덱스는 **검색 데이터 보존·재임베딩 없음**으로 표현하며, 파일 바이트 불변이라고 주장하지 않는다. 평가에 사용한 백업 인덱스에도 같은 런타임 변화가 발생할 수 있다. 판례·안내 청크 파일은 해시로 불변을 확인했다.

## 남은 검색 한계

- DEV-006의 수리비 반환 질문은 제623조를 반환하지만 필요한 제626조까지 자동 확보되는 것은 아니다.
- DEV-071의 수리비 공제 질문에서 제634조가 감지되는 등 `수리`와 통지 관련 표현의 조건은 추가 검토가 필요하다. 이번 수정은 금액 단어와 균열 단어의 오인에 한정한다.
- 임차인 과실·공공임대 정산·원상회복 등은 추가 법령과 조건 검토가 필요하다. 민법 7개 반영으로 100문항의 필수 근거가 모두 충족되지는 않는다.
- 기존 공개 회귀가 유지돼도 독립 성능이나 LLM 답변 품질을 보증하지 않는다. 보완본의 조건부·대체 근거에 따른 정식 검색 채점은 별도다.

## 재현과 복구

이 환경의 준비·검증·적용 스크립트는 `tmp/patch008/prepare.py`, `evaluate_extra.py`, `apply.py`에 보존한다. 기존 결과를 덮어쓰지 않도록 설계되어 있으므로 같은 디렉터리에서 무조건 재실행하지 않는다. 원천 레코드와 기본 청크를 그대로 보존하는 것이 핵심이다.

공개 법령/민법 회귀 명령:

```powershell
.venv/Scripts/python -m scripts.evaluate_civil_regression --before-chunks tmp/patch008/before/data/chunks/chunks.jsonl --before-index tmp/patch008/before/data/index/chroma_kurev1_1024 --after-chunks tmp/patch008/after/chunks.jsonl --after-index tmp/patch008/before/data/index/chroma_kurev1_1024 --civil-index tmp/patch008/after/civil-index --out tmp/patch008/regression-new.json
```

운영 반영 전 프로젝트 앱이 실행 중이지 않음을 확인한다. 변경 전 파일 해시가 백업 시점과 같고 WAL/SHM이 남아 있지 않은지 확인한 뒤 통합 DB·청크를 교체하고 민법 전용 DB·청크·인덱스를 추가한다. 교체 실패 시 백업 DB·청크를 복원하고 실패 산출물을 검토 경로로 이동한다.

복구할 때도 프로젝트 앱을 중지하고 `before/data/database/knowledge.sqlite3`와 `before/data/chunks/chunks.jsonl`을 해당 운영 경로에 함께 복원한다. 추가된 `data/database/civil.sqlite3`, `data/chunks/civil.jsonl`, `data/index/chroma_civil_kurev1_1024`는 별도 보관 경로로 이동한다. 판례·안내 청크는 해시 일치를 확인하고 기본 인덱스는 위 논리 데이터 검증을 적용한다. 앱을 다시 시작해 기존 코퍼스 경로를 확인한다.

브랜치는 `chore/patch-008-civil-rollout`이며 PATCH-007 브랜치를 기반으로 한다. PR을 만들 경우 PATCH-007을 선행 변경으로 취급해야 한다. 푸시·PR·병합은 이번 실행에 포함하지 않는다.
