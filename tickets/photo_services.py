from contextlib import contextmanager
import hashlib
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import User
from core.private_files import outside_uploads, private_directory
from .models import Ticket, TicketHistory, TicketPhoto, TicketPhotoAccess, TicketPhotoDisposal
from .permissions import can_confirm_ticket, can_manage_ticket, can_process_ticket, can_view_ticket
from .photo_forms import MAX_PHOTOS, validate_photos
from .services import _run_ticket_operation, lock_ticket_for_workflow

TERMINAL = {Ticket.Status.COMPLETED, Ticket.Status.CANCELLED, Ticket.Status.REJECTED}


def photo_storage():
    root = outside_uploads(settings.PRIVATE_DOCUMENT_ROOT, [settings.MEDIA_ROOT])
    return FileSystemStorage(location=private_directory(root / "ticket_photos"))


def can_upload_photos(actor, ticket):
    return ticket.status not in TERMINAL and (
        can_confirm_ticket(actor, ticket) or can_process_ticket(actor, ticket)
    )


@contextmanager
def photo_write_batch():
    saved = []
    try:
        yield saved
    except Exception:
        for name in saved:
            photo_storage().delete(name)
        raise


def persist_photos(ticket, actor, photos, saved):
    if ticket.photos.count() + len(photos) > MAX_PHOTOS:
        raise ValidationError({"photos": "每张工单总量最多5张照片。"})
    for data, content_type, extension, original_name in photos:
        # Track the exclusive file immediately after creation, before writing.
        # A short write or close failure must also be removed on rollback.
        storage = photo_storage()
        for attempt in range(5):
            name = uuid.uuid4().hex + extension
            try:
                stream = storage.open(name, "xb")
            except FileExistsError:
                if attempt == 4:
                    raise
                continue
            saved.append(name)
            with stream:
                stream.write(data)
            break
        photo = TicketPhoto.objects.create(
            ticket=ticket, uploader=actor, storage_name=name, original_name=original_name,
            content_type=content_type, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
        )
        TicketHistory.objects.create(ticket=ticket, actor=actor, event_type="note_added",
                                     from_status=ticket.status, to_status=ticket.status,
                                     description="追加现场照片。", metadata={"photo_id": photo.pk})


def upload_photos(*, ticket_id, actor, uploads):
    photos = validate_photos(uploads)
    if not photos:
        raise ValidationError({"photos": "请选择至少一张照片。"})
    def operation():
        with photo_write_batch() as saved, transaction.atomic(durable=True):
            ticket = lock_ticket_for_workflow(ticket_id)
            current = User.objects.select_for_update().filter(pk=actor.pk).first()
            if not can_upload_photos(current, ticket):
                raise PermissionDenied("当前账号或工单状态不允许上传照片。")
            persist_photos(ticket, current, photos, saved)
    return _run_ticket_operation(operation)


def read_photo(*, photo_id, actor):
    def operation():
        photo = TicketPhoto.objects.get(pk=photo_id)
        with transaction.atomic():
            ticket = lock_ticket_for_workflow(photo.ticket_id)
            current = User.objects.select_for_update().filter(pk=actor.pk).first()
            if not can_view_ticket(current, ticket):
                raise PermissionDenied
            photo.refresh_from_db()
            if photo.disposed_at:
                raise ValidationError("照片已按保留策略处置。")
            with photo_storage().open(photo.storage_name, "rb") as stream:
                data = stream.read()
            TicketPhotoAccess.objects.create(photo=photo, actor=current)
            return photo, data
    return _run_ticket_operation(operation)


def retention_deadline(ticket):
    if ticket.status not in TERMINAL:
        return None
    closed = ticket.history.filter(to_status=ticket.status, event_type__in=("status_changed", "cancellation_changed")).exclude(
        from_status=F("to_status")
    ).order_by("-created_at", "-pk").first()
    if closed is None:
        return None
    try:
        return closed.created_at.replace(year=closed.created_at.year + 1)
    except ValueError:
        return closed.created_at.replace(year=closed.created_at.year + 1, day=28)


def require_disposal(ticket, actor):
    if not can_manage_ticket(actor):
        raise PermissionDenied
    deadline = retention_deadline(ticket)
    if deadline is None or timezone.now() < deadline:
        raise ValidationError("工单尚未关闭、缺少可信关闭历史或关闭未满12个月，禁止清理。")


def dispose_photos(*, ticket_id, actor, reason, policy_reference):
    if not reason.strip() or not policy_reference.strip() or len(policy_reference) > 200:
        raise ValidationError("必须提供清理原因和不超过200字的政策依据。")
    with transaction.atomic(durable=True):
        ticket = lock_ticket_for_workflow(ticket_id)
        require_disposal(ticket, actor)
        if TicketPhotoDisposal.objects.filter(photo__ticket=ticket, status="started").exists():
            raise ValidationError("存在未完成的清理，请先人工核查。")
        audits = [TicketPhotoDisposal.objects.create(photo=photo, actor=actor, reason=reason.strip(),
                  policy_reference=policy_reference.strip()) for photo in ticket.photos.filter(disposed_at=None)]
    for audit in audits:
        try:
            with transaction.atomic():
                ticket = lock_ticket_for_workflow(ticket_id)
                require_disposal(ticket, actor)
                photo_storage().delete(audit.photo.storage_name)
                audit.photo.disposed_at = timezone.now()
                audit.photo.save(update_fields=["disposed_at"])
                audit.status = "completed"
                audit.completed_at = timezone.now()
                audit.save(update_fields=["status", "completed_at"])
        except Exception:
            audit.status = "failed"
            audit.save(update_fields=["status"])
            # Remaining intent rows deliberately remain started for operator review.
            raise
    return len(audits)
