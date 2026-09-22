# LENS AWS 서울 배포

**최신 운영 상태 (2026-09-23):** 수정된 `patch060-r2-20260921` 데이터를 반영하여 EC2 검색 설정을 전환했습니다. 공개 HTTPS와 검색 준비 상태를 확인했습니다. 기존 RunPod SSH 연결은 실패하여 현재 AI 답변 생성은 검증하지 못했습니다. 사용자 요청에 따라 RunPod 재연결과 실제 생성 검증은 AWS 운영 설정 공유 이후의 별도 후속 작업으로 남깁니다.

계정 `514644129560`, 서울 리전 `ap-northeast-2`.
현재 앱 소스 커밋: `40ac9d706c4959ab14045349d47ecab55ad228a7`.
2026-09-23 달력 선택·방 삭제 UI를 갱신했고 관련 테스트 31개를 통과했습니다.
최초 배포 커밋은 `117445d7edbdc8a2678e005bc596fa877fdd80b6`입니다.

사용자가 최종 선택한 구성은 **EC2 + systemd/Gunicorn + Nginx + CloudFront HTTPS**입니다.
Docker Compose·Let’s Encrypt·GitHub Actions는 이 배포에 적용하지 않았습니다.
Docker/Certbot 패키지는 이전 구성 준비 중 설치됐지만 Docker 서비스는 중지·비활성화했고
컨테이너·인증서 갱신 작업은 생성하지 않았습니다.

- 접속 주소: https://d3kro62a2qvg04.cloudfront.net
- EC2: `i-091748b7dfcea398e` (`m7i-flex.large`, 2 vCPU, 8 GB)
- 암호화 gp3 EBS: `vol-0adfa689791285be3`, 60 GB, 종료 시 자동 삭제하지 않음
- 고정 IP: `54.116.179.166` (`eipalloc-00eac95a95e680b5d`)
- 보안 그룹: `sg-08f088a33a7ceb165`
- CloudFront: `E1U82EYWALD05C`, 캐싱 비활성화, HTTP→HTTPS 리다이렉트
- 월 예산: `LENS-monthly-30USD`, $30 초과 시 사용자 지정 이메일 알림
- AWS CLI 프로필: `lens-deploy` (기존 교육용 `skn33`는 보존)
- EBS DLM: `policy-0d5e3ce7bcdd03093`, 매일 한국시간 03:00 기준, 최근 7개 보관
- SQLite 사본: `lens-sqlite-backup.timer`, 매일 한국시간 02:30, 백업 후 무결성 검사

현재 서버 갱신은 [UPDATE.md](UPDATE.md), 백업과 복원은 [BACKUP.md](BACKUP.md),
팀 설명 자료는 [공유 문서](../../docs/deployment/aws-runpod-team-guide.md)를 참고하세요.
`lens.env.example`, `runpod-tunnel.env.example`, `nginx.conf.example`에는 예시 값만 있습니다.
실제 값은 `/etc/lens`와 Nginx 설정에만 저장하며 Git에 올리지 않습니다.

2026-09-22의 기존 배포에서 웹·인증·PDF 및 이미지 OCR 업로드 검증은 통과했습니다. 검색 인덱스 구축과
법령·민법·판례·가이드 검색 검증도 완료했습니다. RunPod A40의
`qwen3.8:27b`를 연결했고 실제 웹 질문에서 답변과 출처 반환을 확인했습니다.
연결 정보와 운영 방법은 [RUNPOD.md](RUNPOD.md)를 참고하세요.

## 서버 경로와 설정

- Application checkout: `/opt/lens/app`, owned by the `lens` service user.
- Python 3.11.16 virtual environment: `/opt/lens/venv311`.
- Managed Python runtime: `/opt/lens/python`; Ubuntu system Python is unchanged.
- Previous Python 3.12 environment retained for rollback: `/opt/lens/venv`.
- Environment file: `/etc/lens/lens.env`, mode 0600, read by systemd.
- Model cache: `/opt/lens/cache/huggingface`.
- Persistent application data: `/opt/lens/app/data`; back up this directory and
  the separately stored encryption key before replacing the server.
- Create `/opt/lens/app/tmp` and `/opt/lens/cache` before starting the service.

Python 3.11 가상환경·CPU PyTorch·한국어 Tesseract·Git LFS를 사용합니다.
검증된 EC2 패키지 버전은 `constraints-python311.txt`에 기록했습니다.
초기 설치 시 이 제약 파일을 함께 전달하고 CPU PyTorch 인덱스를 사용합니다.
다른 OS·Python 버전에서 그대로 동작한다는 보장은 없으며, 검색 release의 runtime 검증과 함께 확인합니다.
초기에는 `setup_data.py`로 검색 자료를 구축했습니다. 2026-09-23에는 MySQL의
`patch060-r2-20260921` export와 모델을 검증해 서버용 검색 배포본으로 전환했습니다.
`LENS_MYSQL_RELEASE=/opt/lens/app/data/mysql-search/active.json`,
`LENS_MYSQL_MODEL_DIR=/opt/lens/app/data/models/kure-mysql`를 사용합니다.
현재 데이터·재배포·되돌리기 절차는 [MYSQL-SEARCH.md](MYSQL-SEARCH.md)를 참고하세요.
`install-server.sh`는 초기 구축용 참고 자료이며 현재 데이터 갱신용으로 재실행하지 않습니다.
이 스크립트 실행 전 Ubuntu 패키지(Nginx, Git LFS, Python venv, Tesseract와 kor/eng,
Nanum 한글 글꼴), `lens` 계정, `/opt/lens` 및 `/etc/lens` 경로와 업로드할 설정 파일을 준비해야 합니다.
EC2·보안 그룹·CloudFront 생성까지 자동으로 수행하는 스크립트는 아닙니다.
초기 원천 데이터 구축 후 MySQL 배포본 전환은 `MYSQL-SEARCH.md`의 별도 단계입니다.
서버가 생성한 키와 `/etc/lens/lens.env`는 Git에 올리지 않습니다.

앱은 `config.production`을 사용합니다. Gunicorn은 `127.0.0.1:8000`에서만
수신하며 모델 중복 적재를 피하기 위해 워커 1개·스레드 4개로 실행합니다.
보안 그룹은 CloudFront 원본 IP 대역에서 오는 HTTP와 배포 PC의 SSH만 허용합니다.
Nginx는 추가로 비밀 원본 헤더를 검사하고 `/static/`만 공개 파일로 제공합니다.
CloudFront→Nginx는 HTTP, 사용자→CloudFront는 HTTPS입니다.

## 운영 명령

```bash
sudo systemctl status lens nginx
sudo journalctl -u lens -n 100 --no-pager
sudo systemctl restart lens
```

RunPod Pod `by86qm78z4aszh`의 Ollama `0.34.2`와 `qwen3.8:27b`를 사용합니다.
`lens-runpod-tunnel.service`가 EC2→RunPod 연결을 SSH로 암호화합니다.
앱의 `JEONSEON_LLM_BASE_URL=http://127.0.0.1:11434/v1`은 이 터널의
로컬 주소이며, 공개 HTTP 추론 API는 만들지 않았습니다.
환경 파일을 수정하면 `sudo systemctl restart lens` 후 실제 질문을 검증합니다.

CloudFront의 원본 응답 대기 시간은 120초, LLM 단일 호출 제한은 90초입니다.
질문이 복잡하거나 사용자가 동시에 접속하면 이 시간 제한에 도달할 수 있습니다.

## 중지와 비용

```powershell
aws ec2 stop-instances --profile lens-deploy --region ap-northeast-2 --instance-ids i-091748b7dfcea398e
aws ec2 start-instances --profile lens-deploy --region ap-northeast-2 --instance-ids i-091748b7dfcea398e
```

정상 요금 기준 EC2는 시간당 $0.11771입니다. 60 GB EBS·고정 IP를 포함한
12시간분은 약 $1.56이며 세금·크레딧·트래픽·RunPod는 제외합니다.
EC2를 중지해도 EBS·고정 IP 보관 비용은 남습니다. 예산 알림은 자동 중지나
지출 상한이 아닙니다. 중지/시작 시 고정 IP와 EBS 데이터는 유지됩니다.
RunPod GPU는 EC2와 별도로 실행·과금되며 AWS 예산 알림에 포함되지 않습니다.
위 초기 비용 예시에는 이번에 추가한 EBS 스냅샷 저장 비용이 포함되지 않습니다.
실제 비용과 크레딧 적용 여부는 AWS Billing에서 확인합니다.

## 검증 범위

2026-09-22에 Python 3.11.16으로 전환한 후 아래 기능을 재검증했습니다.
실행 중인 Gunicorn 프로세스의 실제 인터프리터도 Python 3.11 경로로 확인했습니다.
패키지 의존성 검사(`pip check`)를 통과했고 DB·모델 캐시·검색 인덱스는 보존했습니다.

- HTTPS 홈, HTTP 리다이렉트, 정적 CSS: 통과
- 회원가입·로그인·로그아웃·Secure 쿠키: 통과
- 가상 계약서 PDF 업로드·분류·삭제: 통과
- 가상 계약서 PNG의 실제 한국어 OCR·분류·삭제: 통과
- 검증용 계정·문서 정리: 완료
- 판례 8,377건 배포본 DB·원문 해시·인덱스 무결성: 통과
- 법령·민법 인덱스 구축 및 법령·민법·판례·가이드 실제 검색: 통과
- 실제 LLM 생성: RunPod A40의 `qwen3.8:27b` GPU 추론 및 웹 답변·출처 확인

`check --deploy`의 HSTS 하위 도메인·preload 경고 2개는 남겨 두었습니다.
관리하지 않는 하위 도메인에 HTTPS 정책을 강제하거나 preload 등록을 선언하지 않습니다.
