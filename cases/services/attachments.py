from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction

from cases.models import Attachment, CaseFact
from .facts import invalidate_source, record_text_facts
from .storage import checksum, delete_encrypted, save_encrypted


_ROLLBACK_STORAGE_KEYS = ContextVar("lens_rollback_storage_keys", default=None)


@contextmanager
def storage_rollback_guard():
    """Remove newly written encrypted files if the surrounding DB unit rolls back."""
    existing = _ROLLBACK_STORAGE_KEYS.get()
    if existing is not None:
        yield
        return
    keys = []
    token = _ROLLBACK_STORAGE_KEYS.set(keys)
    try:
        yield
    except BaseException:
        for storage_key in reversed(keys):
            try:
                delete_encrypted(storage_key)
            except (OSError, ValueError):
                pass
        raise
    finally:
        _ROLLBACK_STORAGE_KEYS.reset(token)


def _track_storage_for_rollback(storage_key):
    keys = _ROLLBACK_STORAGE_KEYS.get()
    if keys is not None and storage_key not in keys:
        keys.append(storage_key)


def record_pending_document(conversation, *, data, filename, content_type, document_id):
    from chat.models import PendingDocument
    digest = checksum(data)
    pending = PendingDocument.objects.filter(conversation=conversation, sha256=digest).first()
    if pending:
        return pending
    pending_id = __import__("uuid").uuid4()
    storage_key = save_encrypted(f"pending-{conversation.pk}", pending_id, data)
    _track_storage_for_rollback(storage_key)
    try:
        return PendingDocument.objects.create(
            id=pending_id, conversation=conversation, document_id=document_id,
            original_name=filename, content_type=content_type, size=len(data),
            sha256=digest, storage_key=storage_key,
        )
    except Exception:
        delete_encrypted(storage_key)
        raise


def promote_pending_documents(conversation, case):
    from .storage import load_decrypted
    documents = {d.get("document_id"): d for d in conversation.state.get("documents", [])}
    pending_rows = list(conversation.pending_documents.select_for_update())
    for pending in pending_rows:
        document = documents.get(pending.document_id)
        if not document:
            continue
        chunks = document.get("context", {}).get("chunks", [])
        extracted_text = "\n".join(str(chunk.get("text", "")) for chunk in chunks)
        attachment = record_document(
            case, data=load_decrypted(pending.storage_key), filename=pending.original_name,
            content_type=pending.content_type, document=document, extracted_text=extracted_text,
        )
        document["attachment_id"] = str(attachment.pk)
        pending.delete()


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
        storage_key = ""
        try:
            storage_key = save_encrypted(case.pk, attachment.pk, data)
            _track_storage_for_rollback(storage_key)
            attachment.storage_key = storage_key
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
        except Exception:
            if storage_key:
                delete_encrypted(storage_key)
            raise
    return attachment


@transaction.atomic
def delete_document_attachment(case, document_id):
    invalidate_source(case, CaseFact.SourceType.DOCUMENT, document_id)
    attachment = case.attachments.filter(kind=Attachment.Kind.DOCUMENT, context_json__document_id=document_id).first()
    if attachment:
        attachment.delete()
