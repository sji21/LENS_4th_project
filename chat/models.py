import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    """Private, expiring browser conversation. Uploaded file bytes are never stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state = models.JSONField(default=dict)
    expires_at = models.DateTimeField(db_index=True)
    busy_until = models.DateTimeField(default=timezone.now)
    lease_token = models.UUIDField(null=True, editable=False)
    # The browser request currently allowed to publish an answer.  Keeping this
    # separate from the private lease token lets the owner cancel only its own
    # in-flight request without allowing a stale tab to cancel a newer turn.
    active_request_id = models.UUIDField(null=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="conversations")
    case = models.ForeignKey("cases.ContractCase", null=True, blank=True, on_delete=models.CASCADE, related_name="conversations")
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="persistent_messages")
    public_id = models.CharField(max_length=32)
    role = models.CharField(max_length=16)
    content = models.TextField()
    status = models.CharField(max_length=16, blank=True)
    sources = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [models.UniqueConstraint(fields=("conversation", "public_id"), name="unique_conversation_message")]


class PendingDocument(models.Model):
    """Encrypted upload retained until a draft becomes a member-owned case."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="pending_documents")
    document_id = models.CharField(max_length=32, unique=True)
    original_name = models.CharField(max_length=180)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    storage_key = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)


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
