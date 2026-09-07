# 판례 전용 임베딩 비교 인계

## 공식 판례 표준 적재 흐름

법령의 `law_records.jsonl`과 같은 역할을 하는 판례 표준 입력은
`data/parsed/case_records.verified.jsonl`이다. 원천 상세 응답, SQLite, 청크, Chroma는 모두
Git 제외 대상이고, 아래 코드는 커밋해 팀원이 같은 원천으로 재생성한다.

```text
공식 판례 상세 원천 JSONL
  → data/raw/phase1_official_case_details.verified.jsonl
  → src.ingestion.parse_cases
  → data/parsed/case_records.verified.jsonl
  → src.ingestion.load_cases
  → data/database/knowledge.sqlite3 + data/chunks/cases.jsonl
  → src.retrieval.index
  → data/index/chroma_kurev1_1024 / knowledge_chunks
```

판례는 현재 **판결요지 하나를 한 건의 단일 검색 청크**로 쓴다. `parse_cases`는 국가법령
정보센터 상세 응답의 `판결요지`를 `holding`·`summary`에, `판례내용` 전문을 `full_text`에
각각 보존한다. 따라서 전문을 여러 청크로 나누지는 않지만 원천 전문은 SQLite에 남는다.

PATCH-023의 리뷰 보완 변환은 다음 계약을 지킨다.

- JSON·API 구조·필수 필드 오류 또는 사건번호 충돌이 하나라도 있으면 종료 코드 1이며 기존 출력은 유지한다.
- 재수집은 필수 상세 필드 중 하나라도 누락된 레코드를 대상으로 한다. 한 건이라도 미회복이면 재수집 결과 JSONL은 교체하지 않고, 보고서만 `published: false`로 남긴 뒤 종료 코드 1로 끝낸다.
- 범위 밖·수동 제외·짧은 요지는 `excluded`, 적용 법령이 불명확한 사건은 `needs_review`로 분리한다.
- 자동 적재는 주택임대차보호법 적용·참조 근거가 확인된 사건만 허용한다. 상가·점포·권리금 신호는 제외하고, 주택·상가 신호가 함께 있으면 수동 검토한다.
- 같은 사건번호는 법원명·선고일·사건명·공식 전문 체크섬까지 같을 때만 동일 공개본으로 처리한다. 하나라도 다르면 충돌로 종료한다.
- 공개 API 검증 원천은 후보의 `case_id` 일치를 필수로 하고, 사건번호·법원명·선고일 중 둘 이상이 일치할 때만 같은 사건으로 인정한다. `고법`/`고등법원`, 병합 사건의 대표 사건번호처럼 의미가 같은 표기만 정규화하며, ID 불일치 또는 보조 필드 두 개 미만 일치면 기존 원천을 유지한다.
- 공식 상세 응답은 정상이나 `판결요지`만 없는 경우에는 검색 코퍼스에 적재하지 않는다. 보고서의 `information_missing`에 “판결요지가 없습니다. 원문을 확인해주세요.” 안내와 원문 URL을 남겨 MVP가 별도 표시할 수 있게 한다.
- 수동 분류 CSV는 기본 수동 검토 사건에만 `case_id`로 적용한다. `approved`만 후보에 포함하고, `rejected`는 제외하며, `pending` 또는 미분류 사건은 계속 `needs_review`로 남긴다. 사건번호는 사람이 읽는 보조 정보일 뿐 승인 키가 아니다.

```powershell
# 1. 공식 원천 → 표준 판례 JSONL
python -m src.ingestion.parse_cases `
  --input data/raw/phase1_official_case_details.verified.jsonl `
  --output data/parsed/case_records.verified.jsonl `
  --collected-at 2026-08-30T00:00:00Z `
  --report data/parsed/case_records.verified.report.json `
  --manifest data/parsed/case_records.verified.manifest.json `
  --review-classification data/parsed/case_records.residential_review_classification.csv

# 2. 표준 판례 JSONL → 공통 SQLite + 판례 청크
python -m src.ingestion.load_cases `
  --records data/parsed/case_records.verified.jsonl `
  --database data/database/knowledge.sqlite3 `
  --export data/chunks/cases.jsonl

# 3. 청크 규격 검사 후 공통 Chroma에 판례 범위만 동기화
python -m src.ingestion.validate_chunks data/chunks/cases.jsonl
python -m src.retrieval.index `
  --chunks data/chunks/cases.jsonl `
  --path data/index/chroma_kurev1_1024
```

Windows CP949 기본 환경에서는 Python을 UTF-8 모드로 실행한다.

```powershell
$env:PYTHONUTF8 = "1"
```

공개 API 재수집 명령은 저장소 루트의 `.env`에 있는 `LAW_OPEN_API_OC`를 사용한다.
예제 파일을 복사한 뒤 값을 채우고, 다른 위치에 비밀 파일을 둘 필요가 없다.

### 잘못된 판례 일련번호 복구

상세 API가 `PrecService` 대신 다른 응답을 반환하면 기존 `case_id`를 추정으로 바꾸지
않는다. `src.ingestion.resolve_case_ids`가 후보의 사건번호·법원·선고일과 목록 API 결과가
**모두 정확히 일치**할 때만 정식 `판례일련번호`를 매핑한다. 매핑 결과가 있을 때에만
`src.ingestion.refetch_case_details --id-mapping`으로 별도 원천 사본을 재수집한다.

```powershell
python -m src.ingestion.resolve_case_ids `
  --candidates data/raw/phase1_official_case_candidates.jsonl `
               data/raw/phase1_official_case_candidates_expanded.jsonl `
               data/raw/phase1_official_case_candidates_gap_fill.jsonl `
               data/raw/final_phase_official_case_candidates.jsonl `
  --error-report data/parsed/case_records.reviewed.report.json `
  --output data/raw/case_id_mapping.json `
  --report data/raw/case_id_resolution.report.json `
  --oc-env-file .env

python -m src.ingestion.refetch_case_details `
  --input data/raw/phase1_official_case_details.jsonl `
  --output data/raw/phase1_official_case_details.refetched.jsonl `
  --report data/raw/phase1_official_case_details.refetched.report.json `
  --id-mapping data/raw/case_id_mapping.json `
  --oc-env-file .env
```

### 공개 API 검증 원천 재생성

과거 원천의 누락 오류를 그대로 변환하지 않고, 현재 최종 후보 **274건**을 공식 상세 API에서 다시
검증한다. 필수 필드가 완전한 응답만 새 원천에 쓰고, 상세 응답은 정상이나 판결요지만 없는 응답은
`information_missing` 안내로 보고서에만 남긴다.

```powershell
python -m src.ingestion.build_verified_case_source `
  --candidates data/raw/final_phase_official_case_candidates.jsonl `
  --output data/raw/phase1_official_case_details.verified.jsonl `
  --report data/raw/phase1_official_case_details.verified.report.json `
  --oc-env-file .env
```

이 명령의 현재 재현 결과는 **완전 응답 154건**을 원천 JSONL에 쓰고, **판결요지 누락 120건**을
`information_missing`으로 보고서에 남기는 것이다. 274건 모두를 완전 응답으로 요구하지 않으므로
정상 종료한다. 수집 실패 또는 동일성 불일치가 한 건이라도 있으면 기존 원천은 보존하고, 보고서에
`published: false`와 사유를 남긴 뒤 종료 코드 1로 끝난다. 과거 348건·194건 미회복 수치는 이 명령의
현재 입력이나 성공 조건이 아니라, 삭제 전 후보 목록에 대한 역사적 검증 기록이다.

## 재현 증빙

원천과 파생 데이터는 Git 제외 대상이므로, 변환을 실행한 담당자는 아래 파일을 함께
전달하거나 팀 공유 저장소에 보관한다.

- 원천 상세 응답 JSONL의 전달 위치와 SHA-256
- 생성된 `case_records.verified.jsonl`의 SHA-256
- `case_records.verified.manifest.json`의 판례 ID 목록·입력/출력 해시
- `case_records.verified.report.json`의 입력 건수, 제외·오류·수동 검토·충돌 건수와 사유별 건수. `error_records`에는 재수집 대상의 줄 번호·판례 ID·원천 URL·누락 필드가 구조화되어 있다.

검토자가 실제 API 원천 없이도 규칙을 확인할 수 있도록
`tests/fixtures/case_details_sample.jsonl`에 주택 포함·상가 제외·수동 검토 사례를 둔다.
실제 변환 결과의 건수는 이 강화된 규칙으로 매번 다시 산출하며, 과거 207건 수치를
성공 조건으로 고정하지 않는다.

3단계의 오래된 벡터 삭제 범위는 `doc_type=case`뿐이다. 같은 Chroma 컬렉션의 법령과
공식 안내 벡터는 삭제하지 않는다.

PATCH-019의 판례 전용 비교는 PATCH-018의 공통 SQLite·청크·Chroma 구조 위에서
실행한다. 별도 데모 SQLite나 운영용 Chroma 컬렉션을 만들거나 수정하지 않는다.

## 입력과 경계

```text
scripts/load_case_only_demo_corpus.py
  → data/database/knowledge.sqlite3
  → data/chunks/cases.jsonl
  → data/index/chroma_kurev1_1024 / knowledge_chunks   (운영 통합 인덱스)
```

- `cases.jsonl`은 `src.ingestion.load_cases.export_case_chunks`가 생성한 표준 청크다.
- 청크 ID·`case_id`·`case_number`·출처 URL 등 PATCH-018 메타데이터를 그대로 쓴다.
- 판례 전용 실험은 27개 공통 평가 질문 중 13개만 순위 지표에 포함한다. 나머지
  14개는 현행 조문·금액·행정절차 또는 개별 사실관계가 필요하므로 보류한다.
- 현재 26개 판례 본문은 평가용 요약문이다. 실제 판례 원문으로 교체하면 모든
  비교 결과를 다시 실행해야 하며, 결과를 일반 검색 성능으로 주장하면 안 된다.

## 준비

```powershell
python scripts/load_case_only_demo_corpus.py
python -m src.retrieval.index --chunks data/chunks/cases.jsonl --path data/index/chroma_kurev1_1024
```

두 번째 명령은 판례(`doc_type=case`)만 동기화한다. PATCH-017의 색인 범위 규칙에
따라 기존 법령 벡터는 지워지지 않는다.

## 실행

### 통합 KURE-v1 인덱스 평가

```powershell
python scripts/evaluate_case_only_retriever.py
```

기본 결과는 `data/eval/runs/housing_cases_only_kurev1.json`에 쓴다. 이 경로는
재생성 가능한 산출물이며 Git에 커밋하지 않는다.

### 로컬 모델 비교

```powershell
python scripts/compare_case_embedding_models.py --models kure_v1 bge_m3
```

Qwen3까지 포함하려면 모델 다운로드와 충분한 메모리가 필요하다. 모델별 벡터는
`data/index/chroma_case_embedding_comparison`의 **실험 컬렉션**에만 저장한다.

### Hugging Face Inference Providers 비교

```powershell
$env:HF_TOKEN = "..."
python scripts/compare_case_embedding_models_hf_api.py --preflight-only
```

`HF_TOKEN`은 요청 헤더에만 쓰며 보고서와 로그에 기록하지 않는다. 전체 비교는
`--preflight-only`를 생략한다. API 모델별 벡터도
`data/index/chroma_case_hf_api_comparison`의 실험 컬렉션에만 저장한다.

## 결과 해석

- Hit@1·@3·@5·MRR은 답변 가능 13문항에서만 계산한다.
- 보류 14문항은 모든 모델에 동일한 정책이므로, 모델 품질 점수가 아니다.
- 비교 결과는 동일한 `cases.jsonl`, 동일 질문, 동일 정답 판례 ID를 사용한 경우에만
  서로 비교할 수 있다.
