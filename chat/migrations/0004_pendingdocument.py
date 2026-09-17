import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("chat", "0003_conversation_case_conversation_created_at_and_more")]

    operations = [
        migrations.CreateModel(
            name="PendingDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("document_id", models.CharField(max_length=32, unique=True)),
                ("original_name", models.CharField(max_length=180)),
                ("content_type", models.CharField(blank=True, max_length=120)),
                ("size", models.PositiveBigIntegerField()),
                ("sha256", models.CharField(db_index=True, max_length=64)),
                ("storage_key", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pending_documents", to="chat.conversation")),
            ],
        ),
    ]
