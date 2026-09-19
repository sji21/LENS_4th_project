# data_dev_v2 판례 코퍼스 Git 배포

## 배포 기준과 현재 상태

선택한 자료는 `data_dev_v2`이며 판례 **8,377건**, 판례 청크 **8,377개**다.
법령 133개(법률 74·시행령 59), 안내 6개를 포함한 전체 인덱스는 **8,516개**다.
8,395건 PATCH-043 실험 자료와 다르다. 기존 26건 시드는 기본 법령·민법·안내
설치 레시피의 일부로 남지만, 설치가 활성화한 판례 검색은 이 추가 코퍼스를 사용한다.
26건 평가 결과를 8,377건 코퍼스 성능으로 확대 해석하지 않는다.

실제 파일은 배포 폴더에 복사했지만, 커밋·원격 Git LFS 업로드와 원격 pull
검증이 끝나기 전에는 팀원에게 배포 완료로 안내하지 않는다. 2026-09-17 사용자 요청으로
RunPod의 새 환경에서 설치·실제 검색·Qwen 연결을 확인했다. 개인 로컬 `test/`나
기존 모델 캐시는 사용하지 않았다. 범위와 기존 테스트 실패는
[RunPod 검증 기록](runpod-case-release-verification.md)에 기록한다.

## 보존하는 기존 구조

```text
data/case_corpus/
├─ release.json
├─ sample-case.json  (기존 DB의 실제 판례 1건·문서·청크 예시)
├─ database/knowledge.sqlite3
├─ chunks/laws.jsonl
├─ chunks/cases.jsonl
├─ chunks/guides.jsonl
└─ index/chroma_kurev1_1024/  (SQLite + HNSW 파일)
```

`knowledge.sqlite3`의 `cases`, `documents`, `chunks`, 판례 버전·관찰 테이블을
그대로 보존한다. `cases.sqlite3` 전용 스키마로 변환하거나 판례를 추가하지 않는다.
원본 `data_dev_v2`와 `.broken_*` 보관 인덱스는 변경하지 않는다.

매니페스트는 `schemas/case-corpus-release-v1.schema.json`을 따른다. 경로는 배포
루트 기준 상대 경로이며, 팀원 컴퓨터의 절대 경로·SSH 키·API 키를 넣지 않는다.
DB·JSONL·인덱스는 Git LFS, 매니페스트·코드·스키마·문서는 일반 Git으로 추적한다.
런타임 절대 경로를 담는 `runtime-profile.json`은 Git에서 제외한다.

## 실제로 고정한 실행 정보

9월 15일의 `retrieval_profile.rebuilt.json`과 기존 모델 감사 기록을 사용했다.
현재 인덱스는 이전 봉인 인덱스를 그대로 복제한 것이 아니라 재색인된 인덱스다.

- KURE-v1 revision: `8b418a58414668e75532ed045c22d9ca018ae2b2`, 1,024차원
- 현재 논리 인덱스 SHA-256: `85850fa63854788b01ae675cad6003941c536deb7fc77c42b1f30a0b0df4c4d7`
- 후보 80·기본 반환 20, `civil_terms` 확장, RRF60, BM25:dense 2:1
- `band_3pct` 상급심·최신 우선, `tax_source_guard` 출처 필터
- 앱이 명시한 판례 Top-2는 그대로 유지한다. 기본 반환 20과 혼동하지 않는다.
- 모델 가중치를 Git에 올리지 않는다. 설치기가 매니페스트의 모델 revision과
  9개 파일 해시를 확인하고, 없는 파일만 Hugging Face에서 받는다.

기본 법령·민법·안내는 기존 `server_build`로 준비하고, 앱은 기존 서비스의
다른 채널을 유지하면서 판례 채널만 기존 `CaseCorpusRetrievalService`로 교체한다.
기본 KURE revision은 기존 감사 기록으로 고정한다. 판례 모델을 준비하면서
기본 모델의 `refs/main`을 자동 교체하지 않는다.

## 무결성 검사 범위와 한계

검증기는 DB·청크·인덱스 파일 해시, SQLite 무결성, 판례·고유 사건 키 건수,
원문 필드와 출처 URL, DB 원문 SHA-256, DB/JSONL 사건 연결·본문·핵심
메타데이터, Chroma ID·본문·메타데이터·벡터 논리 해시와 cosine 설정을 검사한다.
LFS 포인터만 있고 실제 파일이 없는 상태는 실패한다. 대체 26건 데이터로
조용히 내려가는 fallback은 허용하지 않는다.

보완 판례 11건의 JSONL에는 `observed_at`, `corpus_active`가 없고 DB에는 있다.
이 원래의 차이를 유지·집계한다. 존재하는 관찰 시각·활성 플래그는 DB와 대조한다.
해당 판례의 사건정보·본문·출처·버전 식별자는 검증에서 제외하지 않는다.

검증 통과는 **기존 파일의 일치·연결 확인**이다. 공식 웹페이지의 판결 전문과
모든 문장을 새로 대조한 결과나 판례 검색 성능·법률적 타당성 인증이 아니다.
`full_text` 필드와 URL이 있다고 해서 원래 수집본의 의미적 완전성을 단정하지 않는다.

Windows의 한글 인덱스 경로에서는 HNSW 로딩 문제가 재현되어 네이티브 독자에
영어 임시 경로 사본을 제공한다. 봉인 인덱스는 운영체제에 관계없이 사본에서
열어 Chroma의 내부 SQLite 변경이 Git 원본 해시를 바꾸지 않게 한다.
원본 검증을 생략하는 우회가 아니다. 네이티브 비정상 종료와 전체 테스트
결과는 실제 검증 기록에서 별도로 확인해야 한다.

### 2026-09-16 로컬 확인 결과

Python 3.12 환경에서 전체 배포·모델 파일 검증, 실제 8,377건 청크 재출력
바이트 해시 일치, 샘플 JSON 출력이 통과했다. 실제 고정 KURE 모델 검색에서는
“임대차 보증금 반환과 임대인의 상계”에 2건과 공식 출처 URL이 반환됐다.
이 한 질문은 전체 검색 성능의 증거가 아니다.

동일 근거를 기존 Qwen 생성 체인으로 호출했으나, 로컬 Ollama가 HTTP 500을
반환했다. 서버 로그는 `CPU_REPACK` 3,312,451,584바이트 버퍼 할당 실패를
기록했다. 따라서 실제 `qwen3:8b-q4_K_M` 답변 생성은 **미검증**이다.
모델 설치 목록에 있는 것과 모델이 메모리에 올라 응답하는 것은 다르다.
8B 모델을 실행할 충분한 자원의 Ollama 서버에서 위 연결 검사를 다시 해야 한다.
작은 모델로 대체하거나 RunPod에 설치해 성공으로 처리하지 않았다.

## 팀원 설치 순서

원격 배포 완료 후 저장소 루트에서:

```powershell
git lfs install
git pull
git lfs pull
py -3.12 setup_data.py --venv-dir C:\lens-venv-312
py -3.12 setup_data.py --venv-dir C:\lens-venv-312 --check
```

Windows 새 Python 3.11 환경에서 Chroma access violation이 재현됐고, 별도
Python 3.12 환경의 실제 8,516개 인덱스 count가 통과했다. 따라서 이번 Windows
배포 시험은 3.12를 사용한다. 3.11이 모든 운영체제에서 실패한다고 일반화하지 않는다.
이미 있는 3.11 가상환경을 3.12 실행에 재사용하지 않고 새 `--venv-dir`를 지정한다.

설치는 Python 환경·기본 모델·판례 모델을 준비하고, 기존 법령·민법·안내를
구축한 뒤 판례 배포를 전체 검증해 `data/case_corpus/runtime-profile.json`을 만든다.
환경변수 `LENS_CASE_RETRIEVAL_PROFILE`은 자동 발견 프로필보다 우선한다.
새 pull에서 배포 `release.json`은 있지만 런타임 프로필이 없으면 앱 기본 검색은
설치 안내 오류로 중단한다. 설치 전 기존 26건으로 조용히 돌아가지 않는다.
기존 환경변수로 다른 프로필을 지정했다면 제거하거나 의도한 파일인지 확인한다.

설치 없이 배포 파일만 검사하려면 준비된 환경에서:

```powershell
python -m src.ingestion.case_release --release data/case_corpus/release.json
```

## 리트리버와 LLM 연결

```powershell
python scripts/case_corpus_query.py --question "임대차 종료 뒤 보증금 반환 판례는?" --k 2 --output tmp/case-query.json
```

이 단독 검사는 판례 프로필·KURE 모델·벡터를 사용한다. 다른 채널 DB나 Ollama를
호출하지 않는다. 청크 ID·사건 키·본문·인용·출처·점수와 프로필 버전을 출력한다.
앱에서는 기존 `get_default_service()` → 단계별 검색 → 생성 체인 연결을 유지한다.
판례 본문·인용은 생성 프롬프트로, 출처 URL은 Evidence 및 화면 출처로 보존한다.
자동 테스트의 Fake LLM 연결 확인과 실제 Qwen 실행 확인을 구분한다.

검색 프로세스 종료 후 저장된 근거를 기존 `format_context()` →
`build_qa_chain()` → Ollama native Qwen 호출에 전달하려면:

```powershell
python scripts/case_corpus_llm.py --evidence tmp/case-query.json --output tmp/case-qwen.json
```

`JEONSEON_LLM_MODEL` 기본값은 `qwen3:8b-q4_K_M`이다. Ollama 주소는 기존
`JEONSEON_LLM_BASE_URL` 설정을 사용한다. 작은 모델·Fake 응답으로 자동 대체하지
않는다. 결과에는 모델명·질문·답변·원래 근거·입력/컨텍스트 해시가 남으며,
빈 답변은 실패한다. 두 명령을 순서대로 실행하면 KURE 모델이 종료된 뒤
Qwen을 로딩할 수 있다. 이것은 연결 검사이며 앱의 보안·분기·법률적 품질 검사를
대신하지 않는다. 검사 출력은 `tmp/`에 두고 Git에 올리지 않는다.

실제 Django/Qwen 시험은 별도 로컬 실행 단계다. `.env`의 Django 키를 준비하고
`qwen3:8b-q4_K_M`이 실행 중인 Ollama 주소를 지정한 후 기존 migrate/runserver
절차를 따른다. RunPod 실측은 위 검증 기록의 별도 실행이며, 이 문서 자체가
RunPod 자동 설치 스크립트를 제공하는 것은 아니다.

## 재출력과 새로운 재색인

```powershell
python -m src.ingestion.case_release --release data/case_corpus/release.json --reexport-cases tmp/cases-reexport.jsonl
```

DB의 `chunks.content`·문서 연결을 사용하고 봉인 JSONL의 메타데이터와 순서를
보존하여 바이트 해시까지 같은 청크를 재출력한다. 이는 새로운 청킹이 아니다.
보완 판례의 `source_page_sha256`, `indexed_official_section` 등 일부 메타데이터는
DB만으로 재구성할 수 없어 기존 JSONL도 필요하다. 따라서 이번 배포는 DB와
청크를 둘 다 전달한다. DB를 JSON으로 추가 복제할 필요는 없다.

판결 전문에서 새 청킹을 하거나 다른 장치에서 재임베딩하면 새 산출물·버전·
매니페스트와 별도 검색 평가를 만든다. GPU/CPU·Windows 처리에 따른 미세한
벡터 차이를 기존 해시에 억지로 맞추지 않는다. 기존 코퍼스에 덮어쓰지 않는다.

## 배포 생성 코드

```powershell
python -m src.ingestion.case_release --build-root data/case_corpus --source-profile <재색인 프로필 JSON> --model-audit <기존 모델 감사 JSON> --version <새 배포 버전>
```

생성은 기존 프로필의 파일 해시·정책·모델을 사용하며 즉시 전체 검증한다.
기존 `release.json`은 덮어쓰지 않는다. 새 버전은 별도 배포 루트에 준비한다.
샘플 레코드도 `--sample-output <새 JSON 경로> --sample-case-id <기존 판례 ID>`로
DB에 실제 존재하는 판례·문서·청크만 내보낸다. 빈 필드에 추론값을 채우지 않는다.
