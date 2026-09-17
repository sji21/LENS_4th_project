import logging

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Attachment
from chat.models import PendingDocument
from .services.storage import delete_encrypted

logger = logging.getLogger(__name__)


def _remove_encrypted_bytes(storage_key, object_label, object_id):
    try:
        delete_encrypted(storage_key)
    except (OSError, ValueError):
        logger.error("Failed to remove encrypted bytes: %s=%s", object_label, object_id)


@receiver(post_delete, sender=Attachment)
def remove_attachment_bytes(sender, instance, **kwargs):
    transaction.on_commit(lambda: _remove_encrypted_bytes(instance.storage_key, "attachment", instance.pk))


@receiver(post_delete, sender=PendingDocument)
def remove_pending_document_bytes(sender, instance, **kwargs):
    transaction.on_commit(lambda: _remove_encrypted_bytes(instance.storage_key, "pending", instance.pk))
