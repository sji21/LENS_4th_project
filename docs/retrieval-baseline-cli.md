# 4차 PATCH-003 검색 기준선 실행 도구

PATCH-002의 임시 평가 호출을 `src.evaluation.baseline`으로 고정했다.
검색 순위·코퍼스·평가 정답을 변경하지 않는다. 기존 공개 평가셋의 재현·회귀용이며
새 Holdout-v2의 봉인 해제나 튜닝을 수행하는 도구가 아니다.

## 준비

저장소 루트에서 Python 3.11 가상환경을 사용한다. 데이터 준비는
[PATCH-002 기록](eval-patch002-baseline-run.md)의 8.2절을 따른다.
법령·판례·안내 청크 세 파일과 일치하는 Chroma 인덱스가 모두 필요하다.
누락된 청크를 샘플로 대체하거나 빈 인덱스를 새로 생성해 평가하지 않는다.

인덱스의 ID·본문·정규화 메타데이터를 청크와 비교하고 불일치하면 중단한다.
모델명은 기존 인덱스를 만든 모델과 같아야 한다. 도구는 벡터를 재생성하지 않으며,
모델 가중치까지 검증하는 것은 아니다. 동일 이름의 모델 revision 변경에 주의한다.

## 실행

다음 `tmp/` 결과는 Git 제외 경로이며, 기존 보고서가 있으면 덮어쓰지 않고 실패한다.
다시 측정할 때는 새 출력 파일명을 지정한다.

```powershell
.venv/Scripts/python -m src.evaluation.baseline --kind law --eval-set data/eval/dev.jsonl --out tmp/patch003-law-dev.json
.venv/Scripts/python -m src.evaluation.baseline --kind law --eval-set data/eval/holdout.jsonl --out tmp/patch003-law-holdout.json
.venv/Scripts/python -m src.evaluation.baseline --kind case --eval-set C:/evaluation/case-dev.jsonl --out tmp/patch003-case-dev.json
.venv/Scripts/python -m src.evaluation.baseline --kind case --eval-set C:/evaluation/case26_external_8.jsonl --out tmp/patch003-case-external.json
```

판례 입력 경로는 보관 중인 실제 파일 경로로 바꾼다. 판례 정답을 생성·수정하거나
외부 평가셋을 저장소에 복사하지 않는다. `--law-chunks`, `--case-chunks`,
`--guide-chunks`, `--index`, `--model`로 별도 환경의 경로를 지정할 수 있다.

## 입력과 채점

- 한 줄에 JSON 객체 하나: `qid`(고유 문자열), `question`(빈 문자열 불가).
- 법령: `gold_articles` 문자열 배열. 공식 안내 정답은 별도로 제외 내역을 기록한다.
  빈 정답은 `answer_type=out_of_scope/unanswerable/adversarial`인 경우만 제외한다.
- 판례: `gold_case_ids` 비어 있지 않은 문자열 배열. 다른 필드명은 자동 추측하지 않는다.
- 정답 법령·판례가 코퍼스에 없거나 qid가 중복되면 실패한다. 정답 누락을 분모에서
  제외해 점수를 높이지 않는다. 이 점은 기존 `compare_law_top3`의 코퍼스 교집합
  채점보다 엄격하다.
- 모든 질문은 `search(q, k_law=5, k_case=5, k_guide=2)`로 호출한다.
  반환된 5청크 안에서 article_id/case_id를 순서대로 중복 제거한다.
- Hit@1/2/3/5, Recall@1/2/3/5, MRR을 계산한다. MRR은 반환된 상위 5청크에
  제한된 값이며, 법령 주 지표는 Hit@3·Recall@3, 판례 주 지표는 Hit@2다.
- 법령·판례 점수를 합산하지 않는다. 안내는 반환 건수만 기록하고 정확도를 만들지 않는다.

## 결과와 재현 한계

JSON에는 실행 시점·코드 커밋·Python 버전·모델명·검색 설정, 입력 파일 크기와 SHA-256,
실행 전 Chroma SQLite 해시, 질문별 정답·검색 ID·순위·지표·소요시간·안내 건수를 기록한다.
질문 원문과 근거 본문은 결과에 복제하지 않는다. 코드를 수정한 상태에서 실행한 결과의
커밋은 수정 전 HEAD이므로 공유용 측정은 구현 커밋 이후에 수행한다.

데이터셋 SHA-256은 입력 전체의 동일성 확인용이다. Chroma SQLite 해시만으로 벡터
전체의 동일성을 증명하지 않는다. 인덱스 폴더 전체를 같은 산출물로 준비해야 한다.
검색 지연은 세 자료 유형 검색을 합친 시간이고 모델 초기 로딩·LLM 생성 시간은 제외한다.
스크립트가 성공했다고 앱 기동·생성 품질까지 검증된 것은 아니다.

## 구현 검증 (2026-09-08)

구현 커밋 `fbc946c` 기준이다.

전체 테스트는 `557 passed, 1 failed, 2 skipped, 2 errors` 이다. 통과 수는 PATCH-002
기준선의 547 에서 이 패치가 추가한 테스트 10개만큼 늘었다. 실패 1건과 오류 2건은
PATCH-002 에서 이미 확인해 `PATCH-004` 로 등록한 기존 테스트 설정·호환성 문제이며,
이 패치로 새로 생긴 실패는 없다.

CLI 로 측정한 네 지표는 모두 PATCH-002 기준선과 일치한다.

| 평가 대상 | 문항 | 기준 | 기준선 | CLI |
| --- | ---: | ---: | ---: | ---: |
| 법령 Dev | 24 | Hit@3 | 100.0% | 100.0% |
| 법령 기존 Holdout | 18 | Hit@3 | 94.4% | 94.4% |
| 판례 Dev | 13 | Hit@2 | 92.3% | 92.3% |
| 판례 대체 Holdout | 8 | Hit@2 | 87.5% | 87.5% |

보조 지표도 같다 — 법령 Dev `Hit@1 79.2% / Recall@3 100% / MRR .8819`, 법령 Holdout
`MRR .8056`, 판례 Dev `MRR .9423`, 판례 대체 Holdout `Hit@3 100% / MRR .8542`.

법령 Dev 는 연속 3회 실행에서 모든 지표가 소수점까지 같았다. 실행 간 차이는 없다.

구현 도중 초안 상태에서 법령 Dev Hit@3 23/24 를 관측한 적이 있으나 구현 커밋
`fbc946c` 에서는 재현되지 않는다. 완성 전 코드에서 나온 값이므로 원인을 더 추적하지
않았다. 이후 성능 비교에서 같은 차이가 다시 보이면 그때 진단한다.

판례 Dev 입력은 3차 로컬 최종 보고서의 질문·정답 13행을 그대로 추출했고, 외부 표현
입력은 3차 로컬 사본을 사용했다. 두 입력 모두 커밋하지 않는다.

이 패치는 측정 코드 저장소화까지다. 검색 개선이나 앱·생성 품질 검증을 뜻하지 않는다.
