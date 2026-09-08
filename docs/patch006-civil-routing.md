# PATCH-006 민법 임대차 조건부 검색

## 출처와 변경

3차 [`feat/patch-033-minbeop-corpus-review`](https://github.com/sji21/3rd_project_team4/tree/d76bdc0)의
수집기·민법 전용 BM25·질문 라우팅·회귀 테스트를 현재 main에 맞춰 이식했다.
3차 문서·평가 결과 전체나 다른 기능 커밋은 가져오지 않았다.

3차 후보 제623·626·627·629·634·640조에 제632조를 추가해 7개를 수집한다.
제629조의 전대 원칙만으로 방 일부 사용을 일률적으로 금지하지 않도록 전대 질문에는
제632조의 소부분 사용 예외도 함께 반환한다. 상가 연체 해지 질문에는 민법 제640조를
앞에 넣지 않고 기존 상가 법령 검색에 맡긴다. `계약하자고`의 `하자`가 수선으로
오발동하는 사례도 차단했다. 모든 표현·특별법 예외를 해결하는 규칙은 아니다.

공식 수집 대상은 [국가법령정보센터 민법 고정 판본](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=284415&efYd=20260317&efYn=Y&chrClsCd=010202&nwJoYnInfo=Y&ancYnChk=0&netPrivateYn=N)이다.
2026-03-17 시행·법률 제21454호의 7개 본문을 수집해 확인했다. 시행일이 다르거나
필수 조문을 파싱하지 못하면 실패한다. 평문 원본도 `data/raw/law/민법-20260317.txt`에 보존한다.

## 반환 동작

- 민법을 일반 법령 BM25에서 분리해 기존 문서 빈도 계산을 보존한다.
- 민법 벡터도 별도 Chroma로 분리한다. 동일 컬렉션에 추가한 실험에서는 판례 순위가 바뀌어 채택하지 않았다. 기본 인덱스에 민법이 섞여 있으면 서비스 초기화를 실패시켜 잘못된 적재를 알린다.
- 주제가 감지되면 민법을 최대 2건 앞에 넣고 나머지 자리를 기존 법령으로 채운다.
  `k_law` 전체 상한을 지키며 생성 측의 TOP3 계약은 유지한다.
- 주제별 조문 하나로 필터링하므로 이는 자유 검색 성능보다 규칙에 따른 근거 선택이다.
  규칙이 오발동하면 TOP3를 밀어낼 수 있다. 유사도 점수를 관련성 보증으로 해석하지 않는다.
- 판례·안내의 검색 설정과 분리는 유지한다. 결과에 `civil_topics`를 추가한다.
- 청크에 민법이 없으면 경고하고 기존 법령 검색을 계속한다. 코드 병합만으로 데이터가 생기지 않는다.
- PATCH-003 기준선 JSON에도 실제 민법 검색기 설정을 기록하도록 확장했다.

## 데이터 준비와 복구

Python 3.11 가상환경, 기본 법령·판례·안내 청크와 짝이 맞는 SQLite·Chroma를 먼저 준비한다.
앱을 중지한 상태에서 SQLite 파일, 법령 청크, **Chroma 폴더 전체**를 별도 보관한 다음 실행한다.
이번 검증에서는 `tmp/patch006/`에 복사본을 만들어 원래 운영 산출물을 보존했다.

```powershell
.venv/Scripts/python -m src.ingestion.fetch_minbeop --records data/parsed/minbeop_records.jsonl
.venv/Scripts/python -m src.ingestion.load_laws --records data/parsed/minbeop_records.jsonl --database data/database/civil.sqlite3 --export data/chunks/civil.jsonl
.venv/Scripts/python -m src.retrieval.index --chunks data/chunks/civil.jsonl --path data/index/chroma_civil_kurev1_1024
.venv/Scripts/python -m src.ingestion.load_laws --records data/parsed/minbeop_records.jsonl --database data/database/knowledge.sqlite3 --export data/chunks/chunks.jsonl
```

법령은 133→140청크, 기본 인덱스 165건 + 민법 전용 인덱스 7건이 된다(현재 기준선 기준).
빈 SQLite에서 민법만 적재하면 기존 법령이 없으므로 위 숫자가 되지 않는다.
기존 기본 인덱스는 재색인하지 않는다. 민법 포함 140청크를 기본 인덱스에 넣거나
민법 7건을 기본 인덱스 경로에 넣으면 안 된다. 후자는 같은 law 유형의 기존 법령을
제거할 수 있다. `--prune-all`은 사용하지 않는다. 이후 기본 법령을 다시 색인할 때도
민법을 제외한 입력을 사용한다. 민법 전용 DB는 7개 청크 추출용이며 앱은 통합 SQLite를 쓴다.
복구는 앱 중지 후 세 산출물을 같은 시점의 백업으로 함께 되돌리고 앱을 재시작한다.
자료는 기존 `.gitignore` 대상이며 PR에는 원본 DB·벡터를 넣지 않는다.

## 재현 평가

`scripts.evaluate_civil_regression`은 준비된 추가 전·후 청크와 실제 Chroma를 비교한다.
모델은 동일 KURE-v1을 공유하고 모든 인덱스의 ID·본문·메타데이터를 먼저 검증한다.
기존 Dev·Holdout은 각각 점수와 법령 TOP3·판례 TOP5·안내 TOP2의 변화를 기록한다.
판례 자체 정확도 평가가 아니라 같은 질문에 대한 순위 회귀 검사라는 점을 구분한다.

```powershell
.venv/Scripts/python -m scripts.evaluate_civil_regression --before-chunks tmp/patch006/before/chunks.jsonl --before-index data/index/chroma_kurev1_1024 --after-chunks tmp/patch006/chunks.jsonl --after-index data/index/chroma_kurev1_1024 --civil-index tmp/patch006/civil-index --out tmp/patch006/regression-separated.json
```

위 경로는 이번 검증 배치다. 새 환경에서는 준비한 같은 시점의 사본 경로로 바꾼다.
결과 경로가 이미 있으면 새 이름을 사용한다. 기존 파일은 덮어쓰지 않는다.

민법 평가 입력 `data/eval/minbeop_review_holdout_20260901.jsonl`은 3차 공개 파일을
그대로 이관했다. 15개 정답 문항을 채점하고 빈 정답 5개는 민법 노출 여부를 따로 기록한다.
3차의 `abstain`은 메모리에서만 `unanswerable`로 대응하고 입력 바이트는 바꾸지 않는다.
`qualified` 문항의 조건부 답변 품질은 검색 지표로 평가하지 않는다.
파일명에 holdout이 있어도 이미 개발에 사용한 **공개 회귀셋**이며 독립 평가가 아니다.
추가한 제632조는 별도 단위 회귀 테스트로 확인하며 기존 골드를 바꾸어 점수를 올리지 않는다.

## 2026-09-08 검증 결과

| 검사 | 결과 |
| --- | --- |
| 기존 법령 Dev 24문항 | 추가 전·후 Hit@3 100%, Recall@3 100%, MRR 0.881944 |
| 기존 법령 Holdout 18문항 | 추가 전·후 Hit@3 94.444%, Recall@3 82.407%, MRR 0.805556 |
| Dev 27 + Holdout 20문항 순위 비교 | 법령 TOP3·판례 TOP5·안내 TOP2 변경 0건 |
| 3차 공개 민법 혼합셋 15문항 | Hit@3 80%, Recall@3(문항 평균) 72.222%, MRR 0.738889 |
| 빈 정답 5문항 | 민법 노출 0건 |
| Windows 전체 테스트 | 590 passed, 3 skipped, 124 subtests passed |
| PATCH-003 CLI / 서비스 from_index | 분리 인덱스 172건 ID·본문·메타데이터 검증, 법령 Dev Hit@3 100% |

전체 테스트 명령은 `.venv/Scripts/python -m pytest -q`다. 스킵 3건은 비활성
LangSmith 설정·연결 검사 2건과 비공개 PDF 통합 검사 1건이다.
전체 법령 140청크 SHA-256은 `8fe15b626d1a49e5dbf4041bbe8d1aa414f520867c5f0bddf661555ee9e8b310`,
민법 7청크 SHA-256은 `dc9b27ca13943fe47c00521a96890acc464b622bad464598a9e0278c6fad1cff`다.

공개 민법 혼합셋에는 기존 주택임대차 질문도 포함된다. 15개 모두가 민법 직접 질문이라는
뜻이 아니다. Recall은 문항별 비율의 평균이며, 3차 문서의 전체 정답 조문을 합친 Recall과
분모가 다르므로 숫자 차이를 성능 향상으로 해석하지 않는다.

## 적용 한계

독립 평가와 운영 산출물 반영은 아직 별도 확인 대상이다. 이 패치의 공개 회귀 결과만으로
운영 품질·일반화 성능이 검증됐다고 선언하지 않는다. 수선 책임의 귀책사유, 필요비·유익비,
전대 예외 해당 여부 등 생성 답변의 조건 설명은 별도 검토가 필요하다.
민법 원문 전체·판례 추가·리랭커·새 Holdout 봉인 해제는 이번 범위에 포함하지 않는다.
