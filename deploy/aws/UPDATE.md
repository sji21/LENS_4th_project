# 기존 EC2의 코드 갱신

2026-09-23 반영 커밋: `40ac9d706c4959ab14045349d47ecab55ad228a7`.
이 커밋의 변경은 달력 선택 및 방 삭제 UI이며, 검색 코드·의존성·DB 마이그레이션 변경은 없습니다.
검색 release `7993b603342b6c9da0f8c8cf6245e41c21eef61676df9de02328fe33bb73ca62`를 유지했습니다.

`install-server.sh`를 운영 서버 갱신용으로 재실행하지 않습니다.
아래는 같은 종류의 UI 갱신 절차입니다. 경로·커밋을 검토하고 실행하세요.

1. 배포할 전체 커밋 SHA를 정하고 현재 HEAD와 변경 범위를 확인합니다.
2. 운영 DB 사본과 EBS 복구 지점을 확인합니다. 환경 파일·키·검색 자료를 덮어쓰지 않습니다.
3. `git diff --quiet HEAD --`로 서버의 추적 파일 변경이 없는지 확인합니다.
4. `git fetch origin main` 후 검토한 커밋으로 `git merge --ff-only <SHA>` 합니다.
5. 해당 변경 테스트, `manage.py migrate --check`, `collectstatic --noinput`을 실행합니다.
6. MySQL 검색 `verify` 후 `sudo systemctl restart lens` 합니다.
7. 공개 HTTPS·정적 파일·로그인 화면·검색 준비 상태를 확인합니다.

모든 Django 운영 명령은 `/opt/lens/app`에서 `lens` 사용자로 실행하며,
`DJANGO_SETTINGS_MODULE=config.production`과 `/opt/lens/venv311/bin/python`을 사용합니다.
테스트는 `config.test_settings`를 사용합니다. 기존 운영 DB에 테스트를 실행하지 않습니다.
정적 파일은 Nginx가 읽을 수 있어야 합니다.

```bash
cd /opt/lens/app
sudo -u lens env DJANGO_SETTINGS_MODULE=config.test_settings \
  /opt/lens/venv311/bin/python -m pytest tests/test_case_features.py -q
sudo -u lens env DJANGO_SETTINGS_MODULE=config.production \
  /opt/lens/venv311/bin/python manage.py migrate --check
sudo -u lens env DJANGO_SETTINGS_MODULE=config.production \
  /opt/lens/venv311/bin/python manage.py collectstatic --noinput
sudo chmod -R a+rX /opt/lens/app/data/staticfiles
sudo -u lens /opt/lens/venv311/bin/python -m src.ingestion.mysql_search verify \
  --release data/mysql-search/active.json
sudo systemctl restart lens
```

검색 코드나 관련 라이브러리를 변경하면 release manifest 검증이 실패할 수 있습니다.
이 경우 [MYSQL-SEARCH.md](MYSQL-SEARCH.md)에 따라 호환되는 검색 배포본을 먼저 준비합니다.
새 DB 마이그레이션은 별도 검토·백업 후 적용하며 `migrate --check`를 무시하지 않습니다.

이번 UI 배포의 이전 커밋은 `117445d7edbdc8a2678e005bc596fa877fdd80b6`입니다.
되돌릴 때도 먼저 변경사항을 확인하고 이전 커밋을 체크아웃한 후 정적 파일 수집과 재시작을 수행합니다.
이 UI 변경에는 DB 스키마 변경이 없어 DB 복원은 필요하지 않습니다.

운영 설정이 처음 Git에 추가되는 커밋을 배포할 때는 서버의 동일 경로 미추적 파일을
먼저 별도 보관하고 비교해야 합니다. `git clean`으로 서버 파일이나 데이터를 일괄 삭제하지 않습니다.
GitHub Actions 자동 배포는 구성하지 않았습니다.
