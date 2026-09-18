# MySQL 데이터로 기존 검색기 실행

MySQL은 원문·청크·버전을 관리한다. 앱 검색은 같은 MySQL 스냅샷에서 내보낸 JSONL과 Chroma를 함께 검증한 배포본을 읽는다. BM25는 배포본의 JSONL로 메모리에 구성한다. 검색할 때 기존 SQLite 원본이나 MySQL 접속 비밀번호가 필요하지 않다.

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

담당자가 만든 배포 폴더 전체와 동일한 검색 코드를 사용한다. 개인별로 데이터를 다시 수집하거나 임베딩할 필요는 없다. Python 환경은 프로젝트 `requirements.txt`를 설치한다. MySQL 적재·내보내기를 실행하는 담당자만 `requirements-mysql.txt`가 추가로 필요하다.

```powershell
python -m src.ingestion.mysql_search requirements --release data/mysql-search/releases/VERSION/release.json --output tmp/requirements-search.txt
python -m pip install -r requirements.txt -c tmp/requirements-search.txt
python -m src.ingestion.mysql_search verify --release data/mysql-search/releases/VERSION/release.json
python -m src.ingestion.mysql_search model --release data/mysql-search/releases/VERSION/release.json --model-dir data/models/kure-mysql
python -m src.ingestion.mysql_search activate --release data/mysql-search/releases/VERSION/release.json --pointer data/mysql-search/active.json
```

배포 시점의 Chroma·NumPy·Sentence Transformers·PyTorch·Transformers·Tokenizers 버전을 함께 고정한다. `requirements`는 배포 manifest 해시를 확인한 뒤 제약 파일을 생성하며, 검색 실행은 실제 설치 버전까지 대조한다.

`model` 명령은 고정 revision의 필요한 파일을 로컬 폴더로 다운로드하고 해시를 확인한다. 기존 파일이 다른 내용이면 덮어쓰지 않고 실패한다. 배포본 안에는 모델을 포함하지 않아 별도 모델 다운로드가 최초 한 번 필요하다.

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
