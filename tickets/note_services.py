import time

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, OperationalError, connection, transaction
from django.db.models import F

from accounts.models import User

from .models import Ticket, TicketHistory
from .permissions import can_view_ticket


MAX_NOTE_LENGTH = 2000

# 工作方案：终态只读，最终以组长确认的规则为准。
NOTE_ALLOWED_STATUSES = {
    Ticket.Status.PENDING_ACCEPTANCE,
    Ticket.Status.PENDING_WORK,
    Ticket.Status.IN_PROGRESS,
    Ticket.Status.PENDING_CONFIRMATION,
}


def can_add_ticket_note(actor, ticket):
    return (
        can_view_ticket(actor, ticket)
        and ticket.status in NOTE_ALLOWED_STATUSES
    )


def _add_note_once(*, ticket_id, actor_id, text):
    with transaction.atomic():
        # SQLite 不支持行级 select_for_update，先取得写锁，
        # 再读取当前处理人、状态和账号权限。
        # updated_at 保持原值，不把说明当作工单状态更新。
        if connection.vendor == "sqlite":
            Ticket.objects.filter(pk=ticket_id).update(
                updated_at=F("updated_at")
            )

        ticket = Ticket.objects.select_for_update().get(pk=ticket_id)
        actor = User.objects.select_for_update().filter(pk=actor_id).first()

        if not can_view_ticket(actor, ticket):
            raise PermissionDenied("当前账号无权补充该工单。")

        if ticket.status not in NOTE_ALLOWED_STATUSES:
            raise ValidationError("该工单已进入终态，只能查看，不能追加说明。")

        # 与后续资质模块兼容；当前主分支尚未提供此属性。
        if (
            actor.role == User.Role.MAINTENANCE
            and not getattr(actor, "can_handle_maintenance", True)
        ):
            raise PermissionDenied("当前维修资质不允许补充工单说明。")

        return TicketHistory.objects.create(
            ticket=ticket,
            event_type=TicketHistory.EventType.NOTE_ADDED,
            actor=actor,
            from_status=ticket.status,
            to_status=ticket.status,
            description=text,
        )


def add_ticket_note(*, ticket_id, actor, text):
    if not isinstance(text, str):
        raise ValidationError("请填写说明内容。")
    text = text.strip()
    if not text:
        raise ValidationError("说明内容不能为空。")
    if len(text) > MAX_NOTE_LENGTH:
        raise ValidationError("说明内容不能超过2000字。")

    actor_id = getattr(actor, "pk", None)
    for attempt in range(4):
        try:
            return _add_note_once(
                ticket_id=ticket_id,
                actor_id=actor_id,
                text=text,
            )
        except OperationalError as exc:
            if (
                connection.vendor == "sqlite"
                and "locked" in str(exc).lower()
                and attempt < 3
            ):
                time.sleep(0.05 * (attempt + 1))
                continue
            raise ValidationError(
                "说明暂时无法保存，本次未追加，请稍后重试。"
            ) from exc
        except DatabaseError as exc:
            raise ValidationError(
                "说明暂时无法保存，本次未追加，请稍后重试。"
            ) from exc
