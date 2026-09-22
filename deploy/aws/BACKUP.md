# EBS 및 SQLite 백업

2026-09-23 적용. 계정 `514644129560`, 서울 `ap-northeast-2`.

## 적용 구성

- 대상: `vol-0adfa689791285be3` (암호화 gp3 60 GB, EC2 루트 볼륨).
- DLM 정책: `policy-0d5e3ce7bcdd03093`, ENABLED.
- 대상 태그: `LENSBackup=daily`. 다른 볼륨에 이 태그를 붙이면 그 볼륨도 대상이 됩니다.
- 예약: UTC 18:00 / 한국시간 03:00 기준 매일. AWS의 실제 생성 시작은 예약 시각 이후 1시간 이내입니다.
- 보관: **정책이 생성한 최근 7개**. 이전 스냅샷은 정책이 자동 삭제합니다.
- 첫 배포 전 수동 백업: `snap-0f68fc92c4a71dd78`. 이 수동 백업은 7개 보관 정책에 포함되지 않으며 자동 삭제되지 않습니다.
- 2026-09-23 확인 결과: 첫 수동 스냅샷 `completed` / 100%, 암호화됨, 외부 공유 권한 없음.
- DLM 역할: `AWSDataLifecycleManagerDefaultRole`. EC2에 스냅샷 API 권한을 부여하지 않았습니다.

정책 파일은 [ebs-snapshot-policy.json](ebs-snapshot-policy.json)입니다.
정책을 다시 만들기 전에 기존 ID와 대상을 확인해 중복 정책을 만들지 않습니다.
정기 정책은 다음 예약부터 실행하므로 생성 직후의 수동 스냅샷과 구분합니다.

```bash
aws dlm get-lifecycle-policy --region ap-northeast-2 --policy-id policy-0d5e3ce7bcdd03093
aws ec2 describe-snapshots --region ap-northeast-2 --owner-ids self \
  --filters Name=volume-id,Values=vol-0adfa689791285be3
```

스냅샷 보관에 따른 저장 비용은 추가 발생합니다. 다른 리전·계정으로 복사하거나
공개 공유하지 않습니다. 스냅샷에는 `/etc/lens`의 비밀 설정과 터널 키도 포함되므로
복원 권한은 서버 관리자에게만 부여해야 합니다.

## DB 일관성

`lens-sqlite-backup.timer`는 매일 UTC 17:30 / 한국시간 02:30에
`backup-sqlite.py`를 실행합니다. SQLite Backup API로 운영 DB의 일관된 사본을
만들고 `PRAGMA integrity_check` 성공 후 원자적으로 교체합니다.

- 운영 DB: `/opt/lens/app/data/database/web.sqlite3`
- 복구용 사본: `/opt/lens/app/data/backups/web-latest.sqlite3`, 권한 0600
- 이 사본도 같은 EBS 위에 있으며, EBS 스냅샷에 포함돼야 볼륨 장애에 대비한 백업이 됩니다.
- EC2가 꺼져 있으면 DB 타이머가 실행되지 않으며, 다시 켜질 때 누락 작업을 실행합니다.
- DLM과 DB 타이머는 독립적입니다. DB 사본 생성 실패가 DLM을 막지는 않으므로 로그와 사본 시각을 확인해야 합니다.

```bash
sudo systemctl status lens-sqlite-backup.timer --no-pager
sudo systemctl start lens-sqlite-backup.service
sudo journalctl -u lens-sqlite-backup -n 20 --no-pager
```

정기 EBS 스냅샷 자체는 실행 중 볼륨의 **crash-consistent** 백업입니다.
DB 사본 시점과 이후 업로드 파일 변경 시점은 다를 수 있어, 애플리케이션 전체의
동일 시점 복원을 보장하지 않습니다. 첫 수동 스냅샷은 웹 쓰기를 중지하고
DB 사본 검증·디스크 동기화 후 요청했으며, 요청 응답 후 웹 서비스를 재개했습니다.

## 복구 절차

1. `completed` 스냅샷을 골라 원본과 같은 가용 영역에 새 EBS를 만듭니다.
2. 복원 볼륨을 별도 검증 인스턴스에 연결해 필요한 파일과 권한을 확인합니다.
3. SQLite 무결성, 암호화 키와 업로드 파일의 대응, MySQL 검색 release 검증을 확인합니다.
4. 필요하다면 웹 서비스를 멈춘 상태에서 검증된 `web-latest.sqlite3` 사본을 사용합니다.
   실행 중 DB를 덮어쓰거나 다른 시점의 WAL 파일과 섞지 않습니다.
5. 웹·로그인·기존 파일 복호화·검색을 확인한 뒤 운영 전환합니다. 원본 볼륨은 검증 완료까지 보존합니다.

EBS 스냅샷만으로 EC2·보안 그룹·CloudFront·IAM·Elastic IP 리소스가 자동 복구되지는 않습니다.
[README.md](README.md)의 리소스 기록과 설정 파일을 함께 사용합니다.
이번 작업에서 실제 복원 인스턴스를 만들거나 전체 재해복구 시험을 하지는 않았습니다.

참고: [AWS DLM 정책·예약·보관 설명](https://docs.aws.amazon.com/ebs/latest/userguide/snapshot-ami-policy.html),
[애플리케이션 일관성 스냅샷](https://docs.aws.amazon.com/ebs/latest/userguide/automate-app-consistent-backups.html).
