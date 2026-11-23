from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import User

from .models import Ticket, TicketCancellation, TicketHistory
from .permissions import can_confirm_ticket, can_manage_ticket
from .services import _run_ticket_operation, lock_ticket_for_workflow


AUDITED_CANCELLATION_STATES = {Ticket.Status.PENDING_WORK, Ticket.Status.IN_PROGRESS}


def can_request_cancellation(ticket):
    return not ticket.cancellation_pending and (
        ticket.status == Ticket.Status.PENDING_ACCEPTANCE
        or ticket.status in AUDITED_CANCELLATION_STATES
    )


def _append_history(application, actor, before, description):
    TicketHistory.objects.create(
        ticket=application.ticket,
        event_type=TicketHistory.EventType.CANCELLATION_CHANGED,
        actor=actor,
        from_status=before,
        to_status=application.ticket.status,
        description=description,
        metadata={"cancellation_id": application.pk, "decision": application.decision},
    )


@transaction.atomic
def _submit_cancellation_once(*, ticket, actor, reason=""):
    ticket = lock_ticket_for_workflow(ticket.pk)
    actor = User.objects.select_for_update().filter(pk=getattr(actor, "pk", None)).first()
    if not can_confirm_ticket(actor, ticket):
        raise PermissionDenied("只有报修人可以提交取消申请。")
    if ticket.cancellation_pending or ticket.cancellations.filter(
        decision=TicketCancellation.Decision.PENDING
    ).exists():
        raise ValidationError("这张工单已有一条待审核的取消申请。")
    if not can_request_cancellation(ticket):
        raise ValidationError("当前工单状态不可申请取消。")
    if not isinstance(reason, str):
        raise ValidationError("取消原因格式无效。")
    reason = reason.strip()
    if len(reason) > 1000:
        raise ValidationError("申请原因最多 1000 字。")
    before = ticket.status
    if before == Ticket.Status.PENDING_ACCEPTANCE:
        if not reason:
            raise ValidationError("请填写取消原因。")
        decision = TicketCancellation.Decision.DIRECT
        ticket.status = Ticket.Status.CANCELLED
    else:
        decision = TicketCancellation.Decision.PENDING
        ticket.cancellation_pending = True

    application = TicketCancellation.objects.create(
        ticket=ticket, applicant=actor, reason=reason, decision=decision,
    )
    ticket.repair_version += 1
    ticket.save(update_fields=("status", "cancellation_pending", "repair_version", "updated_at"))
    _append_history(application, actor, before, reason or "报修人提交取消申请。")
    return application


@transaction.atomic
def _review_cancellation_once(*, application, actor, decision, reason=""):
    ticket = lock_ticket_for_workflow(application.ticket_id)
    actor = User.objects.select_for_update().filter(pk=getattr(actor, "pk", None)).first()
    if not can_manage_ticket(actor):
        raise PermissionDenied("只有管理员可以审核取消申请。")
    application = TicketCancellation.objects.get(pk=application.pk, ticket=ticket)
    if application.decision != TicketCancellation.Decision.PENDING or not ticket.cancellation_pending:
        raise ValidationError("这条取消申请已处理，请刷新页面。")
    if decision not in {TicketCancellation.Decision.APPROVED, TicketCancellation.Decision.REJECTED}:
        raise ValidationError("请选择有效的审核结果。")
    if not isinstance(reason, str):
        raise ValidationError("审核说明格式无效。")
    reason = reason.strip()
    if len(reason) > 1000:
        raise ValidationError("审核说明最多 1000 字。")
    if decision == TicketCancellation.Decision.REJECTED and not reason:
        raise ValidationError("驳回时必须填写原因。")
    before = ticket.status
    if decision == TicketCancellation.Decision.APPROVED:
        if before not in AUDITED_CANCELLATION_STATES:
            raise ValidationError("工单状态已变化，请刷新工单后再处理。")
        ticket.status = Ticket.Status.CANCELLED
    ticket.cancellation_pending = False
    ticket.repair_version += 1
    ticket.save(update_fields=("status", "cancellation_pending", "repair_version", "updated_at"))
    application.ticket = ticket
    application.decision = decision
    application.reviewer = actor
    application.review_reason = reason
    application.reviewed_at = timezone.now()
    application.save(update_fields=("decision", "reviewer", "review_reason", "reviewed_at"))
    _append_history(application, actor, before, reason or application.get_decision_display())
    return application


def submit_cancellation(*, ticket, actor, reason=""):
    return _run_ticket_operation(
        lambda: _submit_cancellation_once(ticket=ticket, actor=actor, reason=reason)
    )


def review_cancellation(*, application, actor, decision, reason=""):
    return _run_ticket_operation(
        lambda: _review_cancellation_once(
            application=application, actor=actor, decision=decision, reason=reason,
        )
    )
