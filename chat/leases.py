"""Short database leases renewed only while a request worker is alive."""
import logging
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread

from django.conf import settings
from django.db import DatabaseError, connections
from django.utils import timezone

from .models import Conversation

logger = logging.getLogger(__name__)


def renew(conversation_id, token):
    now = timezone.now()
    # An expired owner must never resurrect its lease or overwrite a successor.
    return Conversation.objects.filter(
        pk=conversation_id, lease_token=token, busy_until__gt=now,
    ).update(busy_until=now + timedelta(seconds=settings.CHAT_LEASE_SECONDS))


@contextmanager
def heartbeat(conversation_id, token):
    stopped = Event()

    def run():
        try:
            while not stopped.wait(settings.CHAT_LEASE_SECONDS / 3):
                try:
                    if not renew(conversation_id, token):
                        break
                except DatabaseError:
                    logger.warning("Conversation lease renewal unavailable")
                    # Retry without treating an expired lease as owned.
                    connections.close_all()
        finally:
            connections.close_all()

    worker = Thread(target=run, name="chat-lease", daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=1)
