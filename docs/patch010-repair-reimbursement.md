# PATCH-010 수리비 선지출·상환 표현 검색 보완

## 변경

개발용 DEV-006의 “수리하고 38만 원 냈어요. … 이 돈 달라고 할 수 있나요?”는 민법 제623조만 반환하고 제626조를 놓쳤다. 제626조는 이미 DB와 별도 민법 인덱스에 존재했지만 기존 조건이 `제 돈`, `돌려받`, `청구` 등의 표현에 한정되어 상환 의도를 인식하지 못했다.

기존 조건에 더해 수리 문맥, 지출 완료 표현, 그 돈 또는 수리비·수선비를 요구하는 표현의 결합을 인식한다. 비용 납부나 금액 단어 하나만으로는 새 조건이 발동하지 않는다. 해당 경우 기존 검색 경로에서 제626조와 제623조를 먼저 반환한다. 상환 가능 여부를 확정하는 법적 판단이 아니라 검토에 필요한 조문의 검색이다.

법령·인덱스 추가, 기존 7개 민법 조문 범위 확대, 판례·안내 조건, LLM 답변 생성은 변경하지 않았다. 부정문·발화 주체·복잡한 대화 전반을 의미 분석하는 변경은 아니며, 기존 다른 키워드 조건의 한계도 남아 있다.

## 검증

- 관련 테스트: 96 passed, 86 subtests passed.
- 전체 테스트: 628 passed, 3 skipped, 124 subtests passed. 스킵은 비활성 LangSmith 2개와 비공개 PDF 통합 검사 1개다.
- 새 테스트 11개: 실제 DEV-006 및 수리비 지급·상환 표현 4개, 비관련 계약금·보증금·납부·통지 등 6개.
- PATCH-009의 입력·DB·인덱스 복사본을 그대로 사용해 100문항의 두 입력 방식 200개를 모두 재검색했다.
- DEV-006 두 입력 모두 제626조가 TOP5 미반환에서 **1위**로 바뀌고 제623조는 **2위**다. 나머지 **198개 입력의 법령 TOP5 순위는 동일**하다. 판례 TOP5·기관 안내 TOP2는 200개 입력 모두 동일하다.
- 기존 공개 회귀: 법령 Dev 24문항 Hit@3 100%, Holdout 18문항 94.444%, 민법 혼합셋 15문항 80%. PATCH-008 기록과 전체 지표 및 채점 대상의 법령 순위가 동일하다.

이는 공개된 개발 문항의 회귀 결과다. 100문항 전체 성공률, 독립 평가 정확도 또는 LLM 답변 품질을 의미하지 않는다. 제626조 검색 누락을 해결했지만 다른 문항의 데이터 공백이나 DEV-006의 개별 사실·예외 판단까지 해결한 것으로 보지 않는다.

## 재현

수정 전 PATCH-009 코드에서 공유 질문셋으로 만든 실행 결과와 스냅샷 및 PATCH-008 공개 평가 기록이 필요하다. 개인 폴더 경로를 고정하지 않고 `--baseline-run`, `--out`, `--baseline-report`로 지정한다. 기존 출력은 덮어쓰지 않는다. PATCH-010 코드로 기준선을 새로 만든 뒤 자신과 비교하지 않도록 기준선 manifest의 코드와 검색 코드 해시를 먼저 확인한다.

```powershell
.venv/Scripts/python -m pytest -q
.venv/Scripts/python scripts/dev100_diagnostics/repair_regression.py --baseline-run tmp/dev100-team-run --out tmp/patch010-review
.venv/Scripts/python scripts/dev100_diagnostics/repair_public_regression.py --run-dir tmp/patch010-review --baseline-report tmp/patch008/regression-final.json
```

원시 결과는 `tmp/patch010/dev100-after.json`, 공개 회귀는 `tmp/patch010/public-regression.json`, 검증한 코드 해시는 `tmp/patch010/code-hashes.json`에 보존한다. 로컬 경로 구조는 PATCH-009 문서를 따른다. 이 도구는 범용 평가기가 아니라 해당 개발 기준선과 비교하는 회귀 도구다.

PATCH-009 공유 입력 보완 병합 후에는 `tmp/patch009-shared-verification` 기준선에서 `tmp/patch010-shared-verification`으로 200입력을 다시 비교했다. DEV-006 두 입력만 변경되고 나머지 법령·판례·안내 순위는 동일했다. 새 경로 옵션으로 공개 회귀 지표·조문 순위 일치도 재확인했으며 관련 테스트 96개·86 subtests가 통과했다. 검색 코드 자체는 최초 628개 전체 테스트 실행 이후 변경하지 않았다.
