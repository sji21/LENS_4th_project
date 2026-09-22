# RunPod Ollama 연결

**2026-09-23 현재:** 기존 SSH 주소로 연결되지 않아 GPU 추론 연결을 재확인하지 못했습니다.
아래 구성과 답변 검증 결과는 2026-09-22의 성공 기록입니다. 현재 Pod의 SSH 연결 정보를
확인해 터널을 복구한 뒤 실제 답변 생성을 다시 검증해야 합니다. 사용자 요청에 따라 이 연결 작업은 PATCH-066의 AWS 운영 설정 공유 범위에서 제외하고 추후 진행합니다.

SSH 접속 대상과 포트는 이제 `/etc/lens/runpod-tunnel.env`에서 읽습니다.
[runpod-tunnel.env.example](runpod-tunnel.env.example)를 참고해 현재 콘솔의 직접 SSH
주소를 설정하세요. 파일 권한은 root:root, 0600입니다. 아래 IP·포트는 과거 연결 기록이며,
Pod 재배치 후 그대로 사용할 수 있다고 보장하지 않습니다.

- Pod: `by86qm78z4aszh`
- GPU: NVIDIA A40, 48 GB
- Ollama: `0.34.2`
- 모델: `qwen3.8:27b` (Q4_K_M)
- RunPod 직접 SSH: `69.30.85.16:22127`
- AWS의 앱 환경: Python 3.11.16

브라우저→CloudFront는 HTTPS, EC2→RunPod는 SSH로 암호화합니다.
Ollama는 RunPod의 `127.0.0.1:11434`에서만 수신합니다.
EC2의 `lens-runpod-tunnel.service`가 같은 로컬 포트를 RunPod로 전달하므로
앱 설정은 `JEONSEON_LLM_BASE_URL=http://127.0.0.1:11434/v1`을 사용합니다.
이 HTTP 구간은 각 서버의 루프백 내부이며 인터넷 구간은 SSH입니다.
별도의 공개 RunPod HTTP 포트나 인증 없는 추론 API를 만들지 않습니다.

EC2 전용 키는 `/etc/lens/tunnel/id_ed25519`에 0600으로 저장합니다.
RunPod의 해당 공개 키는 EC2 고정 IP만 허용하고, 목적지 포트는
`127.0.0.1:11434`로 제한하며 셸 명령·PTY·에이전트 전달을 차단합니다.
개인 SSH 비밀 키는 EC2나 RunPod에 복사하지 않았습니다.
서버 호스트 키를 확인한 뒤 `/etc/lens/tunnel/known_hosts`에 고정했습니다.

## 파일과 시작 방식

- 실행 파일과 라이브러리: `/workspace/lens/bin`, `/workspace/lens/lib`
- 모델: `/workspace/lens/models`
- 로그: `/workspace/lens/logs/ollama.log`
- 실행 및 장애 시 재시작: `/workspace/lens/runpod-ollama.sh`
- Pod 시작 훅: `/pre_start.sh` → `/workspace/lens/runpod-pre-start.sh`

RunPod 기본 시작 스크립트가 실행하는 훅에 Ollama를 연결했습니다.
프로세스 잠금으로 중복 실행을 막고 Ollama 종료 시 5초 후 재시작합니다.
EC2 SSH 연결도 systemd가 자동 재시도합니다.
동시에 적재하는 모델과 병렬 추론은 각각 1개, 컨텍스트 기본값은 8192입니다.

```bash
# EC2에서 확인
sudo systemctl status lens-runpod-tunnel lens
curl --fail http://127.0.0.1:11434/api/tags
curl --fail http://127.0.0.1:11434/api/ps
sudo journalctl -u lens-runpod-tunnel -n 30 --no-pager
```

Pod를 교체하거나 중지 후 재배치하면 공개 IP·SSH 포트·호스트 키가 바뀔 수 있습니다.
그때 RunPod 콘솔의 새 연결 정보와 호스트 키를 확인하고 터널 설정을 갱신합니다.
새 Pod에는 EC2의 터널용 공개 키를 다시 등록해야 할 수 있습니다. 기존 SSH 호스트 키 검증을
끄지 말고 새 호스트 키를 신뢰할 수 있는 경로로 확인한 후 `known_hosts`를 갱신합니다.
환경 파일만 수정했다면 `sudo systemctl restart lens-runpod-tunnel`로 적용합니다.
unit 파일도 수정했다면 먼저 `sudo systemctl daemon-reload`를 실행합니다.
새 컨테이너에는 시작 훅과 제한된 공개 키도 다시 설치해야 할 수 있습니다.
`/workspace` 보존 여부는 해당 Pod의 볼륨 설정에 따릅니다.
현재 실행 중인 Pod를 중지하거나 교체하는 시험은 하지 않았습니다.

AWS의 월 $30 예산 알림에는 RunPod 요금이 포함되지 않습니다.
EC2를 중지해도 RunPod GPU는 별도로 계속 실행되므로 RunPod도 별도로 관리해야 합니다.

## 2026-09-22 검증 결과

- AWS→SSH 터널→Ollama 호출: 성공, 실제 GPU 메모리 약 17.4 GB 사용 확인
- 터널 systemd 재시작 후 연결 복구: 성공
- 공개 HTTPS 채팅 API: 두 질문 모두 HTTP 200, `status=answered`
- 전입신고 질문: 21.72초, 출처 6개·본문 인용 1개
- 보증금 반환 질문: 29.94초, 출처 6개·본문 인용 2개
- 테스트용 익명 대화: 검증 뒤 각각 초기화

이 검증은 검색→GPU 생성→웹 응답과 출처 표시의 연결 검증입니다.
답변의 법률적 정확성이나 모든 질문에 대한 완결성을 보증하지 않습니다.
실제로 보증금 반환 질문은 이사 전 확인할 절차에 대한 답변이 불완전했습니다.
검색 결과 선택·답변 내용 개선은 별도로 필요하며, 배포 과정에서 앱의 검색·생성 코드는 변경하지 않았습니다.
