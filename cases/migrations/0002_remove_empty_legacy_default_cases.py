from django.db import migrations


def remove_empty_legacy_default_cases(apps, schema_editor):
    ContractCase = apps.get_model("cases", "ContractCase")
    for case in ContractCase.objects.filter(title="나의 임대차 상담").iterator():
        has_case_data = (
            case.attachments.exists()
            or case.facts.exists()
            or case.checklist_items.exists()
            or case.schedule_events.exists()
            or case.reports.exists()
        )
        if has_case_data:
            continue
        has_chat_data = False
        for conversation in case.conversations.all():
            state = conversation.state or {}
            if state.get("messages") or state.get("documents") or conversation.persistent_messages.exists():
                has_chat_data = True
                break
        if not has_chat_data:
            case.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0003_conversation_case_conversation_created_at_and_more"),
        ("cases", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(remove_empty_legacy_default_cases, migrations.RunPython.noop),
    ]
