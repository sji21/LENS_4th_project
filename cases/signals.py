import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Attachment
from .services.storage import delete_encrypted

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Attachment)
def remove_attachment_bytes(sender, instance, **kwargs):
    try:
        delete_encrypted(instance.storage_key)
    except (OSError, ValueError):
        logger.error("Failed to remove encrypted attachment bytes: attachment=%s", instance.pk)
