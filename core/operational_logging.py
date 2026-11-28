"""Dated JSON logs; never serialize request data or exception messages."""
from datetime import date, timedelta
import hashlib
import json
import logging
import os
import re
import sys

from django.conf import settings
from django.utils import timezone

from .private_files import outside_uploads, private_directory


class DailyJSONHandler(logging.Handler):
    def __init__(self, channel):
        super().__init__()
        if channel not in {"application", "security"}:
            raise ValueError("Unknown log channel")
        self.channel = channel
        self.ready_root = None
        self.cleaned_day = None

    def emit(self, record):
        try:
            self.write_record(record)
        except (OSError, ValueError):
            # Failure is visible to the process supervisor without causing a
            # logging error to break a business transaction or leak record data.
            sys.stderr.write('{"event":"log_write_failed","channel":"' + self.channel + '"}\n')

    def write_record(self, record):
        root = outside_uploads(settings.LOG_ROOT, [settings.MEDIA_ROOT, settings.PRIVATE_DOCUMENT_ROOT])
        if root != self.ready_root:
            private_directory(root)
            self.ready_root = root
        today = timezone.localdate()
        if self.cleaned_day != today:
            pattern = re.compile(r"(?:application|security)-(\d{4}-\d{2}-\d{2})\.jsonl\Z")
            for path in root.iterdir():
                match = pattern.fullmatch(path.name)
                if match and date.fromisoformat(match[1]) < today - timedelta(days=90):
                    path.unlink()
            self.cleaned_day = today
        item = {
            "time": timezone.now().isoformat(), "level": record.levelname,
            "event": getattr(record, "event", "framework_event"), "logger": record.name,
        }
        for key in ("route", "method", "status", "user_id", "account_hash", "duration_ms"):
            if hasattr(record, key):
                item[key] = getattr(record, key)
        if record.exc_info and record.exc_info[1]:
            item["exception_type"] = type(record.exc_info[1]).__name__
        payload = (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
        descriptor = os.open(root / f"{self.channel}-{today}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)


class OperationalLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import time
        start = time.perf_counter()
        response = self.get_response(request)
        fields = {
            "event": "http_response", "status": response.status_code,
            "method": request.method,
            "route": getattr(getattr(request, "resolver_match", None), "view_name", None),
            "duration_ms": round((time.perf_counter() - start) * 1000, 3),
            "user_id": getattr(getattr(request, "user", None), "pk", None),
        }
        logging.getLogger("operations").info("http_response", extra=fields)
        if response.status_code in {401, 403}:
            logging.getLogger("security").warning("access_denied", extra={**fields, "event": "access_denied"})
        return response


def login_failure(sender, credentials, request, **kwargs):
    account = str(credentials.get("username", ""))
    logging.getLogger("security").warning("login_failed", extra={
        "event": "login_failed", "account_hash": hashlib.sha256(account.encode()).hexdigest(),
    })


def login_protection_saved(sender, instance, **kwargs):
    if instance.locked_until and instance.locked_until > timezone.now():
        logging.getLogger("security").warning("login_locked", extra={
            "event": "login_locked", "account_hash": hashlib.sha256(instance.username.encode()).hexdigest(),
        })
