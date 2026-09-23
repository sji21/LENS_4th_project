# 팀 검색 배포본 설치·검증

이 경로는 MySQL 원문을 팀원 PC에 복사하는 방식이 아닙니다. 담당자가 MySQL 스냅샷에서
검증된 JSONL·Chroma·KURE 모델 묶음을 만들고, 팀원은 그 배포본만 읽습니다. 팀원 검색에는
MySQL 접속 정보가 필요하지 않습니다.

이 문서는 **현재 소스와 호환되는 새 ZIP을 받은 팀원**의 로컬 설치 안내입니다. Python 3.11을
사용합니다. ZIP이 없다면 [로컬 원천 구축](server-data-setup.md)을 따르세요. 이전 EC2 서버용
배포본 기록은 [AWS 검색 데이터 안내](../deploy/aws/MYSQL-SEARCH.md)에 있습니다.

## 현재 배포 상태

현재 코드에서 검증한 `django-runtime-20260922` release는 **로컬 검증본**이며 팀 ZIP은 아직
제공되지 않았습니다. 과거 `LENS-MySQL-search-patch060-r2-20260921.zip`은 현재 코드의 검색
해시와 달라 사용할 수 없습니다. 새 ZIP을 받으면 담당자가 제공한 대상 커밋·release ID가
현재 소스와 일치하는지 확인하세요. 과거 r2의 Windows x64·Apple Silicon macOS·Linux x64
설치 확인은 새 ZIP의 검증 결과가 아닙니다. macOS Intel은 고정 PyTorch 배포본 문제로
지원하지 않습니다([운영 경로 정리 기록](patch062-django-runtime-cleanup.md)). EC2에는 같은 MySQL
스냅샷으로 **서버 환경에 맞춰 다시 만든 별도 release**가 설치돼 있으며, 이를 팀 ZIP으로
배포한 것은 아닙니다([EC2 검색 전환 기록](../deploy/aws/MYSQL-SEARCH.md)).

## 팀원 설치 순서

호환 ZIP을 저장소 루트에 풀어 `data` 폴더가 합쳐지게 합니다. 아래 `RELEASE_NAME`을 ZIP 안
`data/mysql-search/releases/`의 실제 폴더명으로 바꾸세요. 기존 작업 파일이 있는 clone에서는
검색 코드를 강제로 복원하지 말고, 새 clone에서 ZIP을 검증하세요.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
New-Item C:\Temp -ItemType Directory -Force | Out-Null
$env:TEMP='C:\Temp'; $env:TMP='C:\Temp'
$release = 'data/mysql-search/releases/RELEASE_NAME/release.json'
Test-Path $release  # True여야 합니다
python -m src.ingestion.mysql_search requirements --release $release --output tmp/requirements-search.txt
python -m pip install -r requirements.txt -c tmp/requirements-search.txt
python -m src.ingestion.mysql_search verify --release $release
python -m src.ingestion.mysql_search model --release $release --model-dir data/models/kure-mysql
python -m src.ingestion.mysql_search activate --release $release --pointer data/mysql-search/active.json
```

### macOS·Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
mkdir -p tmp
release=data/mysql-search/releases/RELEASE_NAME/release.json
test -f "$release"
python -m src.ingestion.mysql_search requirements --release "$release" --output tmp/requirements-search.txt
python -m pip install -r requirements.txt -c tmp/requirements-search.txt
python -m src.ingestion.mysql_search verify --release "$release"
python -m src.ingestion.mysql_search model --release "$release" --model-dir data/models/kure-mysql
python -m src.ingestion.mysql_search activate --release "$release" --pointer data/mysql-search/active.json
```

`verify` 결과의 `verified`가 `true`인지 확인합니다. 모델 파일이 ZIP에 포함되지 않았다면
`model` 단계에서 고정 revision의 파일을 다운로드합니다. 검증이 실패하면 현재 소스와 ZIP의
버전·해시를 담당자에게 확인하세요. 다른 PC의 `active.json`은 복사하지 말고 각 PC에서
`activate`를 실행합니다. 이 파일은 PC별 경로를 담습니다.

`.env`에는 다음을 설정하고 `LENS_CASE_RETRIEVAL_PROFILE`은 비웁니다.

```dotenv
LENS_MYSQL_RELEASE=data/mysql-search/active.json
LENS_MYSQL_MODEL_DIR=data/models/kure-mysql
LENS_CASE_RETRIEVAL_PROFILE=
```

이제 [로컬 실행 안내](local-setup.md#3-환경변수와-모델)로 이동합니다. 배포본 설치 후
`setup_data.py`를 실행하지 않습니다. 이후 패키지를 추가할 때도
`-c tmp/requirements-search.txt` 제약 파일을 적용합니다.

## 설치 확인용 단일 질의

```powershell
python -m src.ingestion.mysql_search query `
  --release data/mysql-search/active.json `
  --model-dir data/models/kure-mysql `
  --question "전입신고와 확정일자는 어떤 차이가 있나요?" `
  --output tmp/mysql-evidence.json
```

이 명령은 검색 근거만 확인하며 Ollama나 Django를 호출하지 않습니다. 실제 답변 연결은
Django 실행 후 확인합니다.

## 담당자: 배포본 생성

MySQL 적재·스냅샷·원문 갱신은 [`mysql-data.md`](mysql-data.md)와 [`mysql-ingest.md`](mysql-ingest.md)를
먼저 확인합니다. 기본·민법·판례 export가 준비되면 다음 명령으로 새 배포본을 만듭니다.

```powershell
python -m src.ingestion.mysql_search build `
  --export-root EXPORT_ROOT `
  --output data/mysql-search/releases/VERSION `
  --model-dir MODEL_DIR `
  --case-release data/case_corpus/release.json
```

기존 release를 기준으로 갱신할 때는 `--case-release` 대신 `--previous-release`를 사용합니다.
완성된 폴더를 덮어쓰지 않으며, 현재 확정 소스에서 `verify`·`model`·`activate`와 앱 연결
확인을 끝낸 뒤에만 팀 공유 ZIP으로 포장합니다. 팀원에게 MySQL 계정이나 비밀번호를
전달하지 않습니다.

## 왜 같은 ZIP을 쓰나요?

배포본은 생성 당시 검색 코드·정책·의존성·원문/청크 해시·Chroma 벡터를 고정합니다.
CPU별 HNSW의 마지막 비트 차이는 기록·본문·메타데이터가 동일하고 기준 벡터와 `1e-6`
이내일 때만 허용합니다. 검색 코드, 메타데이터 타입, 임베딩 출처가 달라지면 검증을
통과시키지 않습니다. 따라서 개인 PC에서 임의로 재임베딩하거나 MySQL에 접속해 수정하지
않습니다.
