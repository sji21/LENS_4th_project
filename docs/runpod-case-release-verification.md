# RunPod 판례 배포 검증 — 2026-09-17

## 검증 입력과 제외 항목

- GitHub `sji21/LENS_4th_project`의 `main`: `26c5745093cb324951351037cb9fb2fd1cef9dda`.
- 위 체크아웃에 당시 커밋 예정 코드·문서·스키마·`data/case_corpus` 배포 파일을 적용했다. 이 검증 실행 시점에는 Git 커밋·푸시 전이었다. 후속 배포는 PATCH-055로 별도 기록한다.
- 개인 로컬 `test/`, 별도 판례 DB, `.env`, 가상환경, 모델 캐시와 과거 로컬 검색 결과는 반입하지 않았다.
- 임베딩 모델과 Qwen은 RunPod에서 공개 배포처로부터 새로 다운로드했다.
- 최초 변경 패치 SHA-256: `fbcd139ca4f6f2cdc1f421cefa926939a970f0418e951aaf650552f19f21f16a`.
- 신규 파일 전송 압축본 SHA-256: `89c9afbccdb3e2cf67417097893f09f988f56b3b46377b78dcc6148962d7d2ba`.
- 아래에서 발견한 구축 코드 수정과 회귀 테스트는 최초 전송 후 추가 반영했다. Windows 텍스트 파일의 줄바꿈은 전송 방식에 따라 CRLF를 포함한다.

## 실행 환경과 순서

Python 3.12.3, NVIDIA A40, CUDA 12.8 드라이버 환경이다. 독립 가상환경에 공개 CUDA 휠 `torch==2.8.0+cu128`을 먼저 설치한 뒤 `setup_data.py`를 실행했다. Ollama 0.34.1과 `qwen3:8b-q4_K_M`을 사용했다. Ollama와 Django는 RunPod의 루프백 주소에만 열었다. Django 키는 새로 생성한 런타임 값이며 로컬 `.env`를 사용하지 않았다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install torch==2.8.0+cu128 --index-url https://download.pytorch.org/whl/cu128
HF_HOME=/root/lens-hf PIP_NO_COMPILE=1 .venv/bin/python setup_data.py
HF_HOME=/root/lens-hf .venv/bin/python setup_data.py --check
HF_HOME=/root/lens-hf .venv/bin/python scripts/case_corpus_query.py --question '임대차 보증금 반환과 임대인의 상계' --k 2 --output tmp/runpod-case-query.json
JEONSEON_LLM_BASE_URL=http://127.0.0.1:11434/v1 JEONSEON_LLM_MODEL=qwen3:8b-q4_K_M .venv/bin/python scripts/case_corpus_llm.py --evidence tmp/runpod-case-query.json --output tmp/runpod-case-qwen.json
```

`/root/lens-hf`는 이번 검증에서 새로 만든 공개 모델 캐시다. Pod 재생성 시 유지되는 운영 저장 위치라고 보장하지 않는다. 이 명령은 Git LFS 데이터 확보와 Ollama 설치·모델 다운로드가 완료된 후 실행한다. 이번에는 아직 업로드하지 않은 LFS 대상 실파일을 승인된 커밋 예정 배포 묶음으로 전달했다. **원격 Git LFS 업로드 후 팀원의 `git lfs pull` 검증을 대체하지 않는다.**

## 확인된 결과

- SQLite 무결성 `ok`; 판례 8,377건, 판례 JSONL 8,377개, 전체 인덱스 청크 8,516개.
- DB·JSONL 파일 해시, Chroma 물리 파일 6개의 해시, 논리 인덱스, DB↔청크 연결 및 저장 본문·출처 해시 검증 통과.
- 고정된 기본/판례 임베딩 모델 revision을 모두 확보했으며 `setup_data.py --check` 종료 코드 0.
- 실제 판례 검색에서 `case:605337#0`(대법원 2024다302217)과 `case:194367#0`(대법원 2002다52657)이 본문·인용·출처와 함께 반환됐다.
- 앱의 `get_default_service()`가 판례 8,377건 프로필과 기본 서비스를 함께 로딩했다. 보증금·갱신·수리·반환보증 질문의 합집합에서 법령·민법·판례·안내 네 채널을 확인했다. 안내는 모든 질문에 반환되지 않으며 반환보증 질문에서는 1개가 반환됐다.
- 실제 검색 근거 → 기존 생성 체인 → Qwen 8B 연결에서 비어 있지 않은 답변 생성. 작은 모델이나 Fake LLM으로 대체하지 않았다.
- 앱에서 실제 검색한 법령·민법·판례 혼합 근거도 같은 Qwen 생성 체인에 전달했다. 입력 4,089 토큰, 출력 226 토큰, `done_reason=stop`으로 완료했다. 웹 대화의 전체 그래프를 호출한 검사는 아니다.
- 추가 판례 프로필이 존재하는 상태에서 두 번째 `setup_data.py` 실행도 성공했다. 기본 구축 상태는 `unchanged`, 재임베딩 0건이며 기존 인덱스 검사와 추가 판례 프로필 검증을 통과했다.
- Django 마이그레이션 완료; `http://127.0.0.1:8000/` HTTP 200. 이것은 화면 진입 확인이며 모든 웹 기능의 종단 테스트를 의미하지 않는다.
- 설치·프로필·연결 관련 테스트 45개 통과. 추가 수정 후 구축 모듈 테스트 42개 통과.
- 전체 테스트: **2,828 passed, 3 failed, 3 errors, 4 skipped, 678 subtests passed**. 실패한 과거 평가 모듈 2개를 변경 없는 GitHub 원본에서 실행해 동일한 3 failed/3 errors를 재현했다. 전체 테스트가 통과했다고 표시하지 않는다.

## 발견하고 수정한 설치 문제

1. 기본 구축 진입점과 작업자가 모두 고정 revision 모델을 검증하도록 맞췄다. 별도 판례 revision과 충돌하는 `refs/main`을 요구하거나 덮어쓰지 않는다.
2. 기본 인덱스의 smoke 검사는 기본 서비스만 로딩한다. 추가 판례 프로필과 명시적 기본 경로가 충돌하지 않도록 분리했다. 실제 앱의 추가 판례 프로필 자동 연결은 유지한다.

두 문제에 대한 회귀 테스트를 추가했다. 데이터 건수·해시 검사나 프로필 fail-closed 정책은 완화하지 않았다.

## 남아 있는 한계

보완 판례 11개의 JSONL에는 `observed_at`/`corpus_active` 필드가 없으며 검증 보고서에 각각 11건으로 기록했다. 저장 본문 해시 일치는 공식 판결 전문의 의미상 완전성이나 Qwen 답변의 법률적 정확성을 보장하지 않는다. 연결 검사 결과의 `application_quality_verified`는 `false`이다. 검사 출력과 런타임 프로필·웹 DB·모델 캐시는 커밋하지 않는다.
