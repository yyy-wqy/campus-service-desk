from functools import wraps
import time

from django.db import OperationalError, connection
from django.http import HttpResponse
from django.urls import reverse
from django.utils.html import format_html


def retry_repair_request(view):
    """重试请求读取时的短暂 SQLite 锁冲突。"""
    @wraps(view)
    def wrapped(request, pk, action):
        for attempt in range(4):
            try:
                return view(request, pk, action)
            except OperationalError as exc:
                if connection.vendor != "sqlite" or "locked" not in str(exc).lower():
                    raise
                if attempt < 3:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                response = HttpResponse(
                    format_html(
                        '<p>系统繁忙，请返回工单详情核对状态后重试。</p>'
                        '<p><a href="{}">返回工单详情</a></p>',
                        reverse("tickets:detail", kwargs={"pk": pk}),
                    ),
                    status=503,
                )
                response["Retry-After"] = "1"
                return response
    return wrapped
