import json
import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from chat.law_watch import check_law


class Command(BaseCommand):
    help = "법령 메타데이터 변경을 Django 관리자 알림에 기록합니다."

    def add_arguments(self, parser):
        parser.add_argument("--corpus", type=Path, default=settings.BASE_DIR / "data/chunks/chunks.jsonl")
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--interval", type=int, default=86400)

    def handle(self, *args, **options):
        credential = os.getenv("LAW_OPEN_API_OC", "").strip()
        if not credential:
            raise CommandError("LAW_OPEN_API_OC 설정이 필요합니다.")
        if options["interval"] < 60:
            raise CommandError("감시 주기는 60초 이상이어야 합니다.")
        try:
            while True:
                titles = {}
                with options["corpus"].open(encoding="utf-8") as corpus:
                    for line in corpus:
                        meta = json.loads(line)["metadata"]
                        if meta.get("doc_type") in {"law", "decree", "rule"} and meta.get("title"):
                            dates = titles.setdefault(meta["title"], set())
                            if meta.get("effective_date"):
                                dates.add(meta["effective_date"])
                if not titles:
                    raise CommandError("감시할 법령이 없습니다.")
                failed = 0
                for title, dates in sorted(titles.items()):
                    status = check_law(title, credential, dates)
                    failed += status == "failed"
                    self.stdout.write(f"{title}: {status}")
                if not options["watch"]:
                    if failed:
                        raise CommandError("일부 법령 조회 실패. 관리자 화면에서 상태를 확인하세요.")
                    return
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("감시 종료")
