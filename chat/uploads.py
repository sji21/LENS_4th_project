from django.core.files.uploadhandler import MemoryFileUploadHandler, StopUpload


class BoundedMemoryUploadHandler(MemoryFileUploadHandler):
    """Enforce the byte limit while receiving; never spool private originals to disk."""

    limit = 20 * 1024 * 1024

    def receive_data_chunk(self, raw_data, start):
        if start + len(raw_data) > self.limit:
            self.request.upload_too_large = True
            raise StopUpload(connection_reset=False)
        return super().receive_data_chunk(raw_data, start)
