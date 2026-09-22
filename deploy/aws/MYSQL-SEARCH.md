# MySQL 검색 데이터의 EC2 전환

**최신 운영 상태 (2026-09-23):** 수정된 `patch060-r2-20260921` 데이터를 반영하여 EC2 검색 설정을 전환했습니다. 공개 HTTPS와 검색 준비 상태를 확인했습니다. 기존 RunPod SSH 연결은 실패하여 현재 AI 답변 생성은 검증하지 못했습니다. 사용자 요청에 따라 RunPod 재연결과 실제 생성 검증은 AWS 운영 설정 공유 이후의 별도 후속 작업으로 남깁니다.

기준일: 2026-09-23. 이 전환은 기존 EC2에 검색 배포본을 설치하는 작업이며,
새 EC2나 MySQL 서버를 생성하는 작업은 아닙니다.

## 데이터 기준

사용자가 data 폴더에 추가한 배포본 `patch060-r2-20260921`의 MySQL 스냅샷을 사용합니다.
전체 MySQL의 가장 최근 모든 자료를 자동 선택하거나 동기화하는 방식은 아닙니다.

- 원본 release ID: `5b1a63a7b004f63ad2dae018d53b9e4e0cdd120a61d623ec3270174c53622703`
- 기본 스냅샷: `0c313cf1fe641db76994c126ab7d7e7f97cb4c181275170e48e8a133a548a764`
- 민법 스냅샷: `3880f3509c68f3c24a08497fee10913fd25359c8f549e6f780a508da0fff8e06`
- 판례 스냅샷: `bb0244db08f107fac78bba53ebe3f931107c52dd12b899c4718cc9f52b0054a2`

공용 MySQL의 세 스냅샷을 읽기 전용으로 검증했고, 로컬 배포본의 원문·청크·메타데이터 manifest와 일치했습니다.
로컬 수정본의 배포 파일 29개와 모델 파일도 manifest의 SHA-256과 일치했습니다.
로컬 `data/mysql-search/active.json`은 기존 `shared-v3` 그대로 보존하며,
EC2에만 새 활성 포인터를 생성합니다.

| 채널 | 실제 검색 자료 |
|---|---|
| 일반 법령 | 185개 청크 |
| 민법 | 31개 청크, 전용 인덱스 사용 |
| 기관 안내 | 10개 청크 |
| 판례 코퍼스 | 판례 8,377건. 해당 코퍼스의 전체 색인은 과거 법령·안내 포함 8,516개 청크 |

기본 스냅샷의 법령 216개에는 민법 31개가 포함되므로 검색 시 민법을 분리해 중복을 방지합니다.
기본 인덱스의 총 223개는 일반 법령 185 + 시드·보충 판례 28 + 안내 10입니다.

## 배포본을 새로 만든 이유

기존 `patch060-r2-20260921`는 이전 검색 코드와 `sentence-transformers==6.0.1`,
`torch==2.14.0` 등에 맞춰 만들어졌습니다. 현재 서버의 코드·패키지 해시가 달라
기존 manifest를 그대로 활성화할 수 없습니다.

기존 검증을 우회하거나 manifest의 해시만 고치지 않고, 프로젝트의 정식
`src.ingestion.mysql_search build` 명령으로 EC2용 배포본을 생성했습니다.
MySQL에서 내보낸 JSONL과 manifest는 그대로 사용하고, 호환되는 기존 판례
인덱스는 검증 후 재사용했으며 기본·민법 인덱스를 새로 구축했습니다.

새 release ID와 Chroma 파일 바이트는 원본과 달라질 수 있습니다.
동일성의 기준은 세 스냅샷 ID, 모든 export 파일 해시, 모델 파일 해시입니다.
검색 코드·라이브러리 차이에 따른 순위나 부동소수점 점수까지 동일하다고 보장하지 않습니다.

## EC2 경로와 설정

```dotenv
LENS_MYSQL_RELEASE=/opt/lens/app/data/mysql-search/active.json
LENS_MYSQL_MODEL_DIR=/opt/lens/app/data/models/kure-mysql
LENS_CASE_RETRIEVAL_PROFILE=
```

- EC2 배포본: `/opt/lens/app/data/mysql-search/releases/patch060-r2-ec2-py311-20260923/`
- 검색 모델: `/opt/lens/app/data/models/kure-mysql/`
- 기존 검색 자료: 삭제하지 않고 `/opt/lens/app/data`에 보존
- 전환 전 환경 백업: `/etc/lens/lens.env.before-mysql-20260923` (0600)
- 원본 export 검증 기록: `/opt/lens/app/tmp/mysql-r2-source-verification.json`
- MySQL 직접 대조 기록: `/opt/lens/app/tmp/mysql-r2-live-snapshot-verification.json`
- 새 배포본 동일성 기록: `/opt/lens/app/tmp/mysql-r2-release-build-verification.json`
- 앱 검색·근거 검증 기록: `/opt/lens/app/tmp/mysql-r2-retrieval-verification.json`

회원·대화용 Django SQLite는 그대로 사용합니다. 앱 검색은 MySQL에 실시간으로
접속하지 않고, 검증한 JSONL·Chroma 배포본을 읽습니다. MySQL 비밀번호는
이번 작업으로 EC2에 복사하지 않습니다.

## 적용 순서

1. 공용 MySQL 스냅샷과 로컬 export를 대조하고 전송 ZIP의 SHA-256을 확인합니다.
2. EC2에서 현재 코드·Python 3.11·패키지 환경으로 새 배포본을 build합니다.
3. `verify`와 모든 export 해시 대조를 통과한 뒤 실제 검색을 수행합니다.
4. 검색 근거의 `snapshot_id`, `chunk_id`, 본문이 MySQL export와 일치하는지 확인합니다.
5. `activate`로 서버 전용 활성 포인터를 만들고 환경 설정을 교체합니다.
6. Django를 재시작하고 공개 HTTPS의 검색 준비·웹 기능을 검증합니다.
7. RunPod 연결이 가능할 때 실제 AI 답변 생성을 별도로 확인합니다.

## 확인 및 되돌리기

```bash
cd /opt/lens/app
sudo -u lens /opt/lens/venv311/bin/python -m src.ingestion.mysql_search verify \
  --release data/mysql-search/active.json
sudo systemctl status lens --no-pager
```

이번 전환을 되돌릴 때는 아래 전용 스크립트로 검색 설정 세 개만 복원하고 앱을 재시작합니다.
그 사이 바뀐 RunPod 접속값 등 다른 설정은 유지합니다.

```bash
sudo python3 /home/ubuntu/lens-deploy/switch-mysql-r2-search.py --rollback
sudo systemctl restart lens
```

MySQL 자료를 수정한 뒤에는 새 스냅샷 내보내기→검색 배포본 생성·검증→활성화→앱 재시작이 필요합니다.
MySQL의 변경이 현재 EC2에 자동 반영되지는 않습니다.

## 최종 검증 기록

- 활성 EC2 release ID: `7993b603342b6c9da0f8c8cf6245e41c21eef61676df9de02328fe33bb73ca62`
- MySQL 세 스냅샷·모든 export·모델 파일 동일성: 통과
- 실제 검색 4개 질문: 법령·민법·판례·안내 근거의 snapshot ID, chunk ID, 본문 일치
- 실제 Gunicorn 프로세스의 MySQL 설정 및 Python 3.11 실행 경로: 확인
- 공개 HTTPS·정적 파일·검색 준비 API: 통과
- RunPod 연결 실패로 이번 데이터 반영 후 AI 생성은 미검증
- 환경 백업과 기존 검색 자료를 보존했으며 로컬 활성 포인터는 변경하지 않음

실제 서버 패키지 버전:

```json
{
  "chromadb": "1.5.9",
  "numpy": "1.26.4",
  "sentence-transformers": "6.1.0",
  "tokenizers": "0.23.2",
  "torch": "2.14.0+cpu",
  "transformers": "5.17.0"
}
```
