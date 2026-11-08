import time

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import User
from locations.models import Building, FaultCategory

from .models import Ticket, TicketDailySequence, TicketHistory
from .permissions import can_transition_ticket, require_create_permission


class TicketNumberExhausted(ValidationError):
    pass


def validate_submission_scope(reporter, building, fault_category):
    require_create_permission(reporter)
    errors = {}
    if not building.is_active:
        errors["building"] = "该楼栋已停用，请重新选择。"
    if not fault_category.is_active:
        errors["fault_category"] = "该故障类别已停用，请重新选择。"

    if reporter.role == User.Role.TEACHER and building.building_type != "teach":
        errors["building"] = "教师只能提交教学楼报修。"
    if reporter.role == User.Role.STUDENT:
        if fault_category.is_teacher_only:
            errors["fault_category"] = "该故障类别仅限教师报修。"
        if building.building_type == "dorm":
            # Issue #19 尚未提供住宿档案；在其接入前不能仅凭表单选择宿舍楼。
            errors["building"] = "宿舍报修需先完成住宿信息绑定。"

    if errors:
        raise ValidationError(errors)


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
    location_detail,
    preferred_visit_time="",
):
    with transaction.atomic():
        validate_submission_scope(reporter, building, fault_category)
        number = _next_ticket_number(timezone.localdate())
        ticket = Ticket(
            number=number,
            reporter=reporter,
            building=building,
            fault_category=fault_category,
            title=title,
            description=description,
            location_kind=location_kind,
            location_detail=location_detail,
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
        TicketHistory.objects.create(
            ticket=ticket,
            event_type=TicketHistory.EventType.CREATED,
            actor=reporter,
            to_status=Ticket.Status.PENDING_ACCEPTANCE,
            description="报修人提交工单，等待管理员受理。",
            metadata={"number": ticket.number},
        )
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
            if "locked" not in str(exc).lower() or attempt == 4:
                raise
            time.sleep(0.02 * (attempt + 1))
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
