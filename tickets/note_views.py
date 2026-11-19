from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .note_forms import TicketNoteForm
from .note_services import add_ticket_note
from .permissions import visible_tickets
from .views import ticket_detail_context


@login_required
@require_POST
def ticket_note_add(request, pk):
    ticket = get_object_or_404(visible_tickets(request.user), pk=pk)

    # 接口只接受说明正文，拒绝伪造记录人或夹带业务字段。
    allowed_fields = {"csrfmiddlewaretoken", "text"}
    if set(request.POST) - allowed_fields or request.FILES:
        raise PermissionDenied("追加说明接口只接受说明正文。")

    form = TicketNoteForm(request.POST)
    if form.is_valid():
        try:
            add_ticket_note(
                ticket_id=ticket.pk,
                actor=request.user,
                text=form.cleaned_data["text"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "说明已追加，原始报修信息和工单状态未改变。")
            return redirect("tickets:detail", pk=ticket.pk)

    # 保存失败后重新检查查看权限及工单当前状态。
    ticket = get_object_or_404(visible_tickets(request.user), pk=pk)
    return render(
        request,
        "tickets/ticket_detail.html",
        ticket_detail_context(request.user, ticket, note_form=form),
    )
