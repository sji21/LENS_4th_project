from .settings import *  # noqa: F403

SECRET_KEY = "test-only-not-for-deployment"
DEBUG = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
