from datetime import timedelta
import time
import threading

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from .models import LoginProtection, User


LOCK_MESSAGE = "连续登录失败过多，账号已锁定，请15分钟后重试。"
UNKNOWN_ATTEMPT_LIMIT = 30
UNKNOWN_WINDOW_SECONDS = 15 * 60
_locks = [threading.RLock() for _ in range(64)]


def protected_clean(form, authenticate_clean):
    with _locks[hash(form.cleaned_data.get("username", "")) % len(_locks)]:
        return _retry_clean(form, authenticate_clean)


def _retry_clean(form, authenticate_clean):
    for attempt in range(40):
        try:
            return _protected_clean(form, authenticate_clean)
        except OperationalError as error:
            if "locked" not in str(error).lower() or attempt == 39:
                raise
            time.sleep(0.05)


def _protected_clean(form, authenticate_clean):
    username = form.cleaned_data.get("username")
    if not username or not form.cleaned_data.get("password"):
        return authenticate_clean()
    if not User.objects.filter(username=username).exists():
        _throttle_unknown_account(form.request)
        return authenticate_clean()
    state, _ = LoginProtection.objects.get_or_create(username=username)
    error = None
    result = None
    with transaction.atomic():
        # Obtain the write lock before reading state, including on SQLite where
        # select_for_update alone is ineffective. Concurrent attempts serialize.
        LoginProtection.objects.filter(pk=state.pk).update(failures=F("failures"))
        state.refresh_from_db()
        now = timezone.now()
        if state.locked_until and now < state.locked_until:
            error = ValidationError(LOCK_MESSAGE, code="locked")
        else:
            if state.locked_until or (state.window_started_at and now >= state.window_started_at + timedelta(minutes=15)):
                state.failures = 0
                state.window_started_at = None
                state.locked_until = None
            try:
                result = authenticate_clean()
            except ValidationError as failure:
                state.window_started_at = state.window_started_at or now
                state.failures += 1
                error = failure
                if state.failures >= 5:
                    state.locked_until = now + timedelta(minutes=15)
                    error = ValidationError(LOCK_MESSAGE, code="locked")
            else:
                state.failures = 0
                state.window_started_at = None
                state.locked_until = None
            state.save()
    # Raising inside the transaction would roll back the failure counter.
    if error:
        raise error
    return result


def _throttle_unknown_account(request):
    # Key by the server-provided address, never by a submitted username or
    # an untrusted forwarding header.
    address = request.META.get("REMOTE_ADDR") if request else None
    if not address:
        return
    key = f"unknown-login:{address}"
    if cache.add(key, 1, timeout=UNKNOWN_WINDOW_SECONDS):
        return
    try:
        attempts = cache.incr(key)
    except ValueError:
        # The bucket may expire or be evicted between add and incr.
        cache.add(key, 1, timeout=UNKNOWN_WINDOW_SECONDS)
        return
    if attempts > UNKNOWN_ATTEMPT_LIMIT:
        raise ValidationError("登录尝试过多，请稍后再试。", code="unknown_login_throttled")
