from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .forms import TicketCreateForm
from .models import Ticket
from .permissions import can_create_ticket, can_manage_ticket, visible_tickets
from .services import (
    accept_and_assign_ticket,
    reject_ticket,
    set_ticket_priority,
)
from .workflow_forms import (
    TicketAssignmentForm,
    TicketPriorityForm,
    TicketRejectForm,
)


@login_required
@require_http_methods(["GET", "POST"])
def ticket_create(request):
    if not can_create_ticket(request.user):
        return HttpResponseForbidden("当前账号无权提交报修工单。")

    form_data = request.POST if request.method == "POST" else None
    form = TicketCreateForm(form_data, reporter=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            ticket = form.save()
        except ValidationError as exc:
            form.add_service_error(exc)
        else:
            messages.success(request, f"报修提交成功，工单编号：{ticket.number}")
            return redirect("tickets:detail", pk=ticket.pk)
    return render(request, "tickets/ticket_form.html", {"form": form})


@login_required
@require_http_methods(["GET"])
def ticket_list(request):
    tickets = visible_tickets(request.user).select_related(
        "reporter",
        "current_assignee",
        "building",
        "fault_category",
    )
    return render(request, "tickets/ticket_list.html", {"tickets": tickets})


@login_required
@require_http_methods(["GET"])
def ticket_detail(request, pk):
    ticket = get_object_or_404(
        visible_tickets(request.user).select_related(
            "reporter",
            "current_assignee",
            "building",
            "fault_category",
        ),
        pk=pk,
    )
    context = {
        "ticket": ticket,
        "history": ticket.history.select_related("actor"),
    }
    if can_manage_ticket(request.user):
        context.update(
            {
                "priority_form": TicketPriorityForm(
                    initial={"priority": ticket.priority}
                ),
                "reject_form": TicketRejectForm(),
                "assignment_form": TicketAssignmentForm(),
            }
        )
    return render(request, "tickets/ticket_detail.html", context)


def _form_error_text(form):
    return "；".join(
        str(message)
        for field_errors in form.errors.values()
        for message in field_errors
    )


def _validation_error_text(error):
    if hasattr(error, "message_dict"):
        return "；".join(
            message
            for field_errors in error.message_dict.values()
            for message in field_errors
        )
    return "；".join(error.messages)


def _admin_ticket_or_forbidden(request, pk):
    if not can_manage_ticket(request.user):
        return None, HttpResponseForbidden("当前账号无权管理工单。")
    return get_object_or_404(Ticket, pk=pk), None


@login_required
@require_POST
def ticket_accept(request, pk):
    ticket, denied = _admin_ticket_or_forbidden(request, pk)
    if denied:
        return denied
    form = TicketAssignmentForm(request.POST)
    if not form.is_valid():
        messages.error(request, _form_error_text(form))
    else:
        try:
            accept_and_assign_ticket(
                ticket=ticket,
                actor=request.user,
                worker=form.cleaned_data["worker"],
            )
        except ValidationError as exc:
            messages.error(request, _validation_error_text(exc))
        else:
            messages.success(request, "工单已受理并完成首次分派。")
    return redirect("tickets:detail", pk=pk)


@login_required
@require_POST
def ticket_reject(request, pk):
    ticket, denied = _admin_ticket_or_forbidden(request, pk)
    if denied:
        return denied
    form = TicketRejectForm(request.POST)
    if not form.is_valid():
        messages.error(request, _form_error_text(form))
    else:
        try:
            reject_ticket(
                ticket=ticket,
                actor=request.user,
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as exc:
            messages.error(request, _validation_error_text(exc))
        else:
            messages.success(request, "工单已设为不予受理，原因已写入历史。")
    return redirect("tickets:detail", pk=pk)


@login_required
@require_POST
def ticket_set_priority(request, pk):
    ticket, denied = _admin_ticket_or_forbidden(request, pk)
    if denied:
        return denied
    form = TicketPriorityForm(request.POST)
    if not form.is_valid():
        messages.error(request, _form_error_text(form))
    else:
        try:
            set_ticket_priority(
                ticket=ticket,
                actor=request.user,
                priority=form.cleaned_data["priority"],
            )
        except ValidationError as exc:
            messages.error(request, _validation_error_text(exc))
        else:
            messages.success(request, "工单优先级已更新。")
    return redirect("tickets:detail", pk=pk)
