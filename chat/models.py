import uuid
from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    """Private, expiring browser conversation. Uploaded file bytes are never stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state = models.JSONField(default=dict)
    expires_at = models.DateTimeField(db_index=True)
    busy_until = models.DateTimeField(default=timezone.now)
    lease_token = models.UUIDField(null=True, editable=False)
