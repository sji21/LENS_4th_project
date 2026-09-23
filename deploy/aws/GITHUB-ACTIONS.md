# GitHub `main` 자동 EC2 배포

`main`에 병합된 검토 완료 커밋을 EC2에 자동 반영하는 구성이다. GitHub-hosted runner가 SSH로 EC2에 들어오는 방식은 사용하지 않는다. 현재 EC2 보안 그룹은 배포 PC SSH만 허용하므로, GitHub Actions용으로 22번 포트를 인터넷에 열 필요가 없다.

대신 EC2에 GitHub self-hosted runner를 설치한다. 러너는 GitHub에 outbound 연결로 작업을 수신하고, 제한된 `sudo` 명령으로만 `/usr/local/sbin/lens-github-deploy`를 실행한다. 배포 스크립트는 정확한 `main` 커밋만 받으며, 운영 DB 백업, 코드 fast-forward, 의존성 확인, 마이그레이션, 정적 파일 수집, 검색 release 확인, 서비스 재시작을 차례로 수행한다.

## 처음 한 번 설정

이 설정을 포함한 PR을 `main`에 병합한 뒤, EC2에서 한 번 수동으로 해당 `main` 커밋을 반영한다. 기존 환경 파일, SQLite DB, 업로드 파일, 검색 자료는 건드리지 않는다.

```bash
cd /opt/lens/app
sudo -u lens git fetch origin main
sudo -u lens git merge --ff-only origin/main
sudo install -o root -g root -m 0750 deploy/aws/lens-github-deploy /usr/local/sbin/lens-github-deploy
```

러너 전용 Linux 계정을 만든다.

```bash
sudo useradd --create-home --shell /bin/bash github-runner
```

GitHub 저장소에서 **Settings → Actions → Runners → New self-hosted runner**를 열고, Linux 설정 명령을 확인한다. EC2에서는 그 명령을 `github-runner` 사용자로 실행한다. `config.sh` 단계에는 다음 옵션을 추가한다.

```bash
--labels lens-production --unattended
```

GitHub 화면에 표시되는 runner 등록 토큰은 일회용이며 저장소·문서·채팅에 저장하지 않는다. 설정이 끝난 러너 폴더에서 서비스로 등록한다.

```bash
sudo ./svc.sh install github-runner
sudo ./svc.sh start
sudo ./svc.sh status
```

러너 계정이 배포 스크립트만 root로 실행하도록 sudoers 규칙을 만든다.

```bash
sudo visudo -f /etc/sudoers.d/lens-github-runner
```

파일 내용:

```sudoers
github-runner ALL=(root) NOPASSWD: /usr/local/sbin/lens-github-deploy *
```

스크립트는 인자를 40자리 소문자 Git SHA로 검증하므로 임의 셸 명령은 실행하지 않는다. 권한을 확인한다.

```bash
sudo chmod 0440 /etc/sudoers.d/lens-github-runner
sudo visudo -c -f /etc/sudoers.d/lens-github-runner
sudo -iu github-runner sudo -n /usr/local/sbin/lens-github-deploy not-a-sha || true
```

마지막 명령은 SHA 검증 오류가 나야 정상이다. sudo 권한 오류가 나오면 sudoers 설정을 다시 확인한다.

배포 스크립트가 Django 관리 명령을 실행할 때는 `/etc/lens/lens.env`를 직접 불러오고, 저장소에 있는 `config.settings`를 사용한다. 서비스는 기존 systemd 설정대로 `config.production`으로 실행된다. 따라서 `/etc/lens/lens.env`는 `lens` 사용자가 읽을 수 있어야 하며, 배포 때마다 필요한 비밀값과 운영 DB 설정이 관리 명령에도 적용된다.

## GitHub Actions 동작

[`.github/workflows/deploy-production.yml`](../../.github/workflows/deploy-production.yml)은 `main` 푸시와 수동 실행에서만 동작한다. `pull_request` 이벤트에서는 실행하지 않는다. 러너가 준비된 뒤 `main`에 병합하면 GitHub **Actions → Deploy production**에서 배포 기록과 로그를 확인할 수 있다.

첫 자동 실행은 이전에 큐에 있던 `main` 배포 작업도 처리할 수 있다. 배포가 끝난 뒤 아래로 EC2의 상태를 확인한다.

```bash
sudo systemctl status lens lens-runpod-tunnel nginx --no-pager
curl --fail http://127.0.0.1:11434/api/tags
```

## 운영 규칙

- `main` 직접 푸시는 운영 배포 권한이다. GitHub branch protection에서 PR 리뷰와 Actions 성공을 요구하고, `main` 푸시 권한을 운영 담당자에게만 준다.
- `requirements.txt`에 없는 시스템 패키지, Python 버전, Gunicorn 설정, Nginx·systemd 설정, RunPod 터널, 검색 자료 release를 바꾸는 작업은 이 배포 범위 밖이다. 해당 변경은 별도 운영 절차로 검토한다.
- 새 Django migration은 자동으로 실행되기 전에 SQLite DB를 `data/backups/web-latest.sqlite3`에 일관된 사본으로 백업한다. EBS 스냅샷 정책도 별도로 유지한다.
- 코드 병합 뒤 검증 단계가 실패하면 Actions가 실패한다. 서비스 재시작은 실패 지점 뒤에는 실행하지 않는다. 로그를 확인하고 수정 커밋을 다시 `main`에 병합한다.
- GitHub runner 등록 토큰, `/etc/lens/lens.env`, 개인 SSH 키와 암호화 키는 GitHub Secrets나 저장소에 추가하지 않는다.
