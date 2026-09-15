from django.db import transaction

from cases.models import Attachment, CaseFact
from .facts import invalidate_source, record_text_facts
from .storage import checksum, delete_encrypted, save_encrypted


@transaction.atomic
def record_document(case, *, data, filename, content_type, document, extracted_text):
    digest = checksum(data)
    attachment, created = Attachment.objects.get_or_create(
        case=case, sha256=digest, kind=Attachment.Kind.DOCUMENT,
        defaults={
            "original_name": filename, "content_type": content_type, "size": len(data),
            "processing_status": Attachment.ProcessingStatus.PROCESSING,
            "analysis_json": document.get("analysis", {}),
            "context_json": {**document.get("context", {}), "document_id": document.get("document_id", "")},
        },
    )
    if created:
        attachment.storage_key = save_encrypted(case.pk, attachment.pk, data)
        attachment.processing_status = Attachment.ProcessingStatus.READY
        attachment.save(update_fields=("storage_key", "processing_status"))
        record_text_facts(
            case, extracted_text, source_type=CaseFact.SourceType.DOCUMENT,
            source_ref=document["document_id"], source_label=filename,
        )
        if document.get("kind") == "contract":
            from .facts import record_fact
            record_fact(case, key="contract_reviewed", value=True, source_type=CaseFact.SourceType.DOCUMENT, source_ref=document["document_id"], source_label=filename)
        if document.get("kind") == "registry":
            from .facts import record_fact
            record_fact(case, key="registry_reviewed", value=True, source_type=CaseFact.SourceType.DOCUMENT, source_ref=document["document_id"], source_label=filename)
    return attachment


@transaction.atomic
def delete_document_attachment(case, document_id):
    invalidate_source(case, CaseFact.SourceType.DOCUMENT, document_id)
    attachment = case.attachments.filter(kind=Attachment.Kind.DOCUMENT, context_json__document_id=document_id).first()
    if attachment:
        delete_encrypted(attachment.storage_key)
        attachment.delete()
