# Django 서버 데이터 준비

**일반 설치에는 DB ZIP이 필요 없습니다.** 서버 관리자·개발자가 이 명령을 실행하고, 웹 이용자는 Django 사이트에 접속해 질문만 입력합니다. 설치 중에는 앱을 종료합니다.

## 한 번 실행

Python 3.11을 설치하고 저장소를 받은 뒤 저장소 루트에서 실행합니다.

| 환경 | 명령 |
| --- | --- |
| Windows PowerShell | `py -3.11 setup_data.py` |
| macOS 터미널 | `python3.11 setup_data.py` |
| Linux 로컬 터미널 | `python3.11 setup_data.py` |
| RunPod 터미널 | `python3.11 setup_data.py --venv-dir /opt/lens-venv` |

실행 순서:

```text
.venv 생성·필요 패키지 확인/설치·KURE 모델 준비
→ [1/3] 승인 원천 자료·현재 DB 확인
→ [2/3] 법령 파싱 → SQLite 저장 → 일반/민법 인덱스 구축
→ [3/3] 무결성·중복·기본 검색 확인 → 적용
```

기본 입력은 저장소에 보존한 **승인 원천 스냅샷**입니다. `data/sources/server-v1`의 법령 평문·공식 안내 수집 레코드, 기존 추가 법령 HTML, 판례26건 시드를 사용합니다. 완성된 DB나 임베딩 벡터를 복사하지 않습니다. 기존 판례 시드는 전문 전체를 새로 수집하는 과정이 아니며, 안내는 이미 수집한 원문 레코드를 청킹합니다.

PATCH-058 기준 기본 구축 범위는 일반 법령185개·민법31개·판례28개·안내/공식 서식10청크입니다. `data/sources/supplement-v1`과 `data/sources/retrieval-supplements-v1`의 공식 법령12조문·판례2건·금융정보 동의서를 추가 검증합니다. 설치할 때 최신 법령을 임의로 수집·채택하지 않습니다. 신규/개정 자료는 검토 후 원천·판본·선택 목록에 반영하고 다시 구축해야 합니다. 일부 법령의 원래 수집일은 확인되지 않아 빈 값으로 보존하며, 재구축 날짜를 수집일로 바꾸지 않습니다.

위 건수는 기본 원천 구축 레시피의 범위입니다. 추가 Git 판례 배포
`data/case_corpus/release.json`이 있으면 `setup_data.py`가 그 배포도 검증하고
8,377건 `data_dev_v2` 판례 프로필을 활성화합니다. PATCH-058 보완 판례2건은
이 검색기에 결합해 총8,379건을 검색합니다. 기본 판례 시드26건은 합치지 않으며
원래 LFS 파일은 유지합니다. 기본 법령·민법·안내는 유지합니다. 상세 저장·검증·재출력 조건은
[판례 Git 배포](case-git-release.md)를 따릅니다. 이번 Windows 추가 코퍼스
실행 시험은 별도 Python 3.12 환경을 사용합니다. 기존 3.11 가상환경을
덮어쓰지 않고 `py -3.12 setup_data.py --venv-dir C:\lens-venv-312`처럼 새 경로를
지정합니다. Python 3.11/3.12 중 설치 실행 버전과 가상환경 버전이 같아야 합니다.

기존 DB의 법령·민법 본문과 논리 조문 ID는 유지되지만, 예전 적재기의 순번이 들어간 내부 청크 ID는 현재 적재기의 조문별 ID로 달라질 수 있습니다. 기존 캡처의 청크 ID·벡터 파일 해시가 완전히 같아야 하는 시험은 검증 DB 복원 경로를 사용합니다.

구축·검증 중 모델 로딩·임베딩 진행·경고를 터미널에 바로 표시하고, 같은 내용을 `tmp/server-build/<실행 ID>/build.log`·`verify.log`에도 저장합니다. 오류로 끝나면 로그 경로와 실패를 반환합니다.

## 재실행과 변경 반영

- 원천·구축 코드·모델이 같고 DB/인덱스 검사를 통과하면 **재구축·재임베딩을 생략**합니다.
- 승인 입력이 바뀌면 별도 작업 폴더에서 SQLite와 청크를 다시 생성합니다. 동일 모델·임베딩/인덱스 파이프라인의 기존 벡터를 재사용하고, 새 청크·본문이 바뀐 청크만 임베딩합니다. 메타데이터만 바뀌면 벡터를 재계산하지 않습니다.
- 모델 버전이나 임베딩/인덱스 파이프라인 해시가 다르면 벡터를 재사용하지 않습니다. `dense.py`, `index.py`, 구축 모듈 전체와 `requirements.txt`를 보수적으로 비교하므로 구축 모듈의 로그 변경만으로도 재임베딩할 수 있습니다. 실제로 설치된 torch·sentence-transformers·transformers·tokenizers·chromadb·numpy·scipy·scikit-learn·safetensors·huggingface-hub 버전도 비교합니다. 파일이 같아도 패키지 버전이 바뀌면 재임베딩합니다. 이전 기록에 이 정보가 없으면 최초 갱신 시 전체 재임베딩합니다. KURE는 검증한 모델 파일을 확인합니다.
- 구축 변경 감지에는 `src/database/schema.sql`도 포함합니다. 스키마만 바뀌면 DB를 다시 생성하고 호환되는 벡터는 재사용할 수 있습니다.
- 적용 전 검증하고 기존 파일을 백업합니다. 적용 후 검사/기록 저장 실패 시 기존 파일로 복구합니다. `web.sqlite3`의 계정·대화 데이터와 `data/eval`은 교체 대상이 아닙니다. Ctrl+C 중단도 작업 프로세스를 종료한 뒤 복구하고 중단을 다시 전달합니다. 강제 프로세스 종료·전원 차단까지 자동 복구를 보장하는 기능은 아닙니다.
- 다른 도구가 만든 기존 DB는 기본 실행으로 덮어쓰지 않습니다. 원천부터 전환하기로 했다면 `python3.11 setup_data.py --rebuild`(Windows는 `py -3.11`)로 백업 후 구축합니다.
- 이 확인은 조문 수·중복·인덱스 내용·검색 채널 연결 확인입니다. **235문항 전체 평가나 LLM 답변 평가는 설치할 때마다 실행하지 않습니다.** 재생성한 벡터의 순위가 기존 평가와 완전히 같다고 주장하지 않습니다.

## 환경 준비가 끝났다면 Django 관리 명령

`.env`와 Django 키를 README6.2대로 준비하고 가상환경을 활성화한 뒤 실행합니다.

```bash
python manage.py prepare_retrieval
python manage.py migrate
python manage.py check
python manage.py runserver 127.0.0.1:8000
```

원천부터 명시적으로 다시 구축하려면 `python manage.py prepare_retrieval --rebuild`입니다. 데이터 준비는 웹 요청이나 Django 시작 시마다 자동 실행하지 않습니다. 갱신 후 앱 프로세스를 재시작해 검색 캐시가 새 데이터를 읽게 합니다.

`--data-root`는 별도 데이터 폴더의 구축·검증 옵션입니다. 이 옵션만으로 웹 앱의 검색 경로까지 바뀌지는 않으므로 일반 설치는 기본 `data` 경로를 사용합니다.

## RunPod에서 낭비 줄이기

저장소의 `data`, `tmp/server-build`와 모델 캐시는 영구 볼륨에 유지하고, **가상환경은 컨테이너 내부 디스크에 둡니다.** 네트워크 볼륨의 `.venv`에 많은 Python/CUDA 패키지 파일을 설치하는 지연을 줄이기 위한 배치입니다. 속도 개선은 아직 실측하지 않았습니다. 아래 `/workspace`와 `/opt`는 예시이므로 실제 Pod의 마운트 위치·권한·여유 공간을 확인하세요.

`--venv-dir`는 가상환경 생성·패키지 설치·모델 준비·DB 실행에 쓸 Python 위치만 선택합니다. DB·모델 저장 위치를 옮기거나 기존 `.venv`를 복사/삭제하지 않습니다. 옵션을 생략하면 Windows·Mac·Linux 모두 기존 프로젝트 `.venv`를 사용합니다. 상대 경로는 프로젝트 루트 기준이며, 새 경로나 기존 가상환경을 지정합니다. 일반 파일이 들어 있는 다른 디렉터리는 거부합니다.

현재 데이터 교체는 같은 파일시스템의 이름 변경을 사용하므로 프로젝트의 `data`와 `tmp`를 같은 볼륨에 둡니다. 일반 실행에서 손상된 기존 자료는 중단하지만, 명시적 `--rebuild`에서는 검사 실패 시 기존 벡터 재사용을 포기하고 승인 원천부터 새로 구축합니다. 검증된 새 자료가 준비된 뒤 남아 있는 기존 파일을 백업·교체하며, 구축 실패 시 기존 자료를 유지하고 적용 후 실패 시 백업으로 되돌립니다. 실패 로그는 `tmp/server-build/errors`에 보존합니다.

```bash
cd /workspace/LENS_4th_project
export HF_HOME=/workspace/huggingface
python3.11 setup_data.py --venv-dir /opt/lens-venv
source /opt/lens-venv/bin/activate
# .env의 Django 키·LLM 주소를 준비한 뒤 실행
python manage.py migrate
python manage.py check
```

setup에서 이미 DB를 구축했으므로 바로 `prepare_retrieval`을 반복할 필요는 없습니다. 이후 데이터만 확인/갱신할 때는 같은 가상환경에서 `python manage.py prepare_retrieval`을 실행합니다.

새 터미널에서는 `export HF_HOME=/workspace/huggingface`와 `source /opt/lens-venv/bin/activate`를 다시 적용합니다. setup을 다시 실행할 때도 **동일한 `--venv-dir /opt/lens-venv`를 반드시 지정**합니다. 활성화된 환경만으로 setup의 기본 `.venv` 위치가 바뀌지는 않습니다. 환경 확인만 하려면 `python3.11 setup_data.py --venv-dir /opt/lens-venv --check`입니다.

컨테이너가 교체·초기화되면 내부 디스크의 가상환경·기본 pip 캐시는 사라질 수 있으므로 환경 설치를 다시 해야 합니다. 영구 볼륨의 DB와 `HF_HOME` 캐시는 별도로 유지하며, 네트워크 볼륨 자체의 보존 정책도 확인합니다. `/opt`의 쓰기 권한이 없으면 쓰기 가능한 내부 디스크 경로를 지정합니다. 가상환경을 외부 디스크로 지정해도 DB 교체용 `data`와 `tmp`는 같은 파일시스템에 둬야 합니다.

이미 진행 중인 설치는 이 옵션으로 자동 전환되지 않습니다. 설치 도중 같은 저장소에서 setup을 추가 실행하지 마세요. 현재 설치를 종료/완료한 뒤 새 경로를 선택하면 패키지는 새 환경에 다시 설치되고, 사용 가능한 pip 다운로드 캐시는 재사용될 수 있습니다. 기존 `.venv`는 그대로 남습니다. 이 변경은 패키지 목록을 축소하거나 CUDA/GPU 호환성을 자동 보장하지 않습니다.

`HF_HOME` 설정은 이후 Django 실행 프로세스에도 동일하게 적용합니다. 백업은 실패 복구용으로 남으므로 검증이 끝난 오래된 실행 폴더의 보관 여부는 관리자가 결정합니다.

LLM(Ollama/RunPod 엔드포인트) 설정은 README6.3을 따릅니다. 서버 외부 접속에는 실제 호스트에 맞는 Django 호스트·CSRF·프록시 설정이 별도로 필요합니다. 위 명령만으로 외부 배포가 완료되지는 않습니다.

**macOS·RunPod 설치 확인을 완료했습니다.** 이전 RunPod는 `/opt/lens-venv` 환경에서 KURE 검증·DB 구축·적용·기본 검색 확인까지 완료했고, 일반178·민법26·판례26·안내6청크, 최초 임베딩210+26개가 완료 로그에 기록됐습니다. 당시에는 CUDA 초기화 경고가 있어 GPU 정상 작동을 확인하지 못했습니다.

2026-09-15에는 새 RTX A4000 Pod에서 main `b3cb8ac`의 `setup_data.py --venv-dir /opt/lens-venv --prepare-only`를 실행했습니다. Python3.11.15·torch2.14.0+cu130·드라이버595.91.07에서 실제 CUDA 행렬 연산과 KURE 기본/명시적 GPU 선택·임베딩이 모두 통과했습니다. 현재 확인한 Pod에는 추가 CUDA 수정이 필요하지 않습니다. 과거 오류의 원인은 미확정이며 PATCH-029 CUDA12.8 후보를 적용하거나 검증한 결과는 아닙니다. 새 Pod의 DB 구축·검색 재평가·LLM 검증과 설치 속도 비교는 미실시입니다. [상세 실측 기록](patch040-runpod-gpu-verification.md)을 참고하세요.

## 검증된 DB를 그대로 복원할 때만

과거 평가 DB와 동일한 묶음이 필요하면 [검증 DB 복원 안내](local-retrieval-data.md)를 따라 별도 체크아웃의 빈 DB에 설치합니다. 이미 원천 방식으로 구축한 현재 DB를 검증 묶음으로 직접 전환하는 기능은 지원하지 않으며 기존 `data`를 삭제할 필요는 없습니다.

```bash
python3.11 setup_data.py --validation-bundle --source '/path/to/extracted/data'
```

이 경로는 고정된235입력 재현까지 검사하는 별도 검증용입니다. 일반 서버 구축으로 새로 생성한 DB는 그 고정 파일 해시와 같을 필요가 없습니다.


### 공용 DB 경로의 동시 실행 방지

잠금은 체크아웃 위치가 아니라 실제 대상 DB 폴더의 `.retrieval-data.lock`에 걸립니다. 서로 다른 체크아웃에서 같은 `--data-root`를 지정하거나 기본 설치·검증 도구를 함께 실행해도 같은 대상은 동시에 수정하지 않습니다. 다른 대상 DB는 별도 잠금을 사용합니다. 잠금 파일은 교체 대상에서 제외하고 삭제하지 않으며, 프로세스가 종료되면 OS 잠금이 해제됩니다. 이전 버전의 설치/복구 작업은 모두 종료한 뒤 갱신된 도구를 실행하세요.
