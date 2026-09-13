# Django 서버 데이터 준비

**일반 설치에는 DB ZIP이 필요 없습니다.** 서버 관리자·개발자가 이 명령을 실행하고, 웹 이용자는 Django 사이트에 접속해 질문만 입력합니다. 설치 중에는 앱을 종료합니다.

## 한 번 실행

Python 3.11을 설치하고 저장소를 받은 뒤 저장소 루트에서 실행합니다.

| 환경 | 명령 |
| --- | --- |
| Windows PowerShell | `py -3.11 setup_data.py` |
| macOS 터미널 | `python3.11 setup_data.py` |
| Linux / RunPod 터미널 | `python3.11 setup_data.py` |

실행 순서:

```text
.venv 생성·필요 패키지 확인/설치·KURE 모델 준비
→ [1/3] 승인 원천 자료·현재 DB 확인
→ [2/3] 법령 파싱 → SQLite 저장 → 일반/민법 인덱스 구축
→ [3/3] 무결성·중복·기본 검색 확인 → 적용
```

기본 입력은 저장소에 보존한 **승인 원천 스냅샷**입니다. `data/sources/server-v1`의 법령 평문·공식 안내 수집 레코드, 기존 추가 법령 HTML, 판례26건 시드를 사용합니다. 완성된 DB나 임베딩 벡터를 복사하지 않습니다. 기존 판례 시드는 전문 전체를 새로 수집하는 과정이 아니며, 안내는 이미 수집한 원문 레코드를 청킹합니다.

현재 범위는 일반 법령178개·민법26개·판례26개·안내6청크입니다. 설치할 때 최신 법령을 임의로 수집·채택하지 않습니다. 신규/개정 자료는 검토 후 원천·판본·선택 목록에 반영하고 다시 구축해야 합니다. 일부 법령의 원래 수집일은 확인되지 않아 빈 값으로 보존하며, 재구축 날짜를 수집일로 바꾸지 않습니다.

기존 DB의 법령·민법 본문과 논리 조문 ID는 유지되지만, 예전 적재기의 순번이 들어간 내부 청크 ID는 현재 적재기의 조문별 ID로 달라질 수 있습니다. 기존 캡처의 청크 ID·벡터 파일 해시가 완전히 같아야 하는 시험은 검증 DB 복원 경로를 사용합니다.

## 재실행과 변경 반영

- 원천·구축 코드·모델이 같고 DB/인덱스 검사를 통과하면 **재구축·재임베딩을 생략**합니다.
- 승인 입력이 바뀌면 별도 작업 폴더에서 SQLite와 청크를 다시 생성합니다. 동일 모델의 기존 벡터를 재사용하고, 새 청크·본문이 바뀐 청크만 임베딩합니다. 메타데이터만 바뀌면 벡터를 재계산하지 않습니다.
- 모델 버전이 달라지면 기존 벡터를 재사용하지 않습니다. KURE는 현재 검증한 모델 버전의 파일을 확인합니다.
- 적용 전 검증하고 기존 파일을 백업합니다. 적용 후 검사/기록 저장 실패 시 기존 파일로 복구합니다. `web.sqlite3`의 계정·대화 데이터와 `data/eval`은 교체 대상이 아닙니다.
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

저장소를 영구 볼륨에 두고 그 아래의 `.venv`, `data`, `tmp/server-build`를 유지합니다. 모델 캐시도 영구 경로를 사용합니다. 아래 `/workspace`는 예시이므로 실제 Pod의 영구 볼륨 위치를 확인하세요.

```bash
cd /workspace/LENS_4th_project
export HF_HOME=/workspace/huggingface
python3.11 setup_data.py
source .venv/bin/activate
python manage.py prepare_retrieval
python manage.py migrate
python manage.py check
```

`HF_HOME` 설정은 이후 Django 실행 프로세스에도 동일하게 적용합니다. 모델 파일·이미 만든 DB를 유지하면 Pod 재시작마다 다운로드·전체 임베딩이 반복되지 않습니다. 백업은 실패 복구용으로 남으므로 검증이 끝난 오래된 실행 폴더의 보관 여부는 관리자가 결정합니다.

LLM(Ollama/RunPod 엔드포인트) 설정은 README6.3을 따릅니다. 서버 외부 접속에는 실제 호스트에 맞는 Django 호스트·CSRF·프록시 설정이 별도로 필요합니다. 위 명령만으로 외부 배포가 완료되지는 않습니다.

Windows 외에 macOS/RunPod 실제 기동·GPU 결과는 아직 검증하지 않았습니다. 원천 자료와 검색 정책은 같아도 OS·패키지·장치 차이로 검색 순위가 달라질 수 있습니다.

## 검증된 DB를 그대로 복원할 때만

과거 평가 DB와 동일한 묶음을 복원할 때만 [검증 DB 복원 안내](local-retrieval-data.md)를 사용합니다.

```bash
python3.11 setup_data.py --validation-bundle --source '/path/to/extracted/data'
```

이 경로는 고정된235입력 재현까지 검사하는 별도 검증용입니다. 일반 서버 구축으로 새로 생성한 DB는 그 고정 파일 해시와 같을 필요가 없습니다.
