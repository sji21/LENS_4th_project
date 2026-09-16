from hashlib import sha256
from pathlib import Path
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _cipher():
    key = settings.FILE_ENCRYPTION_KEY.strip().encode("ascii")
    if not key:
        raise ImproperlyConfigured("Set LENS_FILE_ENCRYPTION_KEY before storing private files.")
    try:
        return Fernet(key)
    except (ValueError, TypeError) as error:
        raise ImproperlyConfigured("LENS_FILE_ENCRYPTION_KEY must be a valid Fernet key.") from error


def save_encrypted(case_id, attachment_id, data):
    root = Path(settings.PRIVATE_UPLOAD_ROOT).resolve()
    folder = (root / str(case_id)).resolve()
    if root not in folder.parents:
        raise ValueError("Invalid private storage path")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / f"{attachment_id}.bin"
    path.write_bytes(_cipher().encrypt(data))
    os.chmod(path, 0o600)
    return str(path.relative_to(root))


def load_decrypted(storage_key):
    root = Path(settings.PRIVATE_UPLOAD_ROOT).resolve()
    path = (root / storage_key).resolve()
    if root not in path.parents:
        raise ValueError("Invalid private storage path")
    try:
        return _cipher().decrypt(path.read_bytes())
    except InvalidToken as error:
        raise ValueError("Stored file cannot be decrypted with the configured key.") from error


def delete_encrypted(storage_key):
    if not storage_key:
        return
    root = Path(settings.PRIVATE_UPLOAD_ROOT).resolve()
    path = (root / storage_key).resolve()
    if root not in path.parents:
        raise ValueError("Invalid private storage path")
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


def checksum(data):
    return sha256(data).hexdigest()
