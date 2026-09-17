# PATCH-053 — 복합 질문의 절차·권리 근거 검색

## 범위와 기준선

기준 main은 `4491b76`이다. 일반 법령3/5, 민법 최대3, 판례5, 안내 최대2의 검색 회귀 계약을 유지한다. 생성 모델·프롬프트·법률 검증·서비스 반환 예산·원천·DB·인덱스는 변경하지 않는다.

등록민간임대 신고·재계약 질문에서 신고·서식 근거만 남고 재계약 근거가 빠지는 문제와, 전입 절차 질문에서 주민등록 신고 근거가 뒤로 밀리는 문제를 대상으로 한다. 신규 일반 법령 후보 선택은 기존 세금 조회 및 대항력·우선변제 선택 뒤에 적용한다. 민법 정책 변경은 이 패치에 섞지 않는다.

2026-09-17 최신 main에서 실제 KURE 검색313입력을 일반 K3/K5로 각각 호출했다. 코드·자료·모델·질문·정답·논리 인덱스·설정의 실행 전후 동일성을 확인했다. 실제 KURE957회이며 생성 LLM 호출은 없다. 기존 PATCH-051 저장 산출물과313입력의 네 채널 반환 근거·순위·본문 해시가 모두 같았다.

기준선: `data/eval/patch053-retrieval/before/`, 지표: `baseline-summary.json`.

| 평가군 | 채점 분모 | K3 완전 확보 | K5 완전 확보 |
| --- | ---: | ---: | ---: |
| DEV100 질문 단독 | 75 | 40 | 47 |
| DEV100 제공 문맥 | 75 | 39 | 48 |
| 민법35 필수 | 29 | 28 | 28 |
| 기존 법령 DEV | 24 | 23 | 24 |
| 공개 법령 HOLDOUT | 18 | 12 | 13 |
| 공개 민법 회귀 | 15 | 9 | 12 |

민법35는 별도 민법 채널 중심, 공개 민법 회귀는 원래의 combined 목표·범위를 따른다. 판례는 별도 case5 기준 DEV5/13, 외부표현4/8이다. 기존 공개 HOLDOUT은 튜닝에 노출된 회귀 자료이며 독립 평가 성적이 아니다.

## 후보 생성과 선택

BM25·KURE 각20개 결과를 그대로 사용한다. 기존 최종 반환을 위해 잘랐던 결합 목록 대신 요청별 구성원 결과의 전체 합집합을 같은 RRF 가중치와 동점 규칙으로 재구성한다. 멤버 검색 깊이, 질의 확장, 임베딩 호출을 늘리지 않는다. 공유 검색기의 상태를 요청마다 바꾸지 않는다.

탐색 시 실제 구성원 후보 추적에서 DEV-096 제45조는 질문 단독의 전체 결합 순위21위, 문맥 포함18위였다. 원문 미보유가 아니라 순위·최종 선택 누락이었다. 해당 후보 기록은 `dev096-candidate-trace.json`에 보존하며, 최종 성능은 별도의 고정 전후 전체 실측으로 판정했다.

현재 질문의 명시적인 민간임대 재계약·신고·서식 및 전입 절차 의도를 구분하고, 검색 후보의 현행 조문 본문에서 해당 내용을 직접 확인한 근거를 선택한다. 문항 ID·질문 전문·정답 조문 번호·인접 조문 번호를 선택 조건으로 쓰지 않는다. 요청하지 않은 의도와 과거 대화·인용·부정 표현은 보수적으로 제외한다.

제품 변경은 `src/retrieval/retrieval_intent.py`, `src/retrieval/expanded.py`에 한정한다. `intent_selection_config`로 선택 정책을 평가 스냅샷에 남긴다.

## 검증과 결과

개발 테스트10개와 독립 반례44개를 추가했다. 현재 질문·추가 상황과 과거 대화, 인용, 부정, 일반/공공/상가 범위, 완료된 전입신고, 배경으로만 언급한 갱신을 구분한다. 후보 외 근거 삽입 금지, 현행 판본·본문·필터 일치, 검색기별 깊이·호출 수·RRF 가중치와 작은 반환 예산도 검증한다.

공식 스냅샷과 해시 검증된 청크에서 실제 BM25 검색 → 제품 서비스 → 생성 graph → native HTTP 요청 직렬화까지 실행해 민간임대45·46·47의 본문 전체가 전달됨을 확인했다. HTTP 응답만 모의값을 썼다. 별도 합성 후보 검사에서는 서비스의 일반3·민법3·판례2·안내2 경계를 확인했다. 검색 회귀의 판례5 계약과 서비스 판례2 계약을 혼동하지 않는다.

실제313입력×K3/K5 각각의 전후 검색을 비교했다. 8입력·16개의 입력/예산 조합에서 일반 법령 결과만 바뀌었으며 기존 필수 근거 손실과 완전 확보→불완전 전환은 모두0이다. 민법·판례·안내 채널 전체와 공개78회귀는 근거·순위·본문 해시가 동일하다. KURE957회, 일반 backend1303회, 민법 backend770회로 호출 수도 같다.

| DEV100 채점 대상 | 일반 법령 예산 | 필수 근거 완전 확보 전→후 | 개별 목표 확보 전→후 |
| --- | ---: | ---: | ---: |
| 질문 단독75입력 | 3 | 40→43/75 (53.3→57.3%) | 74→79/121 |
| 질문 단독75입력 | 5 | 47→51/75 (62.7→68.0%) | 87→92/121 |
| 제공 문맥75입력 | 3 | 39→41/75 (52.0→54.7%) | 73→76/121 |
| 제공 문맥75입력 | 5 | 48→50/75 (64.0→66.7%) | 88→91/121 |

민법 최대3은 각 행에서 별도 반환한다. 완전 확보는 원래 정답의 모든 필수 근거가 일반·민법의 해당 채널에 포함된 경우다. 개별 목표 분모121은 문항별 목표를 합한 수이며 서로 다른 조문121개라는 뜻이 아니다. 채널별 Hit@1·3·5와 분모는 `comparison.json`의 `channel_hit_metrics`, 전체 목표 단위 확보는 `details.json`에 있다.

| 질문 ID·모드 | 새로 확보한 필수 근거 | K3 완전 확보 전→후 | K5 완전 확보 전→후 | 기존 필수 근거 손실 |
| --- | --- | --- | --- | ---: |
| DEV-005 질문 | 주민등록법16 | 미확보→미확보 | 미확보→확보 | 0 |
| DEV-005 문맥 | 주민등록법16 | 미확보→미확보 | 미확보→미확보 | 0 |
| DEV-033 질문 | 주민등록법16 | 미확보→확보 | 미확보→확보 | 0 |
| DEV-038 질문 | 주민등록법16 | 미확보→미확보 | 미확보→미확보 | 0 |
| DEV-046 질문·문맥 각각 | 주민등록법16 | 미확보→확보 | 미확보→확보 | 0 |
| DEV-096 질문·문맥 각각 | 민간임대45 | 미확보→확보 | 미확보→확보 | 0 |

DEV-096의 필수 근거는 민간임대45·46·47이다. 두 모드 모두 K3는 **거래신고6의5·민간임대46·47 → 민간임대46·47·45**로 바뀐다. K5에서도45를 확보한다. 질문별 전체 전후 순위·필수 근거·본문 해시·반환 수는 `before/rows.json`, `after/rows.json`, `comparison.json`에 남겼다. 전체 검색 근거 본문 문자 수는 질문 모드 K3에서2,574→2,383, K5에서4,939→3,582였다. 이는 판례5를 포함한 검색 근거 합계이며 실제 프롬프트 토큰이나 생성 품질 점수가 아니다.

첫 후보 평가 시 검색은 끝났지만 worktree의 Git 소유권 검사로 메타데이터 저장이 실패했다. 전역 설정을 바꾸지 않고 재실행 프로세스에만 `safe.directory`를 지정해 새 결과를 수집했다. 최종 수집의 소스·자료·모델·설정은 실행 전후 동일하다.

평가 모듈을 불러오지 않는 독립 표준 라이브러리 감사로313행·626쌍과 반환 근거15,164개를 재집계했다. manifest, 문항·정답, 소스·모델·설정, 원문 해시·출처·현행 상태·채널·순위·반환 한도와 본문 문자 수가 모두 일치했다. 결과는 `independent-verification.json`, 재현 코드는 `audit-replay.py`다.

최종 전체 검사: `python -X utf8 -m pytest -q` → **2,726 passed, 3 skipped, 678 subtests passed** (443.68초). 생략은 LangSmith 미설정2건과 로컬 등기 PDF 미지정1건이다. 출력은 `full-tests.log`, 최종 소스·시험·산출물 해시는 `validation.json`에 보존한다. 사용자 승인 후 구현 커밋 `93a49e5`를 만들었으며 메인 작업 트리는 변경하지 않았다.

## 620e1c6 리뷰 후 보완 (수정 커밋 67382c2)

공개313입력의 기존 근거 손실0은 모든 문형에서 손실이 없다는 보장이 아니다. 병합 전 리뷰에서 다음 두 오류가 확인돼 수정했다.

- 배경으로 말한 “재계약하면서”가 같은 질문의 신고·서류 요청과 결합하면서 재계약 의도로 승격됐다. 임대료·변경 신고·서류 질문에서 기존 임대료44조가45조로 교체됐다.
- 다음 주제 단어에서 부정 범위를 잘라 “계약 신고와 재계약 서류는 모두 제외”의 앞쪽 주제를 포함시켰다. 별도 문장의 완료된 주민등록을 긍정 요청으로 읽고, 앞 문장의 제외된 전입신고 방법까지 다시 활성화하는 문제도 있었다.

제보 문항, 나열·부정·재긍정·완료 표현과 정상 복합 요청을 공개 개발 회귀로 추가했다. 변경 전 재현 기록과 수정 후 실측은 `data/eval/patch053-review/`에 별도로 보관한다. 기존 `patch053-retrieval` 자료는 수정 전 평가 시점의 기록으로 유지한다.

재계약 의도는 해당 언급에 이어진 요청 표현에 연결한다. 다른 신고 주제나 임대료 인상 설명에서 가져온 요청 단어는 재계약 요청으로 사용하지 않는다. 함께 나열한 주제에는 공동 부정을 적용하되 독립적인 요청과 다른 문장의 부정을 합치지 않는다. 전입 절차의 요청·부정·완료 판정도 같은 절에서 확인한다.

공식 청크와 실제 BM25로 제보 문항을 비교한 결과(`reported-cases.json`):

| 제보 | 수정 전 의도 | 수정 후 의도 | 일반K3 변화 |
| --- | --- | --- | --- |
| 재계약하면서 올린 임대료·변경 신고 서류 | 재계약·신고·서식 | 신고·서식 | 민간임대46·47·45 → 44·46·47 |
| 계약 신고와 재계약 서류 모두 제외 | 재계약·신고 | 없음 | 강제로 넣었던45를 제외하고 원래 BM25 상위3건 복원 |
| 전입신고 방법 제외·주민등록 완료 | 전입 절차 | 없음 | 강제로 넣었던 주민등록16을 제외하고 원래 BM25 상위3건 복원 |

여기서 “의도 없음”은 새 선택기를 적용하지 않는다는 뜻이다. 원래 검색 근거가 질문의 모든 요구를 충족한다는 판정은 아니다. 이 세 문항은 BM25 재현이며, 기존313입력의 KURE 하이브리드 회귀와 구분한다. 고정313입력의 수정 전후 의도 판정은 전부 동일했다(`gate-comparison.json`).

수정 소스를 고정한 새 실제 KURE313입력×K3/K5에서 네 채널의 근거·순위·본문 해시는 이전 PATCH-053과 모두 동일했다. KURE957회·일반backend1303회·민법backend770회도 동일하다. `comparison.json`은 직전 PATCH-053 대비 변경0·손실0, `comparison-vs-main.json`은 기준 main 대비 기존16쌍의 개선·손실0을 보존한다. DEV-096의45·46·47 동시 확보와 질문43/75·51/75, 문맥41/75·50/75의 완전 확보 결과를 유지한다.

독립 감사는 이전 PATCH-053 대비626비교·반환 근거15,164개의 무결성과 main 대비 개선16쌍·손실0을 재집계했다. 제보3건은 별도 실제 BM25로 재실행해 수정 후 의도와 원래K3 보존을 확인했다. 최종 전체 검사: **2,757 passed, 3 skipped, 678 subtests passed** (387.42초). 생략 사유는 이전과 같은 LangSmith2건·등기 PDF1건이다. 리뷰에서 추가한31개 검사를 포함하며 실제 생성 답변 품질은 평가하지 않았다. 결과는 리뷰 디렉터리의 `independent-verification.json`, `full-tests.log`, `validation.json`에 남긴다.

## 재현

저장소 루트와 준비된 가상환경에서 실행한다. `--data`는 동일한 재구축 DB·인덱스 경로이며 출력은 항상 새 디렉터리를 사용한다. 사례 파일은 기존 고정78회귀와 같은 해시여야 한다. 아래 초기 수집·소스 감사 명령은 구현 커밋 `93a49e5`의 checkout 기준이다. P2 수정 후 소스 감사에는 그 아래 리뷰 경로를 사용한다.

```powershell
python -X utf8 -m scripts.patch051_companion_eval capture --data C:/team_project/patch041-worktree/data --out data/eval/patch053-retrieval/after --case-dev C:/team_project/4th_project/tmp/patch003-case-dev-input.jsonl --case-external C:/team_project/3rd_project/3rd_project_team4/data/eval/case26_external_8.jsonl
python -X utf8 -m scripts.patch053_retrieval_report --before data/eval/patch053-retrieval/before --after data/eval/patch053-retrieval/after --out data/eval/patch053-retrieval/comparison.json
python -X utf8 data/eval/patch053-retrieval/audit-replay.py --before data/eval/patch053-retrieval/before --after data/eval/patch053-retrieval/after --report data/eval/patch053-retrieval/comparison.json --out tmp/patch053-independent-replay.json --source-root . --corpus C:/team_project/patch041-worktree/data/chunks/chunks.jsonl C:/team_project/patch041-worktree/data/chunks/civil.jsonl C:/team_project/patch041-worktree/data/chunks/cases.jsonl C:/team_project/patch041-worktree/data/chunks/guides.jsonl
```

기존 paired 평가기를 재사용하되 새 의도 선택 설정을 스냅샷에 추가했다. 비교 보고서는 원래 필수 근거 판정을 유지하며 채널별 Hit@K, 완전→불완전 및 개별 목표 손실, 소스 차이를 함께 기록한다.

P2 수정 후 저장된 결과의 재집계·소스 감사(모델 호출 없음):

```powershell
python -X utf8 -m scripts.patch053_retrieval_report --before data/eval/patch053-retrieval/after --after data/eval/patch053-review/after --out tmp/patch053-review-comparison-replay.json
python -X utf8 data/eval/patch053-retrieval/audit-replay.py --before data/eval/patch053-retrieval/after --after data/eval/patch053-review/after --report data/eval/patch053-review/comparison.json --out tmp/patch053-review-independent-replay.json --source-root . --corpus C:/team_project/patch041-worktree/data/chunks/chunks.jsonl C:/team_project/patch041-worktree/data/chunks/civil.jsonl C:/team_project/patch041-worktree/data/chunks/cases.jsonl C:/team_project/patch041-worktree/data/chunks/guides.jsonl
```

## 경계와 한계

- 필수 일반 법령이4~5개인 DEV-003·025·026·028·038은 일반3개 상한에서 모두 확보할 수 없다. 상한·정답을 바꿔 개선으로 처리하지 않는다.
- LLM_TEST의39/75와 검색 기준선40/75 차이는 DEV-022의 검색 전 범위 거절이다. 이 패치의 검색 순위 문제로 취급하지 않는다.
- 모델 요청 전달 검사는 가짜 응답으로 경계를 검증한다. 실제 LLM 답변 정확도·답변율·보류율·비용은 별도다.
- 이 평가 수집기는 문항별 검색 시간과 실제 모델 토큰 수를 기록하지 않는다. 호출 수 동일성을 지연 동일성으로 표현하지 않는다.
- 새 미공개 HOLDOUT 평가를 수행하지 않았다. 새 반례도 개발·리뷰에 공개된 후에는 개발 회귀로 취급한다.
- 기존 세금 조회·대항력/우선변제 선택이 먼저 적용되는 혼합 질문에서는 신규 의도 선택을 중첩하지 않는다. 전입 절차와 보호 효과를 함께 물으면 신규 전입 절차 보강이 적용되지 않을 수 있다.
- 기존 BM25·KURE 각20개 후보의 합집합 밖에 있는 근거는 새로 찾지 않는다. 주민등록법16조의 일부 누락은 이 조건 때문에 남을 수 있다.
- DEV-033 문맥, DEV-038 문맥, DEV-041 질문·문맥의 주민등록법16 누락은 남아 있다. 신규 표적의 일부 근거 확보를 해당 질문 전체 해결로 계산하지 않는다.
- 2026-09-17 사용자 승인으로 구현·기록 커밋과 패치 브랜치 푸시를 진행한다. PR 생성·병합은 별도 요청 대상이다.
