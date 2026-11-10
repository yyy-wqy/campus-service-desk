from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .database_retry import retry_sqlite_lock
from .material_forms import MaterialForm, MaintenanceReviewForm
from .material_services import (
    deactivate_worker, private_storage, require_admin, retire_material,
    review_application, submit_application, upload_material,
)
from .models import MaintenanceApplication as Application, MaintenanceMaterial, User


def own_application(user):
    if user.role != User.Role.MAINTENANCE:
        raise PermissionDenied
    return Application.objects.get_or_create(worker=user)[0]


def render_own(request, form=None):
    app = own_application(request.user)
    return render(request, "accounts/qualifications.html", {
        "application": app, "form": form or MaterialForm(),
        "materials": app.materials.filter(active=True),
        "history": app.reviews.select_related("reviewer").prefetch_related("materials").order_by("-id"),
        "editable": app.status in {"draft", "needs_more"},
    })


@login_required
@require_http_methods(["GET"])
def qualifications(request):
    return render_own(request)


@login_required
@require_POST
@retry_sqlite_lock
def upload(request):
    app = own_application(request.user)
    # Ownership comes from the session. Reject forged target identifiers.
    if any(request.POST.get(key) not in (None, "", str(app.pk if key in {"application", "application_id"} else request.user.pk)) for key in ("application", "application_id", "worker", "worker_id", "user", "user_id")):
        raise PermissionDenied
    form = MaterialForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            upload_material(app.pk, request.user, form)
        except ValidationError as error:
            form.add_error(None, error)
        except OSError:
            form.add_error(None, "材料保存失败，请稍后重试；若持续失败，请联系管理员。")
        else:
            messages.success(request, "材料已私有保存。")
            return redirect("accounts:qualifications")
    return render_own(request, form)


@login_required
@require_POST
@retry_sqlite_lock
def submit(request):
    app = own_application(request.user)
    try:
        submit_application(app.pk, request.user)
    except ValidationError as error:
        messages.error(request, "；".join(error.messages))
    return redirect("accounts:qualifications")


@login_required
@require_POST
@retry_sqlite_lock
def retire(request, material_id):
    material = get_object_or_404(MaintenanceMaterial, pk=material_id, application__worker=request.user)
    try:
        retire_material(material, request.user)
    except ValidationError as error:
        messages.error(request, "；".join(error.messages))
    return redirect("accounts:qualifications")


@login_required
@require_http_methods(["GET"])
def download(request, material_id):
    material = get_object_or_404(MaintenanceMaterial.objects.select_related("application"), pk=material_id)
    if material.application.worker_id != request.user.pk:
        require_admin(request.user)
    storage = private_storage()
    if material.disposed_at or not storage.exists(material.storage_name):
        raise Http404
    response = FileResponse(storage.open(material.storage_name, "rb"), as_attachment=True, filename=material.original_name, content_type="application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_http_methods(["GET", "POST"])
@retry_sqlite_lock
def review(request, application_id=None):
    require_admin(request.user)
    app = get_object_or_404(Application.objects.select_related("worker"), pk=application_id) if application_id else None
    form = MaintenanceReviewForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and app and form.is_valid():
        try:
            review_application(app.pk, request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "审核已记录。")
            return redirect("accounts:maintenance_review", application_id=app.pk)
    return render(request, "accounts/maintenance_review.html", {
        "application": app, "form": form,
        "applications": Application.objects.select_related("worker").order_by("id"),
        "materials": app.materials.filter(active=True) if app else [],
        "history": app.reviews.select_related("reviewer").prefetch_related("materials").order_by("-id") if app else [],
        "deactivations": app.worker.deactivation_events.select_related("actor").order_by("-created_at") if app else [],
        "disposals": app.worker.materialdisposal_set.select_related("actor").prefetch_related("materials").order_by("-created_at") if app else [],
    })


@login_required
@require_POST
@retry_sqlite_lock
def deactivate(request, worker_id):
    require_admin(request.user)
    worker = get_object_or_404(User, pk=worker_id, role=User.Role.MAINTENANCE)
    try:
        deactivate_worker(worker, request.user, request.POST.get("reason", ""))
    except ValidationError as error:
        messages.error(request, "；".join(error.messages))
    return redirect("accounts:maintenance_reviews")
