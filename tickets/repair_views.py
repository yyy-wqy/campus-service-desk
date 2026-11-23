from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .permissions import can_confirm_ticket, can_process_ticket, visible_tickets
from .repair_forms import REPAIR_FORMS
from .repair_http import retry_repair_request
from .repair_services import (
    RepairConflict,
    add_repair_progress, confirm_repair_result,
    start_repair, submit_repair_result,
)
from .views import ticket_detail_context


ACTIONS = {
    "start": (start_repair, "已开始维修。"),
    "progress": (add_repair_progress, "维修进度已保存。"),
    "result": (submit_repair_result, "维修结果已提交，等待报修人确认。"),
    "confirm": (confirm_repair_result, "确认决定已记录。"),
}


@login_required
@require_POST
@retry_repair_request
def repair_action(request, pk, action):
    if action not in ACTIONS:
        raise Http404

    ticket = get_object_or_404(visible_tickets(request.user), pk=pk)
    permitted = (
        can_confirm_ticket(request.user, ticket) if action == "confirm"
        else can_process_ticket(request.user, ticket)
    )
    if not permitted:
        raise PermissionDenied("当前账号无权执行此操作。")

    form = REPAIR_FORMS[action](
        request.POST, auto_id=f"repair_{action}_%s"
    )
    allowed_fields = set(form.fields) | {"csrfmiddlewaretoken"}
    if request.FILES or set(request.POST) - allowed_fields:
        raise PermissionDenied("请求包含不允许修改的字段。")

    response_status = 200
    if form.is_valid():
        service, success_message = ACTIONS[action]
        try:
            service(
                ticket_id=ticket.pk,
                actor=request.user,
                **form.cleaned_data,
            )
        except ValidationError as exc:
            if isinstance(exc, RepairConflict):
                response_status = 409
            form.add_error(None, exc)
        else:
            messages.success(request, success_message)
            return redirect("tickets:detail", pk=ticket.pk)

    ticket = get_object_or_404(visible_tickets(request.user), pk=pk)
    context = ticket_detail_context(
        request.user, ticket, repair_form=form, repair_action=action
    )
    context["repair_conflict"] = response_status == 409
    if context["repair_conflict"]:
        drafts = []
        for field in form.visible_fields():
            value = field.value()
            choices = getattr(field.field, "choices", None)
            if choices:
                value = dict(choices).get(value, value)
            drafts.append({"name": field.name, "label": field.label, "value": value})
        context["repair_draft_fields"] = drafts
    return render(request, "tickets/ticket_detail.html", context, status=response_status)
