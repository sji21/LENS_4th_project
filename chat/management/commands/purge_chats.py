from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from chat.models import Conversation


class Command(BaseCommand):
    help = "Delete expired browser conversations and their masked document text."

    def handle(self, *args, **options):
        count, _ = Conversation.objects.filter(expires_at__lte=timezone.now()).filter(
            Q(user__isnull=True) | Q(case__isnull=True)
        ).delete()
        self.stdout.write(f"Deleted {count} expired conversations.")
