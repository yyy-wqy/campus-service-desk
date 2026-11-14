from django.db import connection


LOCK_CONFLICT_MESSAGE = (
    "当前有其他操作正在更新数据，本次保存未成功，请刷新页面后重试。"
)


def is_sqlite_lock_error(exc):
    return (
        connection.vendor == "sqlite"
        and "locked" in str(exc).lower()
    )
