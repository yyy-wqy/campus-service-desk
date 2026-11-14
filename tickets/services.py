import time

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import User
from locations.scope import validate_repair_scope

from .models import Ticket, TicketDailySequence, TicketHistory
from .permissions import can_manage_ticket, can_transition_ticket, require_create_permission


class TicketNumberExhausted(ValidationError):
    pass


class TicketCreationFailed(ValidationError):
    pass


class TicketOperationFailed(ValidationError):
    pass


TICKET_OPERATION_RETRIES = 5


def _run_ticket_operation(operation):
    """Run a complete ticket mutation with safe feedback and lock retries."""

    for attempt in range(TICKET_OPERATION_RETRIES):
        try:
            return operation()
        except (PermissionDenied, ValidationError):
            raise
        except OperationalError as exc:
            if "locked" in str(exc).lower() and attempt < TICKET_OPERATION_RETRIES - 1:
                time.sleep(0.02 * (attempt + 1))
                continue
            raise TicketOperationFailed(
                "系统暂时繁忙，工单未发生变化，请稍后重试。"
            ) from exc
        except DatabaseError as exc:
            raise TicketOperationFailed(
                "工单操作暂时无法完成，数据未发生变化，请稍后重试。"
            ) from exc
        except Exception as exc:
            raise TicketOperationFailed(
                "工单操作暂时无法完成，数据未发生变化，请稍后重试。"
            ) from exc
    raise RuntimeError("unreachable")


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
        reporter = User.objects.filter(pk=getattr(reporter, "pk", None)).first()
        require_create_permission(reporter)
        normalized_location = validate_repair_scope(
            reporter=reporter,
            building=building,
            fault_category=fault_category,
            location_kind=location_kind,
            room_number=room_number,
            public_area=public_area,
            public_area_other=public_area_other,
        )
        reporter = normalized_location["reporter"]
        building = normalized_location["building"]
        fault_category = normalized_location["fault_category"]
        if not (reporter.phone or "").strip():
            raise ValidationError(
                {"reporter": "请先在个人资料中补齐手机号后再提交报修。"}
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


def _transition_ticket_once(*, ticket, actor, to_status, description="", metadata=None):
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
            metadata=metadata or {},
        )
        return locked


def transition_ticket(*, ticket, actor, to_status, description="", metadata=None):
    """Shared atomic status/history primitive for Issues #21-#24."""

    return _run_ticket_operation(
        lambda: _transition_ticket_once(
            ticket=ticket,
            actor=actor,
            to_status=to_status,
            description=description,
            metadata=metadata,
        )
    )


PRIORITY_EDITABLE_STATUSES = {
    Ticket.Status.PENDING_ACCEPTANCE,
    Ticket.Status.PENDING_WORK,
}


def _current_manager(actor):
    actor = User.objects.filter(pk=getattr(actor, "pk", None)).first()
    if not can_manage_ticket(actor):
        raise PermissionDenied("当前账号无权管理工单。")
    return actor


def _eligible_worker_for_update(worker):
    worker = (
        User.objects.select_for_update()
        .filter(pk=getattr(worker, "pk", None))
        .first()
    )
    if worker is None or not worker.can_handle_maintenance:
        raise ValidationError(
            {"worker": "只能分派给账号和维修资质均已通过且仍启用的维修人员。"}
        )
    return worker


def accept_and_assign_ticket(*, ticket, actor, worker):
    """Atomically accept a pending ticket and perform its first assignment."""

    def operation():
        with transaction.atomic():
            manager = _current_manager(actor)
            locked = Ticket.objects.select_for_update().get(pk=ticket.pk)
            if locked.status != Ticket.Status.PENDING_ACCEPTANCE:
                raise ValidationError(
                    {"worker": "只有待受理工单可以受理并首次分派。"}
                )
            if locked.current_assignee_id is not None:
                raise ValidationError(
                    {"worker": "工单已经存在处理人，不能再次首次分派。"}
                )
            current_worker = _eligible_worker_for_update(worker)
            old_status = locked.status
            locked.status = Ticket.Status.PENDING_WORK
            locked.current_assignee = current_worker
            locked.full_clean()
            locked.save(update_fields=("status", "current_assignee", "updated_at"))
            TicketHistory.objects.create(
                ticket=locked,
                event_type=TicketHistory.EventType.STATUS_CHANGED,
                actor=manager,
                from_status=old_status,
                to_status=Ticket.Status.PENDING_WORK,
                description="管理员受理工单并完成首次分派。",
                metadata={"action": "accepted"},
            )
            TicketHistory.objects.create(
                ticket=locked,
                event_type=TicketHistory.EventType.ASSIGNED,
                actor=manager,
                from_status=old_status,
                to_status=Ticket.Status.PENDING_WORK,
                description=(
                    "首次分派给维修人员："
                    f"{current_worker.real_name or current_worker.username}"
                    f"（{current_worker.username}）。"
                ),
                metadata={
                    "assignee_id": current_worker.pk,
                    "assignee_username": current_worker.username,
                    "assignee_name": current_worker.real_name,
                },
            )
            return locked

    return _run_ticket_operation(operation)


def reject_ticket(*, ticket, actor, reason):
    """Permanently reject a pending ticket and retain the mandatory reason."""

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "不予受理必须填写原因。"})
    return _run_ticket_operation(
        lambda: _transition_ticket_once(
            ticket=ticket,
            actor=_current_manager(actor),
            to_status=Ticket.Status.REJECTED,
            description=f"不予受理：{reason}",
            metadata={"action": "rejected", "reason": reason},
        )
    )


def set_ticket_priority(*, ticket, actor, priority):
    if priority not in Ticket.Priority.values:
        raise ValidationError({"priority": "请选择有效的工单优先级。"})

    def operation():
        with transaction.atomic():
            manager = _current_manager(actor)
            locked = Ticket.objects.select_for_update().get(pk=ticket.pk)
            if locked.status not in PRIORITY_EDITABLE_STATUSES:
                raise ValidationError(
                    {"priority": "只有待受理或待处理工单可以修改优先级。"}
                )
            if locked.priority == priority:
                raise ValidationError({"priority": "工单已经是该优先级。"})
            old_priority = locked.priority
            locked.priority = priority
            locked.save(update_fields=("priority", "updated_at"))
            TicketHistory.objects.create(
                ticket=locked,
                event_type=TicketHistory.EventType.PRIORITY_CHANGED,
                actor=manager,
                from_status=locked.status,
                to_status=locked.status,
                description=(
                    f"优先级由 {Ticket.Priority(old_priority).label} 调整为 "
                    f"{Ticket.Priority(priority).label}。"
                ),
                metadata={
                    "from_priority": old_priority,
                    "to_priority": priority,
                },
            )
            return locked

    return _run_ticket_operation(operation)
