from functools import wraps
import time

from django.db import OperationalError, connection


def retry_sqlite_lock(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        for attempt in range(100):
            try:
                return view(*args, **kwargs)
            except OperationalError as error:
                if connection.vendor != "sqlite" or "locked" not in str(error).lower() or attempt == 99:
                    raise
                time.sleep(0.05)
    return wrapped
