import time

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from locations.scope import validate_repair_scope

from .models import Ticket, TicketDailySequence, TicketHistory
from .permissions import can_transition_ticket, require_create_permission


class TicketNumberExhausted(ValidationError):
    pass


class TicketCreationFailed(ValidationError):
    pass


def _next_ticket_number(business_date):
    sequence, _ = TicketDailySequence.objects.get_or_create(
        business_date=business_date,
        defaults={"last_value": 0},
    )
    advanced = TicketDailySequence.objects.filter(
        pk=sequence.pk,
        last_value__lt=9999,
    ).update(last_value=F("last_value") + 1)
    if not advanced:
        raise TicketNumberExhausted("当日工单编号已用尽，请联系管理员。")
    sequence.refresh_from_db(fields=("last_value",))
    return f"CS{business_date:%Y%m%d}{sequence.last_value:04d}"


def _create_ticket_once(
    *,
    reporter,
    building,
    fault_category,
    title,
    description,
    location_kind,
    room_number="",
    public_area=None,
    public_area_other="",
    preferred_visit_time="",
):
    with transaction.atomic():
        require_create_permission(reporter)
        if not (reporter.phone or "").strip():
            raise ValidationError(
                {"reporter": "请先在个人资料中补齐手机号后再提交报修。"}
            )
        normalized_location = validate_repair_scope(
            reporter=reporter,
            building=building,
            fault_category=fault_category,
            location_kind=location_kind,
            room_number=room_number,
            public_area=public_area,
            public_area_other=public_area_other,
        )
        number = _next_ticket_number(timezone.localdate())
        ticket = Ticket(
            number=number,
            reporter=reporter,
            building=building,
            fault_category=fault_category,
            title=title,
            description=description,
            location_kind=normalized_location["location_kind"],
            location_detail=normalized_location["location_detail"],
            preferred_visit_time=preferred_visit_time,
            contact_name_snapshot=reporter.real_name or reporter.username,
            contact_phone_snapshot=reporter.phone,
            reporter_role_snapshot=reporter.role,
            building_name_snapshot=building.name,
            building_type_snapshot=building.building_type,
            category_name_snapshot=fault_category.name,
            category_teacher_only_snapshot=fault_category.is_teacher_only,
            priority=Ticket.Priority.NORMAL,
            status=Ticket.Status.PENDING_ACCEPTANCE,
        )
        ticket.full_clean()
        ticket.save()
        try:
            TicketHistory.objects.create(
                ticket=ticket,
                event_type=TicketHistory.EventType.CREATED,
                actor=reporter,
                to_status=Ticket.Status.PENDING_ACCEPTANCE,
                description="报修人提交工单，等待管理员受理。",
                metadata={"number": ticket.number},
            )
        except OperationalError:
            raise
        except Exception as exc:
            raise TicketCreationFailed(
                "工单暂时无法提交，请稍后重试。"
            ) from exc
        return ticket


def create_ticket(**kwargs):
    """Create a ticket and its first history entry in one transaction.

    SQLite can briefly reject concurrent writers. A short bounded retry keeps
    parallel submissions reliable without hiding other database errors.
    """

    for attempt in range(5):
        try:
            return _create_ticket_once(**kwargs)
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise TicketCreationFailed(
                    "系统暂时繁忙，请稍后重试。"
                ) from exc
            if attempt == 4:
                raise TicketCreationFailed(
                    "系统暂时繁忙，请稍后重试。"
                ) from exc
            time.sleep(0.02 * (attempt + 1))
        except DatabaseError as exc:
            raise TicketCreationFailed(
                "工单暂时无法提交，请稍后重试。"
            ) from exc
    raise RuntimeError("unreachable")


ALLOWED_STATUS_TRANSITIONS = {
    Ticket.Status.PENDING_ACCEPTANCE: {
        Ticket.Status.PENDING_WORK,
        Ticket.Status.REJECTED,
        Ticket.Status.CANCELLED,
    },
    Ticket.Status.PENDING_WORK: {
        Ticket.Status.IN_PROGRESS,
        Ticket.Status.CANCELLED,
    },
    Ticket.Status.IN_PROGRESS: {
        Ticket.Status.PENDING_CONFIRMATION,
        Ticket.Status.CANCELLED,
    },
    Ticket.Status.PENDING_CONFIRMATION: {
        Ticket.Status.IN_PROGRESS,
        Ticket.Status.COMPLETED,
        Ticket.Status.CANCELLED,
    },
}


def transition_ticket(*, ticket, actor, to_status, description=""):
    """Shared atomic status/history primitive for Issues #21-#24.

    This primitive deliberately does not impersonate a business role. Callers
    must perform the action-specific permission check before invoking it.
    """

    if to_status not in ALLOWED_STATUS_TRANSITIONS.get(ticket.status, set()):
        raise ValidationError({"status": "不允许执行该工单状态变更。"})
    with transaction.atomic():
        locked = Ticket.objects.select_for_update().get(pk=ticket.pk)
        if to_status not in ALLOWED_STATUS_TRANSITIONS.get(locked.status, set()):
            raise ValidationError({"status": "工单状态已变化，请刷新后重试。"})
        if not can_transition_ticket(actor, locked, to_status):
            raise PermissionDenied("当前账号无权执行该状态变更。")
        old_status = locked.status
        locked.status = to_status
        locked.save(update_fields=("status", "updated_at"))
        TicketHistory.objects.create(
            ticket=locked,
            event_type=TicketHistory.EventType.STATUS_CHANGED,
            actor=actor,
            from_status=old_status,
            to_status=to_status,
            description=description,
        )
        return locked
