# 로컬 실행 안내

서비스에 접속할 수 없거나 개발 PC에서 별도로 실행할 때 사용합니다. 이 절차는 운영 서버의
검색 배포본·RunPod 연결을 복제하지 않습니다. 로컬 답변 생성에는 Ollama와
`qwen3.8:27b`가 필요합니다.

## 1. 준비

- Git, Git LFS, Ollama를 설치합니다.
- macOS·Linux는 Python 3.11을 사용합니다. Windows에서 판례 release까지 구축할 때는
  Python 3.12를 사용합니다.
- 스캔 PDF·이미지를 읽으려면 한국어 데이터가 있는 Tesseract가 필요합니다
  ([OCR 설치 안내](registry-check.md)).

저장소와 Git LFS 판례 자료를 받습니다.

```bash
git clone https://github.com/sji21/LENS_4th_project.git
cd LENS_4th_project
git lfs install
git lfs pull
```

## 2. 검색 데이터와 Python 환경 준비

승인 원천으로 SQLite·Chroma를 구축합니다. 아래 명령은 필요한 패키지와 KURE 모델을
준비하고 판례 release도 검증합니다. 구축 옵션·산출물은 [검색 데이터 안내](server-data-setup.md)에
있습니다.

| 환경 | 구축 명령 | 이어서 활성화 |
| --- | --- | --- |
| Windows PowerShell | `py -3.12 setup_data.py --venv-dir C:\lens-venv-312` | `C:\lens-venv-312\Scripts\Activate.ps1` |
| macOS·Linux | `python3.11 setup_data.py` | `source .venv/bin/activate` |

현재 코드와 호환되는 팀 검색 ZIP을 전달받았다면 원천 구축 대신
[배포본 설치 안내](mysql-search.md)를 사용합니다. 기존 r2 ZIP은 현재 코드와 호환되지 않으며,
팀원 검색에 MySQL 접속 정보는 필요하지 않습니다.

## 3. 환경변수와 모델

`.env`가 없을 때만 `.env.example`을 복사합니다. macOS·Linux는 `cp .env.example .env`,
Windows PowerShell은 `Copy-Item .env.example .env`를 사용합니다. 이미 있는 `.env`는
덮어쓰지 마세요.

활성화한 Python 환경에서 아래 명령의 출력값을 각각 `.env`의 `DJANGO_SECRET_KEY`,
`LENS_FILE_ENCRYPTION_KEY`에 넣습니다. 키를 저장소에 올리지 않습니다.

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

원천 구축을 사용한다면 `LENS_MYSQL_RELEASE`, `LENS_MYSQL_MODEL_DIR`,
`LENS_CASE_RETRIEVAL_PROFILE`은 비워 두고 `DJANGO_DEBUG=true`를 유지합니다.
호환 검색 ZIP을 설치했다면 [배포본 설치 안내](mysql-search.md)의 경로 설정을 따릅니다.
`.env.example`의 기본 `JEONSEON_LLM_BASE_URL`은 로컬 Ollama 주소입니다.

```bash
ollama pull qwen3.8:27b
```

Ollama가 실행 중이 아니라면 별도 터미널에서 `ollama serve`를 시작합니다.

## 4. Django 실행

검색 구축 또는 배포본 설치에 사용한 가상환경을 활성화한 상태에서 실행합니다.

```bash
python manage.py migrate
python manage.py check
python manage.py runserver 127.0.0.1:8000 --noreload
```

<http://127.0.0.1:8000>에서 질문하고 답변 상태·출처 링크를 확인합니다. 검색 모델은
첫 화면 뒤 준비되며 실패하면 화면에서 재시도할 수 있습니다. 코드·검색 데이터·`.env`를
바꾼 뒤에는 서버를 재시작합니다. 웹 구조와 API는 [Django 안내](django-web.md)에 있습니다.
