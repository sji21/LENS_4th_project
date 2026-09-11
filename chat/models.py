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


class LawWatch(models.Model):
    title = models.CharField(max_length=250, unique=True)
    metadata = models.JSONField(default=dict)
    checked_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.title


class LawAlert(models.Model):
    watch = models.ForeignKey(LawWatch, on_delete=models.CASCADE)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)
