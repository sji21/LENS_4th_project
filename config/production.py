"""Production settings for Gunicorn behind a trusted HTTPS reverse proxy."""
from .settings import *  # noqa: F403

from cryptography.fernet import Fernet

DEBUG = False
if not os.getenv("DJANGO_ALLOWED_HOSTS") or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set explicit DJANGO_ALLOWED_HOSTS for production.")
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("Production requires a random DJANGO_SECRET_KEY of at least 50 characters.")
try:
    Fernet(FILE_ENCRYPTION_KEY.encode("ascii"))
except (ValueError, TypeError, UnicodeError) as error:
    raise ImproperlyConfigured("Set a valid LENS_FILE_ENCRYPTION_KEY for private uploads.") from error

# Gunicorn listens only on loopback. The trusted Nginx proxy replaces this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS]
SECURE_HSTS_SECONDS = 3600
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
