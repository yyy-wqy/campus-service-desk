from functools import wraps
import time

from django.db import OperationalError, connection
from django.http import HttpResponse
from django.urls import reverse
from django.utils.html import format_html


def retry_cancellation_request(view):
    """Retry transient reads as well as mutations during cancellation races."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        for attempt in range(4):
            try:
                return view(request, *args, **kwargs)
            except OperationalError as exc:
                if connection.vendor != "sqlite" or "locked" not in str(exc).lower():
                    raise
                if attempt < 3:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                response = HttpResponse(
                    format_html(
                        '<p>系统繁忙，请重新查看工单和取消申请状态后重试。</p>'
                        '<p><a href="{}">返回工单列表</a></p>',
                        reverse("tickets:list"),
                    ),
                    status=503,
                )
                response["Retry-After"] = "1"
                return response
    return wrapped
