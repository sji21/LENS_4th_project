import uuid

from django.conf import settings
from django.db import models


class ContractCase(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "진행 중"
        COMPLETED = "completed", "완료"
        ARCHIVED = "archived", "보관"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="contract_cases")
    title = models.CharField(max_length=120)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self):
        return self.title


class Attachment(models.Model):
    class Kind(models.TextChoices):
        DOCUMENT = "document", "문서"

    class ProcessingStatus(models.TextChoices):
        PENDING = "pending", "대기"
        PROCESSING = "processing", "처리 중"
        READY = "ready", "완료"
        FAILED = "failed", "실패"
        DELETED = "deleted", "삭제"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(ContractCase, on_delete=models.CASCADE, related_name="attachments")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    original_name = models.CharField(max_length=180)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    storage_key = models.CharField(max_length=255, blank=True)
    processing_status = models.CharField(max_length=16, choices=ProcessingStatus.choices, default=ProcessingStatus.PENDING)
    error_code = models.CharField(max_length=64, blank=True)
    analysis_json = models.JSONField(default=dict, blank=True)
    context_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=("case", "sha256", "kind"), name="unique_case_attachment")]


class CaseFact(models.Model):
    class SourceType(models.TextChoices):
        CHAT = "chat", "채팅"
        DOCUMENT = "document", "문서 OCR"
        USER = "user", "사용자 확인"

    class Status(models.TextChoices):
        ACTIVE = "active", "확인"
        CONFLICT = "conflict", "충돌"
        INVALIDATED = "invalidated", "무효"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(ContractCase, on_delete=models.CASCADE, related_name="facts")
    key = models.CharField(max_length=80, db_index=True)
    value_json = models.JSONField()
    normalized_value = models.CharField(max_length=300, db_index=True)
    source_type = models.CharField(max_length=16, choices=SourceType.choices)
    source_ref = models.CharField(max_length=100)
    source_label = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    confidence = models.FloatField(default=1.0)
    observed_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("key", "-observed_at")
        indexes = [models.Index(fields=("case", "key", "status"))]
        constraints = [models.UniqueConstraint(fields=("case", "key", "normalized_value", "source_type", "source_ref"), name="unique_fact_assertion")]


class ChecklistItem(models.Model):
    class State(models.TextChoices):
        TODO = "todo", "확인 필요"
        DONE = "done", "완료"
        DISMISSED = "dismissed", "제외"

    case = models.ForeignKey(ContractCase, on_delete=models.CASCADE, related_name="checklist_items")
    code = models.CharField(max_length=80)
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    priority = models.PositiveSmallIntegerField(default=50)
    state = models.CharField(max_length=16, choices=State.choices, default=State.TODO)
    auto_completed = models.BooleanField(default=False)
    source_fact = models.ForeignKey(CaseFact, null=True, blank=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("priority", "id")
        constraints = [models.UniqueConstraint(fields=("case", "code"), name="unique_case_checklist")]


class ScheduleEvent(models.Model):
    class Status(models.TextChoices):
        CANDIDATE = "candidate", "승인 대기"
        CONFIRMED = "confirmed", "확정"
        DISMISSED = "dismissed", "제외"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(ContractCase, on_delete=models.CASCADE, related_name="schedule_events")
    starts_at = models.DateTimeField()
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    rule_code = models.CharField(max_length=80)
    source_fact = models.ForeignKey(CaseFact, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CANDIDATE)
    user_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("starts_at",)
        constraints = [models.UniqueConstraint(fields=("case", "rule_code", "starts_at"), name="unique_case_schedule")]


class Report(models.Model):
    case = models.ForeignKey(ContractCase, on_delete=models.CASCADE, related_name="reports")
    version = models.PositiveIntegerField()
    content_json = models.JSONField(default=dict)
    source_snapshot = models.JSONField(default=dict)
    generation_mode = models.CharField(max_length=40, default="llm")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version",)
        constraints = [models.UniqueConstraint(fields=("case", "version"), name="unique_case_report_version")]
