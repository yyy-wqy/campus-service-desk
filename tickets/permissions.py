from django.core.exceptions import PermissionDenied

from accounts.models import User

from .models import Ticket


def current_business_user(user):
    """Resolve permissions from persisted state, never a cached session object."""

    if not getattr(user, "is_authenticated", False):
        return None
    current = User.objects.filter(pk=getattr(user, "pk", None)).first()
    if not current or not current.is_active:
        return None
    if current.approval_status != User.ApprovalStatus.APPROVED:
        return None
    if current.role not in User.Role.values:
        return None
    if current.role == User.Role.MAINTENANCE and not current.can_handle_maintenance:
        return None
    return current


def is_approved_business_user(user):
    return current_business_user(user) is not None


def can_create_ticket(user):
    current = current_business_user(user)
    return current is not None and current.role in {
        User.Role.STUDENT,
        User.Role.TEACHER,
    }


def can_view_ticket(user, ticket):
    current = current_business_user(user)
    if current is None:
        return False
    if current.role == User.Role.ADMIN:
        return True
    if current.role in {User.Role.STUDENT, User.Role.TEACHER}:
        return ticket.reporter_id == current.pk
    if current.role == User.Role.MAINTENANCE:
        return ticket.current_assignee_id == current.pk
    return False


def can_process_ticket(user, ticket):
    current = current_business_user(user)
    return (
        current is not None
        and current.role == User.Role.MAINTENANCE
        and ticket.current_assignee_id == current.pk
    )


def can_confirm_ticket(user, ticket):
    current = current_business_user(user)
    return (
        current is not None
        and current.role in {User.Role.STUDENT, User.Role.TEACHER}
        and ticket.reporter_id == current.pk
    )


def can_manage_ticket(user):
    current = current_business_user(user)
    return current is not None and current.role == User.Role.ADMIN


def can_transition_ticket(user, ticket, to_status):
    """Check the business actor for a concrete status transition."""

    if ticket.status == Ticket.Status.PENDING_ACCEPTANCE:
        if to_status == Ticket.Status.REJECTED:
            return can_manage_ticket(user)
        if to_status == Ticket.Status.CANCELLED:
            return can_confirm_ticket(user, ticket)
    if ticket.status == Ticket.Status.PENDING_WORK:
        if to_status == Ticket.Status.IN_PROGRESS:
            return can_process_ticket(user, ticket)
        if to_status == Ticket.Status.CANCELLED:
            return can_manage_ticket(user)
    if ticket.status == Ticket.Status.IN_PROGRESS:
        if to_status == Ticket.Status.PENDING_CONFIRMATION:
            return can_process_ticket(user, ticket)
        if to_status == Ticket.Status.CANCELLED:
            return can_manage_ticket(user)
    if ticket.status == Ticket.Status.PENDING_CONFIRMATION:
        if to_status in {Ticket.Status.IN_PROGRESS, Ticket.Status.COMPLETED}:
            return can_confirm_ticket(user, ticket)
        if to_status == Ticket.Status.CANCELLED:
            return can_manage_ticket(user)
    return False


def visible_tickets(user):
    return Ticket.objects.visible_to(user)


def require_create_permission(user):
    current = current_business_user(user)
    if current is None or current.role not in {
        User.Role.STUDENT,
        User.Role.TEACHER,
    }:
        raise PermissionDenied("当前账号无权提交报修工单。")
    return current
