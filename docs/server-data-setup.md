# 로컬 검색 데이터 준비

이 문서는 **개발 PC에서 승인 원천으로 검색 데이터를 구축하는 방법**입니다. 전체 실행 순서는
[로컬 설치 안내](local-setup.md)에, EC2 검색 배포·갱신 절차는
[AWS 검색 데이터 안내](../deploy/aws/MYSQL-SEARCH.md)에 있습니다. 현재 소스와 호환되는
팀 검색 ZIP이 없어 로컬 첫 설치에는 이 원천 구축 방법을 사용합니다. 호환 ZIP을 전달받았다면
[`mysql-search.md`](mysql-search.md), 과거 평가 DB 복원은
[`local-retrieval-data.md`](local-retrieval-data.md)를 따릅니다.

## 1. 어떤 경로를 선택하나요?

| 경로 | 대상 | 결과 |
| --- | --- | --- |
| 로컬 원천 구축 | 개발 PC의 첫 설치자 | 승인 원천에서 SQLite·청크·Chroma 생성 |
| 팀 검색 배포본 | 현재 코드와 호환되는 ZIP을 받은 팀원 | 동일 release의 JSONL·Chroma·KURE 모델 사용, MySQL 접속 불필요 |
| 검증 DB 복원 | 과거 평가 재현 | 고정 평가 묶음 복원 전용, 일반 서버 설치 아님 |

두 검색 자료 경로를 동시에 설정하지 않습니다. 로컬 원천 구축 시 `.env`의
`LENS_MYSQL_RELEASE`와 `LENS_MYSQL_MODEL_DIR`을 비웁니다.

## 2. 준비

- Python 3.11 또는 3.12. 실행 인터프리터와 생성할 가상환경의 버전이 같아야 합니다.
  macOS·Linux와 일반 원천 구축은 3.11을 기본으로 합니다. Windows에서 Git LFS 판례
  release를 함께 검증하면 3.12를 사용합니다. 3.11 Windows에서 대형 Chroma 인덱스
  access violation이 재현되어 판례 release 검증은 3.12로 고정했습니다.
- 저장소 루트의 승인 원천 자료. 판례 Git release를 사용하려면 `git lfs install`과
  `git lfs pull`도 실행합니다.
- 최초 설치 시 패키지·KURE 모델을 받을 인터넷 연결.

검색 구축 후 [로컬 설치 안내](local-setup.md)에 따라 `.env`와
`DJANGO_SECRET_KEY`·업로드용 `LENS_FILE_ENCRYPTION_KEY`를 준비합니다. 기존 `.env`는
덮어쓰지 않습니다. 검색 데이터 준비 중에는 Django 서버를 종료합니다.

## 3. 처음 한 번 실행

저장소 루트에서 실행합니다. `setup_data.py`가 지정한 경로(기본 `.venv`)에 가상환경을 만들고
`requirements.txt`와 검증된 KURE 모델을 준비한 뒤 DB 구축을 진행합니다.

| 환경 | 명령 |
| --- | --- |
| Windows PowerShell | `py -3.12 setup_data.py --venv-dir C:\lens-venv-312` |
| macOS·Linux | `python3.11 setup_data.py` |

구축이 끝나면 Windows PowerShell은 `C:\lens-venv-312\Scripts\Activate.ps1`, macOS·Linux는
`source .venv/bin/activate`로 가상환경을 활성화합니다. 같은 터미널에서
[로컬 실행 단계](local-setup.md#3-환경변수와-모델)를 계속합니다.

Python 3.12를 사용할 때는 `py -3.12 setup_data.py --venv-dir C:\lens-venv-312`처럼
기존 3.11 환경과 다른 새 경로를 지정합니다. 한 가상환경을 두 Python 버전이 함께
사용하지 않습니다. Windows 판례 release 검증의 상세 결과는
[`case-git-release.md`](case-git-release.md)를 참고합니다.

실행 흐름은 다음과 같습니다.

```text
환경·의존성·KURE 확인
→ [1/3] 승인 원천과 기존 구축 상태 확인
→ [2/3] SQLite·청크·일반/민법 Chroma 구축
→ Git 판례 release 검증·프로필 생성(있는 경우)
→ [3/3] 중복·인덱스·기본 검색 확인 후 적용
```

## 4. 구축 범위와 생성 파일

현재 원천 구축은 저장소에 보존한 승인 입력을 사용하며 최신 법령을 자동 수집·채택하지
않습니다. 기본 범위는 다음과 같습니다.

| 자료 | 범위 |
| --- | ---: |
| 일반 법령·시행령 | 185개 조문 |
| 민법 전용 채널 | 31개 조문 |
| 기본 판례 시드 | 28건 |
| 안내·공식 서식 | 3문서·10청크 |

판례 Git release가 있으면 `data/case_corpus/release.json`을 자동으로 확인합니다. release의
기본 판례 8,377건에 승인 보완 2건을 연결해 판례 검색 경로를 활성화합니다. 기본 26건
시드는 원래 release에 합치지 않습니다. release가 LFS 포인터만 있거나 파일 해시가 다르면
조용한 대체 없이 설치를 중단합니다.

주요 산출물은 다음과 같습니다.

```text
data/database/knowledge.sqlite3       공식 법령·판례·안내 관계 DB
data/database/civil.sqlite3           민법 전용 DB
data/chunks/                          청크 JSONL
data/index/                           일반·민법 Chroma 인덱스
data/parsed/server-build/             이번 구축의 파싱 결과
data/index/server-build.json          구축 입력·코드·모델·패키지 기록
data/case_corpus/runtime-profile.json 판례 release 런타임 프로필(있는 경우)
tmp/server-build/                     진행 로그·백업·실패 진단
```

웹 회원·계정·대화 DB인 `data/database/web.sqlite3`와 `data/eval`은 교체 대상이 아닙니다.

## 5. 점검·재실행 옵션

```bash
python3.11 setup_data.py --check
python3.11 setup_data.py --prepare-only
python3.11 setup_data.py --rebuild
python3.11 setup_data.py --data-root /path/to/data-root
```

- `--check`: 다운로드·DB 변경 없이 가상환경, 패키지, KURE, 판례 release를 확인합니다.
- `--prepare-only`: 환경·모델만 준비하고 DB를 바꾸지 않습니다.
- `--rebuild`: 다른 도구가 만든 기존 DB를 백업한 뒤 승인 원천부터 전환할 때만 사용합니다.
- `--data-root`: 별도 데이터 폴더를 대상으로 구축합니다. 데이터 폴더는 저장소의 `tmp`와 같은 파일시스템(드라이브·마운트)에 있어야 합니다. 다른 파일시스템에서는 구축 후 적용 단계의 폴더 이동이 실패합니다. 이 옵션만으로 웹 앱의 검색 경로는 바뀌지 않습니다.

같은 원천·코드·모델·패키지라면 검사를 통과한 기존 결과를 재사용합니다. 승인 입력이나
임베딩 파이프라인이 바뀌면 변경 청크만 재임베딩할 수 있으며, 모델·인덱스 버전이 다르면
벡터를 재사용하지 않습니다. 적용 전 기존 자료를 백업하고 적용 후 다시 검사하며, 실패하면
기존 자료를 유지하거나 백업으로 되돌립니다. 전체 235문항 평가나 LLM 답변 평가는 설치 때
자동으로 실행하지 않습니다.

환경 준비가 끝난 뒤 데이터만 갱신하려면 다음 관리 명령을 사용할 수 있습니다.

```bash
python manage.py prepare_retrieval
python manage.py prepare_retrieval --rebuild
```

이 명령은 이미 준비된 현재 Python 환경을 사용합니다. 웹 요청이나 Django 시작 때마다
무거운 구축을 자동 실행하지 않으며, 갱신 후에는 앱을 재시작합니다.

## 6. 동시 실행과 복원

설치·검증 잠금은 체크아웃 폴더가 아니라 실제 대상 데이터 폴더의
`.retrieval-data.lock`에 걸립니다. 같은 `--data-root`를 여러 체크아웃에서 동시에 수정하지
않습니다. 이전 설치·복구 프로세스를 종료한 뒤 다시 실행합니다.

고정 평가 DB가 필요할 때만 다음 경로를 사용합니다.

```bash
python3.11 setup_data.py --validation-bundle --source /path/to/extracted/data
```

이 경로는 평가 파일 해시와 동일한 묶음을 확인하는 용도이며, 원천 기반으로 새로 만든
서버 DB가 그 해시와 같아야 한다는 뜻은 아닙니다.
