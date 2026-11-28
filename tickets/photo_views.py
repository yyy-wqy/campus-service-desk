from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .cancellation_http import retry_cancellation_request
from .models import TicketPhoto
from .permissions import visible_tickets
from .photo_forms import PhotoUploadForm
from .photo_services import can_upload_photos, read_photo, upload_photos
from .views import ticket_detail_context


@login_required
@require_POST
@retry_cancellation_request
def upload(request, pk):
    ticket = get_object_or_404(visible_tickets(request.user), pk=pk)
    if not can_upload_photos(request.user, ticket):
        raise PermissionDenied
    if set(request.POST) - {"csrfmiddlewaretoken"} or set(request.FILES) - {"photos"}:
        raise PermissionDenied
    form = PhotoUploadForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            upload_photos(ticket_id=pk, actor=request.user, uploads=form.cleaned_data["photos"])
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                for errors in exc.message_dict.values():
                    form.add_error("photos", errors)
            else:
                form.add_error("photos", exc)
        else:
            messages.success(request, "现场照片已追加。")
            return redirect("tickets:detail", pk=pk)
    ticket = get_object_or_404(visible_tickets(request.user), pk=pk)
    context = ticket_detail_context(request.user, ticket)
    context["photo_form"] = form
    return render(request, "tickets/ticket_detail.html", context)


@login_required
@require_GET
@retry_cancellation_request
def read(request, photo_id):
    get_object_or_404(TicketPhoto, pk=photo_id,
                      ticket__in=visible_tickets(request.user), disposed_at=None)
    try:
        photo, data = read_photo(photo_id=photo_id, actor=request.user)
    except TicketPhoto.DoesNotExist:
        raise Http404
    except ValidationError:
        return HttpResponse("照片暂时不可用，请稍后重试或联系管理员。", status=503)
    response = HttpResponse(data, content_type=photo.content_type)
    response["Content-Disposition"] = f'inline; filename="photo-{photo.pk}.{ "png" if photo.content_type == "image/png" else "jpg"}"'
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Vary"] = "Cookie"
    response["X-Content-Type-Options"] = "nosniff"
    return response
