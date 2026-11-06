import hashlib
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import MaintenanceApplication as Application, MaintenanceDeactivation, MaintenanceMaterial, MaintenanceReview, MaterialDisposal, User


def private_storage():
    return FileSystemStorage(location=settings.PRIVATE_DOCUMENT_ROOT)


def require_admin(actor):
    if not actor.is_authenticated or not actor.is_active or actor.role != User.Role.ADMIN or actor.approval_status != User.ApprovalStatus.APPROVED:
        raise PermissionDenied


def lock_application(application_id):
    Application.objects.filter(pk=application_id).update(version=F("version"))
    return Application.objects.select_related("worker").get(pk=application_id)


def require_editable(app, worker):
    if app.worker_id != worker.pk or app.worker.role != User.Role.MAINTENANCE or not app.worker.is_active:
        raise PermissionDenied
    if app.status not in {Application.Status.DRAFT, Application.Status.NEEDS_MORE}:
        raise ValidationError("当前状态不能修改材料，请等待审核或联系管理员。")


def upload_material(application_id, worker, form):
    saved_name = None
    storage = private_storage()
    try:
        with transaction.atomic():
            app = lock_application(application_id)
            require_editable(app, worker)
            category = form.cleaned_data["category"]
            if app.materials.filter(category=category, active=True).count() >= 3:
                raise ValidationError("每类最多3份材料，请先替换旧材料。")
            material = form.save(commit=False)
            data, content_type, extension = form.validated_file
            saved_name = storage.save(uuid.uuid4().hex + extension, ContentFile(data))
            material.application = app
            material.storage_name = saved_name
            material.original_name = form.cleaned_data["attachment"].name.replace("\\", "/").split("/")[-1][:200]
            material.content_type = content_type
            material.size = len(data)
            material.sha256 = hashlib.sha256(data).hexdigest()
            material.save()
            return material
    except Exception:
        if saved_name:
            storage.delete(saved_name)
        raise


def require_complete(app):
    for category in MaintenanceMaterial.Category.values:
        materials = list(app.materials.filter(active=True, category=category, disposed_at__isnull=True))
        if not 1 <= len(materials) <= 3:
            raise ValidationError("工作关系证明、专业资质证明每类需要1至3份。")
        for material in materials:
            if not private_storage().exists(material.storage_name):
                raise ValidationError("附件缺失，请联系管理员处理。")
            if category == "qualification" and (not material.name or not material.direction):
                raise ValidationError("专业资质名称、方向和附件必填。")


@transaction.atomic
def submit_application(application_id, worker):
    app = lock_application(application_id)
    require_editable(app, worker)
    require_complete(app)
    app.status = Application.Status.PENDING
    app.version += 1
    app.save()
    User.objects.filter(pk=worker.pk).update(approval_status="pending", approval_reason="")
    return app


@transaction.atomic
def retire_material(material, worker):
    app = lock_application(material.application_id)
    require_editable(app, worker)
    MaintenanceMaterial.objects.filter(pk=material.pk, application=app).update(active=False)


@transaction.atomic
def review_application(application_id, actor, result, reason):
    require_admin(actor)
    app = lock_application(application_id)
    if app.status != Application.Status.PENDING or not app.worker.is_active:
        raise ValidationError("仅可审核启用账号的待审核申请。")
    if result not in {"approved", "needs_more", "rejected"}:
        raise ValidationError("无效审核结果。")
    if result != "approved" and not reason.strip():
        raise ValidationError("要求补充或拒绝必须填写原因。")
    if result == "approved":
        require_complete(app)
    review = MaintenanceReview.objects.create(application=app, reviewer=actor, version=app.version, result=result, reason=reason.strip())
    review.materials.set(app.materials.filter(active=True))
    app.status = result
    app.save()
    User.objects.filter(pk=app.worker_id).update(approval_status="approved" if result == "approved" else "pending" if result == "needs_more" else "rejected", approval_reason=reason.strip())
    return review


@transaction.atomic
def deactivate_worker(worker, actor, reason):
    require_admin(actor)
    if worker.role != User.Role.MAINTENANCE or not reason.strip():
        raise ValidationError("正式停用维修账号必须填写原因。")
    User.objects.filter(pk=worker.pk).update(is_active=False)
    return MaintenanceDeactivation.objects.create(worker=worker, actor=actor, reason=reason.strip())


def dispose_materials(worker, actor, reason, policy_reference):
    require_admin(actor)
    if not reason.strip() or not policy_reference.strip() or len(policy_reference.strip()) > 200:
        raise ValidationError("必须记录校方处置依据和处置原因。")
    with transaction.atomic():
        User.objects.filter(pk=worker.pk).update(is_active=F("is_active"))
        worker.refresh_from_db()
        if worker.role != User.Role.MAINTENANCE:
            raise ValidationError("仅支持维修账号材料处置。")
        event = worker.deactivation_events.order_by("-created_at").first()
        if worker.is_active or not event or timezone.now() < event.created_at + timedelta(days=180):
            raise ValidationError("材料仍在存续或180天最低保留期内，禁止清理。")
        audit = MaterialDisposal.objects.create(worker=worker, actor=actor, reason=reason.strip(), policy_reference=policy_reference.strip())
        audit.materials.set(MaintenanceMaterial.objects.filter(application__worker=worker, disposed_at__isnull=True))
    # Persist the audit intent before irreversible filesystem operations. If a
    # process or storage operation fails, the attempt remains traceable.
    try:
        for material in audit.materials.all():
            with transaction.atomic():
                User.objects.filter(pk=worker.pk).update(is_active=F("is_active"))
                worker.refresh_from_db()
                event = worker.deactivation_events.order_by("-created_at").first()
                if worker.is_active or not event or timezone.now() < event.created_at + timedelta(days=180):
                    raise ValidationError("账号已恢复启用，停止处置。")
                private_storage().delete(material.storage_name)
                material.disposed_at = timezone.now()
                material.save(update_fields=["disposed_at"])
    except Exception:
        audit.status = "failed"
        audit.failure_note = "处置中断，请结合文件状态与存储日志核验。"
        audit.save(update_fields=["status", "failure_note"])
        raise
    audit.status = "completed"
    audit.save(update_fields=["status"])
    return audit
