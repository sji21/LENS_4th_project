# PATCH-016 — 민법 후보 진입 분리 실험

## 판단

**별도 민법 검색은 신규 문항의 후보 누락을 해소할 가능성이 있지만, 상위 2개로 기존 라우팅을 교체하는 방식은 채택하지 않는다.** 신규 민법 기본 29문항에서 RRF 후보 TOP2가 필요한 민법 조문을 모두 포함했으나, 기존 DEV 문맥 입력의 민법 대상 10문항에서는 2/10이다. 기존 결과 보존 없이 교체하면 필요한 근거를 잃을 수 있다.

제품 소스·운영 자료·최종 검색 순위를 변경하지 않았다. 이번 결과는 후보 생성 실험이며 최종 검색 정확도 향상이 아니다. 후속은 기존 경로를 보존한 후보 보충과 최종 선택을 별도 검증하는 방향이다. 후보를 받았다는 이유로 민법을 무조건 먼저 반환하지 않는다.

## 실험 조건

PATCH-015 병합 main `2a8c7017acef645a28e7e94b1eda22dc0a0f9056`에서 실시했다. PATCH-015의 235개 입력 문자열과 평가 기준을 그대로 사용하고, 기존 민법 7개 인덱스를 복사했다. 기존 모델·청크·검색 설정·제품 코드 해시가 PATCH-015와 일치함을 확인했다.

기존 KURE-v1, BM25, 질의 확장, RRF 가중치와 상수를 유지했다. 실험용으로 `detect_civil_topics`에 의한 진입 제한을 거치지 않고 `svc._search_one(svc.civil, query, 7)`만 별도 호출했다. 원래 `service.search`의 반환 결과를 대체하거나 재정렬하지 않았다.

BM25·dense·RRF의 후보 전체 순위를 저장한 후 사전 지정한 k=1·2·3·5·7에서 비교했다. 민법 7개 전체를 반환하는 k=7은 대상 조문의 포함을 거의 자명하게 보장하므로 성능 향상의 증거로 쓰지 않는다. 질문에 법령 목표가 없어도 RRF가 7개를 반환한 점은 최종 선택 단계가 별도로 필요하다는 근거다.

## 민법 조문 후보 커버리지

이 표는 원래 필수 조문 중 **현재 민법 7개에 속하는 조문만 모두 포함했는지**를 센다. 다른 법령을 제외한 후보 지표이며 PATCH-015의 전체 필수 조문 AllRequired와 같지 않다. 예를 들어 CIV-DRAFT-028은 여기서 민법 제640조만 보며, 상가법 제2조·제10조의8까지 찾았다는 뜻이 아니다.

| 세트 / 방법 | n | 후보 TOP1 | TOP2 | TOP3 | TOP5 | TOP7 |
|---|---:|---:|---:|---:|---:|---:|
| 신규 민법 기본 / BM25 | 29 | 22 | 25 | 27 | 29 | 29 |
| 신규 민법 기본 / dense | 29 | 22 | 24 | 27 | 27 | 29 |
| 신규 민법 기본 / RRF | 29 | 23 | 29 | 29 | 29 | 29 |
| 기존 DEV 질문 단독 / BM25 | 10 | 2 | 4 | 7 | 8 | 8 |
| 기존 DEV 질문 단독 / dense | 10 | 4 | 4 | 6 | 6 | 10 |
| 기존 DEV 질문 단독 / RRF | 10 | 5 | 6 | 6 | 7 | 10 |
| 기존 DEV 문맥 포함 / BM25 | 10 | 2 | 4 | 6 | 9 | 10 |
| 기존 DEV 문맥 포함 / dense | 10 | 2 | 4 | 4 | 7 | 10 |
| 기존 DEV 문맥 포함 / RRF | 10 | 2 | 2 | 7 | 9 | 10 |

기존 최종 결과에서 같은 민법 부분 목표를 모두 반환한 수는 신규 기본 16/29, DEV 두 입력 각각 7/10이다. 이는 서로 역할·예산이 다른 최종 결과와 후보 목록의 참고 비교이며 직접적인 성능 개선율을 계산하지 않는다. 신규 29문항만 보고 TOP2를 선택하면 기존 DEV에서의 누락을 놓친다.

주차장 범위 보류 1문항은 각 방법 TOP1부터 1/1이지만 기본29에 합산하지 않는다. 신규 관찰 5문항, 기존 DEV 각 입력의 민법7 목표 없는 88문항, 과거 시점 별도 관찰 4입력은 후보 점수를 만들지 않는다. 목표 없음은 비관련 조문이라는 판정이나 민법 검색 금지가 아니다. 전체 235입력에 후보 검색을 수행했으며 고정 목표가 없는 입력을 누락하지 않았다.

추가 후보 검색 시간은 중앙값 약 0.266초, P95 약 0.447초다. 첫 질의 포함, CPU에서 별도 후보 호출만 잰 시간이며 앱 응답시간이나 운영 추가 지연을 확정한 수치가 아니다.

## 자료·검증·한계

- [실험 조건](../data/eval/patch016-candidates/capture/manifest.json), [235입력 후보 순위](../data/eval/patch016-candidates/capture/results.json), [전후 감사](../data/eval/patch016-candidates/capture/audit.json)
- [집계](../data/eval/patch016-candidates/report/summary.json), [문항별 결과](../data/eval/patch016-candidates/report/details.json), [공유 해시](../data/eval/patch016-candidates/bundle-manifest.json)

원문 질문은 PATCH-015의 저장 입력을 참조하고 새 파일에는 ID·입력 해시만 기록한다. 코드·모델·운영 파일은 PATCH-015 manifest로 연결한다. 실행기는 미커밋 상태에서 사용돼 실제 파일 해시와 dirty 상태를 남겼다. 운영 파일 전후 해시와 평가 기준 불변, 민법 인덱스 7개 본문·메타데이터 일치를 확인했다.

관련 테스트 8개 통과. `DJANGO_SETTINGS_MODULE` 미인식 환경 경고 1개가 있으며 본 테스트는 Django를 사용하지 않는다. 초기 설정 대조는 JSON 목록/런타임 튜플의 표현 차이 때문에 검색 전에 중단됐고, 비교 표현을 통일한 새 실행만 결과에 사용했다. 전체 앱·LLM·OCR·법적 답변·판례 정확도는 평가하지 않았다.

후보 자체의 유용성은 확인했으나 민법 최종 노출 조건, 기존 근거 보존, 민법 7개 외 추가 조문의 일반화는 검증하지 않았다. 따라서 운영 변경이나 신규 라우팅 규칙으로 승격하지 않는다. PATCH-012의 규칙이나 BGE/Qwen 실험을 가져오지 않았다.

## 재현

프로젝트 루트에서 PATCH-015와 해시가 같은 운영 자료·모델 캐시가 필요하다. 다른 자료로 진행하려면 별도의 새 기준선부터 마련한다. 출력은 새 tmp 하위 디렉터리로 제한된다.

```powershell
.venv/Scripts/python -m scripts.patch016_candidates --out tmp/patch016-new-run
.venv/Scripts/python -m scripts.patch016_candidates --replay tmp/patch016-new-run --out tmp/patch016-new-report
.venv/Scripts/python -m pytest tests/test_patch016_candidates.py tests/test_patch015_baseline.py -q
```

공유 결과만 재집계할 때는 모델이나 운영 DB 없이 아래 명령을 사용한다. 결과 파일 무결성은 공유 해시 목록과 대조한다.

```powershell
.venv/Scripts/python -m scripts.patch016_candidates --replay data/eval/patch016-candidates/capture --out tmp/patch016-replay
```
