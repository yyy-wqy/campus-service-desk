from django.core.exceptions import PermissionDenied

from accounts.models import User

from .models import Ticket


def is_approved_business_user(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and user.is_active
        and user.role in User.Role.values
        and user.approval_status == User.ApprovalStatus.APPROVED
        and (user.role != User.Role.MAINTENANCE or user.can_handle_maintenance)
    )


def can_create_ticket(user):
    return is_approved_business_user(user) and user.role in {
        User.Role.STUDENT,
        User.Role.TEACHER,
    }


def can_view_ticket(user, ticket):
    if not is_approved_business_user(user):
        return False
    if user.role == User.Role.ADMIN:
        return True
    if user.role in {User.Role.STUDENT, User.Role.TEACHER}:
        return ticket.reporter_id == user.pk
    if user.role == User.Role.MAINTENANCE:
        return ticket.current_assignee_id == user.pk
    return False


def can_process_ticket(user, ticket):
    return (
        is_approved_business_user(user)
        and user.role == User.Role.MAINTENANCE
        and ticket.current_assignee_id == user.pk
    )


def can_confirm_ticket(user, ticket):
    return (
        is_approved_business_user(user)
        and user.role in {User.Role.STUDENT, User.Role.TEACHER}
        and ticket.reporter_id == user.pk
    )


def can_manage_ticket(user):
    return is_approved_business_user(user) and user.role == User.Role.ADMIN


def can_transition_ticket(user, ticket, to_status):
    """Check the business actor for a concrete status transition."""

    if ticket.status == Ticket.Status.PENDING_ACCEPTANCE:
        if to_status in {Ticket.Status.PENDING_WORK, Ticket.Status.REJECTED}:
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
    if not is_approved_business_user(user):
        return Ticket.objects.none()
    return Ticket.objects.visible_to(user)


def require_create_permission(user):
    if not can_create_ticket(user):
        raise PermissionDenied("当前账号无权提交报修工单。")
