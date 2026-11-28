"""A separate database and attachment/log roots; never point at working data."""
import os
from pathlib import Path

from config.settings import *  # noqa: F403

VALIDATION_ROOT = Path(os.environ["VALIDATION_ROOT"]).resolve()
if VALIDATION_ROOT == BASE_DIR or not VALIDATION_ROOT.name.startswith("run-"):
    raise ValueError("VALIDATION_ROOT must be a dedicated run-* directory")
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": VALIDATION_ROOT / "db.sqlite3"}}
MEDIA_ROOT = VALIDATION_ROOT / "media"
PRIVATE_DOCUMENT_ROOT = VALIDATION_ROOT / "private_documents"
OPERATIONS_ROOT = VALIDATION_ROOT / "operations"
BACKUP_ROOT = OPERATIONS_ROOT / "backups"
LOG_ROOT = OPERATIONS_ROOT / "logs"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEBUG = False
