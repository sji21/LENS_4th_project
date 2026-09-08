# PATCH-002 4차 환경 기준선 실행·검색 평가

- 측정일: 2026-09-07
- 담당자: sji21
- 브랜치: `test/patch-002-baseline-run`
- 코드 기준: 4차 `main` `4bd7006` (3차 `8c7d781cd34a9e305df4472048ef0f499f1b37ee` 이관본)

## 1. 목적과 범위

기능 개발에 들어가기 전에, 3차에서 이관한 코드베이스가 4차 환경에서 그대로 동작하고
3차가 기록한 검색 성능을 재현하는지 확인한다.

이 패치는 **실행·측정·기록만 한다.** 코드는 수정하지 않았다. 측정 중 발견한 코드
오류는 이 패치에서 고치지 않고 후속 패치로 등록한다.

## 2. 동일 조건 근거

3차와 같은 코퍼스·모델·검색 설정·평가셋을 사용했다. 코퍼스는 재생성하지 않고 3차
로컬 산출물을 그대로 가져와, 재생성 과정에서 생길 수 있는 차이를 배제했다.

| 항목 | 3차 기록 | 4차 확인 |
| --- | ---: | ---: |
| 법령 청크 | 133 | 133 |
| 판례 청크 | 26 | 26 |
| 기관 안내 청크 | 6 | 6 |
| Chroma 인덱스 벡터 | 165 | 165 |

- 임베딩 모델: `nlpai-lab/KURE-v1` (1024차원)
- 인덱스: `data/index/chroma_kurev1_1024`
- 검색 파라미터: `bm25_k1=1.5`, `bm25_b=0.75`, `char_ngram=2`, `rrf_k`·질의 확장은 서비스 기본값
- 평가셋: `data/eval/dev.jsonl`, `data/eval/holdout.jsonl`, 3차 `dev_mapped_13`, 3차 `case26_external_8`

## 3. 실행 환경

| 항목 | 값 |
| --- | --- |
| OS | Windows-10-10.0.26200-SP0 |
| Python | 3.11.9 (`.venv`) |
| torch / chromadb / sentence-transformers | 2.14.0 / 1.5.9 / 6.0.1 |
| streamlit / langchain / langgraph / pytest | 1.63.0 / 1.4.0 / 1.2.11 / 9.1.1 |
| LLM | Ollama 0.33.2, `qwen3:8b-q4_K_M` |
| OCR | Tesseract 5.4.0 (`eng`, `kor`, `osd`) |
| 환경변수 | `.env.example` 기본값 그대로 (Local Ollama, API 키 없음) |

## 4. 앱 기동

`streamlit run app/streamlit_app.py` 로 기동한다.

- `/_stcore/health` → `HTTP 200 ok`
- 루트 페이지 → `HTTP 200`, 7,459 bytes
- 기동 로그에 예외·오류 0건

## 5. 전체 테스트

```
pytest -q
547 passed, 1 failed, 2 skipped, 2 errors   (종료 코드 1)
```

실패·오류 3건은 모두 **환경 문제**이며 기존 코드 오류는 확인되지 않았다.

| 항목 | 분류 | 내용 |
| --- | --- | --- |
| `tests/test_langsmith_connection.py::test_langsmith_environment_is_configured` | 환경 설정 | `LANGSMITH_TRACING` 이 `true` 이기를 단언하지만 `.env.example` 기본값은 `false`. 3차도 PATCH-037 기록에서 같은 사유로 실패 1건을 남겼다 |
| `tests/test_pdf_extraction.py::test_validate_pdf_rejects_invalid_uploads` (오류 2건) | 플랫폼 제약 | `ValueError: the environment variable is longer than 32767 characters`. 긴 파라미터화 테스트 이름이 Windows 환경변수 길이 한도를 넘는 문제로, 3차가 PATCH-020·032·037에서 반복 기록한 사전 존재 오류 |
| 스킵 2건 | 선택 조건 | `LANGSMITH_TRACING` 비활성 1건, `REGISTRY_SAMPLE_PDF`(로컬 비공개 PDF) 미설정으로 OCR 통합 테스트 1건 |

## 6. 검색 평가

### 6.1 법령

`python -m src.evaluation.compare_law_top3` 로 재현한다. 운영 설정은
`rrf_k=5, law context expansion` 이다.

| 평가 대상 | 문항 | 기준 | 3차 | 4차 |
| --- | ---: | ---: | ---: | ---: |
| 법령 Dev | 24 | Hit@3 | 100.0% | 100.0% |
| 법령 기존 Holdout | 18 | Hit@3 | 94.4% (17/18) | 94.4% |

재실행이 덮어쓴 `data/eval/runs/law-top3-comparison.json` 은 3차 커밋본과 끝 줄바꿈 하나를
제외하고 바이트 단위로 같았다. 산출물은 3차 기록을 보존하기 위해 되돌렸다.

보조 지표도 3차 `final-main-law-20260901.json` 과 같다 — Dev `Hit@1 79.2%`,
`Recall@3 100%`, 문맥 확장 규칙 발동 42문항 중 `dev-001` 1건.

### 6.2 판례

운영 설정 `hybrid_service_rrf` 기준이다.

| 평가 대상 | 문항 | 기준 | 3차 | 4차 |
| --- | ---: | ---: | ---: | ---: |
| 판례 Dev (`dev_mapped_13`) | 13 | Hit@2 | 92.3% (12/13) | 92.3% |
| 판례 대체 Holdout (`external_wording_8`) | 8 | Hit@2 | 87.5% (7/8) | 87.5% |

보조 지표까지 소수점이 일치한다 — Dev 13 `Hit@1 .9231 / Hit@5 1.0000 / MRR .9423`,
대체 Holdout 8 `Hit@1 .7500 / Hit@3 1.0000 / MRR .8542`.

### 6.3 기관 안내

기관 안내는 **별도 정확도 수치를 산출하지 않았다.** 3차가 "독립 평가셋이 충분하지
않아 별도 정확도를 주장하지 않음" 으로 정리한 기준을 그대로 따른다. 이번 측정에서는
코퍼스 구성(6청크)이 3차와 같다는 것만 확인했다.

법령과 판례는 정답 단위와 운영 반환 건수가 달라 하나의 전체 정확도로 합치지 않는다.

## 7. 평가 목적 구분

여기서 실행한 Holdout 측정은 **재현·회귀 확인용**이다. 새 독립 평가가 아니다.

- 법령 기존 Holdout 18문항은 3차에서 이미 공개·측정된 평가셋이다. 봉인 상태가 아니므로
  이 수치로 일반화 성능을 주장하지 않는다. 새 독립 평가는 `LIST.md` 「검색 파트 후속
  과제」의 법령 Holdout-v2 로 남아 있다.
- 판례 `dev_mapped_13` 은 3차 기록에 "판례 요약문이 dev 질문을 참고했으므로 독립 평가가
  아님" 으로 명시되어 있다.
- 판례 `external_wording_8` 은 설정 선택·튜닝에 쓰지 않고 회귀 확인에만 쓴다.

## 8. 재현 방법

```bash
python -m venv .venv                      # Python 3.11
.venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env
pytest -q
python -m src.evaluation.compare_law_top3
streamlit run app/streamlit_app.py
```

법령 Holdout 18문항과 판례 두 평가셋에는 저장소에 커밋된 실행 스크립트가 없다. 3차도
`docs/eval-holdout.md` 에서 같은 한계를 기록했다. 이번에는 3차 기록과 동일한 호출
(`RetrievalService.from_index().search(q, k_law=5, k_case=5, k_guide=2)`) 과 동일한 지표
정의로 임시 스크립트를 작성해 측정했고, 코드 무수정 원칙에 따라 저장소에 넣지 않았다.
스크립트 저장소화는 후속 과제로 등록한다.

## 9. 한계

- 판례 `dev_mapped_13` 질문·정답과 `case26_external_8.jsonl` 은 3차 저장소에 커밋되지
  않은 파일이다. 이번 측정에는 3차 로컬 사본을 사용했고 4차 저장소에 넣지 않았다.
  Holdout 봉인 상태를 유지하기 위한 선택이며, 4차에서 재측정하려면 같은 로컬 사본이
  필요하다.
- OCR 통합 테스트는 로컬 비공개 PDF가 없어 실행하지 못했다. Tesseract 설치·한국어
  데이터는 확인했으므로 샘플만 확보되면 실행 가능하다.
- 생성(LLM) 품질은 이번 범위가 아니다. Ollama 응답 여부와 앱 기동만 확인했다.
- 3차 `data/eval/runs/holdout-2026-08-29.json` 의 dev 25문항 수치는 코드 커밋
  `7207692` 시점 기록이라 최종 `main` 과 다르다. 4차 비교 기준으로 쓰지 않는다.

## 10. 결론

4차 환경에서 이관 코드베이스가 정상 기동하고, 3차가 기록한 검색 성능 4개 수치를 모두
재현했다. 기존 코드 오류는 확인되지 않았으므로 별도 수정 패치를 등록하지 않는다.
기능 개발을 시작해도 되는 상태다.
