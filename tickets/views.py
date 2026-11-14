from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import TicketCreateForm
from .permissions import can_create_ticket, visible_tickets


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
    return render(request, "tickets/ticket_detail.html", {"ticket": ticket})
