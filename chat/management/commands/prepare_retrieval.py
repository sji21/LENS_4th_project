"""Prepare server retrieval data separately from web requests and migrations."""
from django.core.management.base import BaseCommand, CommandError

from src.ingestion.server_build import prepare


class Command(BaseCommand):
    help = "승인 원천 자료로 SQLite·검색 인덱스 구축 (ZIP 불필요)"
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--data-root", help="기본값: 저장소 data 폴더")
        parser.add_argument("--rebuild", action="store_true", help="기존 데이터 백업 후 재구축")

    def handle(self, *args, **options):
        try:
            result = prepare(options["data_root"], options["rebuild"])
        except (ValueError, OSError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(str(result)))
