# MySQL 데이터로 기존 검색기 실행

MySQL은 원문·청크·버전을 관리한다. 앱 검색은 같은 MySQL 스냅샷에서 내보낸 JSONL과 Chroma를 함께 검증한 배포본을 읽는다. BM25는 배포본의 JSONL로 메모리에 구성한다. 검색할 때 기존 SQLite 원본이나 MySQL 접속 비밀번호가 필요하지 않다.

배포본은 생성 당시 `src/retrieval/*.py`의 바이트 해시를 고정한다. 검색 코드가 바뀌면 배포본을 다시 생성한다. PATCH-060부터 이 파일들은 모든 OS에서 LF로 체크아웃되므로(`.gitattributes`) Windows·macOS·Linux가 같은 배포본을 검증할 수 있다. PATCH-060 이전에 만든 배포본(`shared-v3`, `patch059-20260921` 등)은 CRLF 체크아웃 기준이라 새 코드에서 사용할 수 없다.

## 지원 환경

| 환경 | 상태 |
| --- | --- |
| Windows x64 | 지원. 빌드 담당 환경 |
| macOS Apple Silicon (arm64) | 지원. 기준 벡터 허용 오차 검증 사용 |
| Linux x64 | 지원 대상. 배포 전 `verify`·`query` 확인 필요 |
| macOS Intel | 미지원. 고정 버전의 PyTorch 설치 파일이 없음 |

Python은 3.11을 기준으로 한다. PyTorch는 반드시 PyPI(`pip install`)에서 설치한다. `download.pytorch.org` 설치본은 버전이 `2.14.0+cpu`·`+cu124`처럼 표시되어 배포본의 고정 버전과 달라 거절된다.

### CPU별 벡터 차이

Chroma는 색인을 열 때 기록 대기 중인 벡터를 HNSW에 다시 넣는다. 이때 CPU 종류(x86·Apple Silicon)에 따라 일부 벡터의 마지막 비트가 달라져 논리 해시가 달라질 수 있다. 배포본은 빌드한 PC의 전체 벡터를 `references/<채널>.f32`로 함께 담는다. 빌드 시 벡터를 제외한 ID·본문·메타데이터 해시(`records_sha256`)도 기록한다. 논리 해시가 다르면 먼저 이 해시가 빌드 당시와 정확히 같은지 확인한다. 값의 타입(`1`과 `true`)과 임베딩 출처 필드 유무까지 구분하므로 벡터 외의 변경은 여기서 거절된다. 그다음 모든 벡터가 기준 벡터와 `1e-6` 이내인지 검사한다. 그 이상 다르면 거절한다. 실측된 Apple Silicon 차이는 약 `1.5e-8`이다. 기존 Mac 전용 수동 패치(`case_index_reference_x86.json`)는 더 이상 필요하지 않으며 적용하지 않는다.

## 담당자: 배포본 생성

먼저 `mysql_transfer export`로 기본·민법·판례 스냅샷을 각각 `EXPORT_ROOT/base`, `EXPORT_ROOT/civil`, `EXPORT_ROOT/cases`에 내보낸다. 각 폴더에는 `manifest.json`과 JSONL이 있어야 한다. [적재·내보내기 명령](mysql-data.md)을 참고한다.

```powershell
python -m src.ingestion.mysql_search build --export-root EXPORT_ROOT --output data/mysql-search/releases/VERSION --model-dir KURE_MODEL_DIR --case-release data/case_corpus/release.json
```

최초 빌드는 기존 8,377건 판례 배포본의 색인을 재사용한다. 재사용 전에 파일 해시와 정책을 확인하고, 실제 색인의 전체 ID·본문·메타데이터·벡터 논리 해시를 MySQL 청크와 대조한다. 이후 갱신은 `--case-release` 대신 `--previous-release`에 이전 MySQL 검색 배포본을 지정한다. 이 경로에서는 기존 벡터를 검증해 재사용하고 새 본문만 임베딩하므로, 판례 추가·수정·삭제도 반영할 수 있다. [원문 변경·재청킹·검색 갱신 안내](mysql-ingest.md)를 참고한다.

민법은 기본 스냅샷과 별도 민법 스냅샷에 중복 저장되어 있다. 두 자료가 완전히 일치하는지 확인한 뒤 검색에는 별도 민법 색인 하나를 사용한다. 판례 배포에 포함된 과거 법령은 판례 검색기의 내부 자료로 유지하고, 앱 법령 채널은 기본 스냅샷의 확장 법령 정책을 사용한다.

모델 폴더에는 판례 배포 `embedding_model.files`에 명시된 파일이 있어야 한다. 기본 법령과 판례의 과거 모델 revision은 다르므로, 두 revision의 추론 파일 해시가 모두 같을 때만 하나의 모델을 공유한다. 일치하지 않으면 빌드를 중단한다. 모델 파일은 [Hugging Face의 로컬 폴더 다운로드](https://huggingface.co/docs/huggingface_hub/guides/download#download-files-to-a-local-folder) 방식으로 준비할 수 있다.

Chroma 생성 프로세스가 종료된 뒤 물리 파일 해시를 계산한다. 배포본이 완성되기 전에는 최종 폴더를 만들지 않으며 기존 배포 폴더는 덮어쓰지 않는다. Windows에서 TEMP 경로는 영문 경로여야 한다.

## 팀원: 동일 배포본 설치·실행

현재 공유 대상은 **`LENS-MySQL-search-patch060-r2-20260921.zip`**이다. release 폴더는 `data/mysql-search/releases/patch060-r2-20260921/`, release ID는 `5b1a63a7b004f63ad2dae018d53b9e4e0cdd120a61d623ec3270174c53622703`이다. 아래 `VERSION`은 `patch060-r2-20260921`로 바꾼다. `records_sha256`이 없는 1차 PATCH-060 배포본도 현재 코드와 함께 사용하지 않는다.

r2는 Windows 공식 `verify`와 관련 테스트31개를 확인했다. 초기 배포의 Apple Silicon 통과 기록을 r2 Mac·Linux 실측 완료로 확대하지 않으며, 각 환경에서 설치 후 `verify`·`query`를 실행한다.

담당자가 만든 배포 폴더 전체와 동일한 검색 코드를 사용한다. 개인별로 데이터를 다시 수집하거나 임베딩하거나 MySQL에 접속할 필요는 없다. macOS에서도 배포본을 직접 빌드하지 않는다. 배포본은 생성한 커밋의 검색 코드에서만 통과하므로, 그 커밋이 반영된 `main`(병합 전에는 해당 브랜치·커밋)을 checkout한 뒤 설치한다.

배포본 폴더(`data/mysql-search/releases/VERSION/`)에는 모델이 없다. 팀에 공유하는 배포 ZIP에는 배포본과 같은 해시의 모델(`data/models/kure-mysql/`)을 함께 넣을 수 있다. 이 경우에도 아래 `model` 명령이 모델 해시를 다시 확인하고, 파일이 모두 있으면 내려받지 않는다.

PATCH-060 이전에 clone한 저장소는 검색 코드 파일이 CRLF로 남아 있을 수 있다. 커밋하지 않은 변경이 없는지 확인한 뒤 한 번 다시 체크아웃한다(새로 clone해도 된다).

```powershell
Remove-Item src\retrieval\*.py
git checkout -- src/retrieval
```

macOS·Linux:

```bash
rm src/retrieval/*.py
git checkout -- src/retrieval
```

Python 환경은 프로젝트 `requirements.txt`를 설치한다. MySQL 적재·내보내기를 실행하는 담당자만 `requirements-mysql.txt`가 추가로 필요하다. 명령은 저장소 루트에서 실행한다. Windows는 먼저 영문 TEMP 경로를 지정한다.

```powershell
New-Item C:\Temp, tmp -ItemType Directory -Force
$env:TEMP='C:\Temp'
$env:TMP='C:\Temp'
python -m src.ingestion.mysql_search requirements --release data/mysql-search/releases/VERSION/release.json --output tmp/requirements-search.txt
python -m pip install -r requirements.txt -c tmp/requirements-search.txt
python -m src.ingestion.mysql_search verify --release data/mysql-search/releases/VERSION/release.json
python -m src.ingestion.mysql_search model --release data/mysql-search/releases/VERSION/release.json --model-dir data/models/kure-mysql
python -m src.ingestion.mysql_search activate --release data/mysql-search/releases/VERSION/release.json --pointer data/mysql-search/active.json
```

macOS·Linux는 Python 3.11 가상환경에서 같은 명령을 실행한다.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
R=data/mysql-search/releases/VERSION/release.json
mkdir -p tmp
python -m src.ingestion.mysql_search requirements --release "$R" --output tmp/requirements-search.txt
python -m pip install -r requirements.txt -c tmp/requirements-search.txt
python -m src.ingestion.mysql_search verify --release "$R"
python -m src.ingestion.mysql_search model --release "$R" --model-dir data/models/kure-mysql
python -m src.ingestion.mysql_search activate --release "$R" --pointer data/mysql-search/active.json
```

`verify`가 `verified: true`를 출력해야 한다. 모델 폴더를 Hugging Face 캐시에서 직접 복사하면 심볼릭 링크가 남아 거절되므로 `model` 명령으로 준비한다. `active.json`은 PC별 절대 경로를 담으므로 다른 PC의 파일을 복사하지 않고 각 PC에서 `activate`로 만든다.

배포 시점의 Chroma·NumPy·Sentence Transformers·PyTorch·Transformers·Tokenizers 버전을 함께 고정한다. `requirements`는 배포 manifest 해시를 확인한 뒤 제약 파일을 생성하며, 검색 실행은 실제 설치 버전까지 대조한다. 이 명령은 pip만 있는 새 가상환경에서도 실행되고 출력 폴더가 없으면 만든다. 같은 내용의 파일이 이미 있으면 그대로 두고, 내용이 다른 파일은 덮어쓰지 않고 실패한다.

`model` 명령은 고정 revision의 필요한 파일 중 없는 것만 로컬 폴더로 다운로드하고 전체 해시를 확인한다. 기존 파일이 다른 내용이면 덮어쓰지 않고 실패한다. 배포본 폴더만 받았다면 최초 한 번 모델 다운로드가 필요하고, 모델이 포함된 팀 ZIP을 풀었다면 다운로드 없이 해시만 확인한다.

앱 실행 환경 또는 로컬 `.env`에 다음 두 경로를 지정한다. `LENS_CASE_RETRIEVAL_PROFILE`은 비워 둔다.

```dotenv
LENS_MYSQL_RELEASE=data/mysql-search/active.json
LENS_MYSQL_MODEL_DIR=data/models/kure-mysql
```

기존 `RetrievalService.from_index()`와 생성 체인의 `get_default_service()`가 이 배포본을 읽는다. 명시한 배포본이 깨졌거나 코드·정책이 다르면 오류를 반환한다. 검색 코드가 변경되면 배포본을 다시 생성·검증한다. 활성 포인터는 새 배포 전체 검증이 통과한 뒤 원자적으로 교체되므로 실패 시 이전 버전이 유지된다. 앱은 검색기를 메모리에 캐시하므로 **활성화 후 앱 프로세스를 재시작**한다. 이전 배포를 다시 활성화하면 되돌릴 수 있다.

LLM 없이 실제 검색을 확인하려면:

```powershell
python -m src.ingestion.mysql_search query --release data/mysql-search/active.json --model-dir data/models/kure-mysql --question "전입신고와 확정일자는 어떤 차이가 있나요?" --output tmp/mysql-evidence.json
```

결과는 기존 `lens-retrieval-evidence-v1` 계약을 유지하고 `release_id`와 근거별 `snapshot_id`를 추가한다. 근거의 `snapshot_id`와 `chunk_id`를 `mysql_transfer trace`에 전달하면 당시 MySQL 원문·출처로 추적할 수 있다.

## LLM 연결 범위

사용자 요청에 따라 실제 LLM 서버 준비 전에는 검색 연결까지 검증한다. 기존 생성 API의 입력 계약은 유지하며, 모의 LLM 테스트로 법령·민법·판례·안내 본문이 프롬프트에 전달되는지 검사한다. 이는 실제 LLM 응답 품질 검증을 의미하지 않는다.

서버가 준비되면 기존 생성 체인을 그대로 실행한다. 판례 근거가 포함된 위 JSON은 `scripts/case_corpus_llm.py --evidence tmp/mysql-evidence.json --output tmp/mysql-llm.json`으로 연결 검사를 할 수 있다. 앱 전체 보안·답변 품질 평가는 별도로 수행해야 한다.
