from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as dt_timezone
import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from PIL import Image
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .cancellations import submit_cancellation
from .models import Ticket, TicketPhoto, TicketPhotoDisposal
from .photo_forms import MAX_PHOTO_SIZE, validate_photos
from .photo_services import dispose_photos, photo_storage, read_photo, retention_deadline, upload_photos
from .tests import TicketFixtureMixin


def image_upload(name="现场.png", fmt="PNG"):
    output = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(output, format=fmt)
    return SimpleUploadedFile(name, output.getvalue(), content_type="text/plain")


class PhotoStorageMixin:
    def setUp(self):
        super().setUp()
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name) / "private"
        self.override = override_settings(PRIVATE_DOCUMENT_ROOT=self.private,
                                         MEDIA_ROOT=Path(self.temp.name) / "media")
        self.override.enable()
        self.addCleanup(self.override.disable)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PhotoTests(PhotoStorageMixin, TicketFixtureMixin, TestCase):
    def create(self, count=1):
        return self.create_valid_ticket(photos=[image_upload() for _ in range(count)])

    def test_actual_content_extensions_size_and_sanitization(self):
        for name, fmt in (("x.JPG", "JPEG"), ("x.jpeg", "JPEG"), ("x.png", "PNG")):
            self.assertEqual(len(validate_photos([image_upload(name, fmt)])), 1)
        for upload in (image_upload("x.jpg"), image_upload("x.svg"),
                       SimpleUploadedFile("bad.png", b"broken"),
                       SimpleUploadedFile("big.png", b"x" * (MAX_PHOTO_SIZE + 1))):
            with self.subTest(name=upload.name), self.assertRaises(ValidationError):
                validate_photos([upload])
        data = image_upload().read()
        cleaned = validate_photos([SimpleUploadedFile("limit.png", data + b"x" * (MAX_PHOTO_SIZE - len(data)))])[0][0]
        self.assertLess(len(cleaned), MAX_PHOTO_SIZE)
        self.assertFalse(cleaned.endswith(b"xxx"))

    def test_create_and_append_limit_are_atomic(self):
        with self.assertRaises(ValidationError):
            self.create(6)
        self.assertEqual(Ticket.objects.count(), 0)
        ticket = self.create(4)
        upload_photos(ticket_id=ticket.pk, actor=self.student, uploads=[image_upload()])
        with self.assertRaises(ValidationError):
            upload_photos(ticket_id=ticket.pk, actor=self.student, uploads=[image_upload()])
        self.assertEqual(ticket.photos.count(), 5)
        self.assertEqual(len(list((self.private / "ticket_photos").iterdir())), 5)

    def test_database_failure_removes_files_and_rolls_back_creation(self):
        with patch.object(TicketPhoto.objects, "create", side_effect=DatabaseError("failure")):
            with self.assertRaises(ValidationError):
                self.create()
        self.assertFalse(Ticket.objects.exists())
        self.assertEqual(list((self.private / "ticket_photos").iterdir()), [])
        ticket = self.create_valid_ticket()
        with patch.object(TicketPhoto.objects, "create", side_effect=DatabaseError("failure")):
            with self.assertRaises(ValidationError):
                upload_photos(ticket_id=ticket.pk, actor=self.student, uploads=[image_upload()])
        self.assertFalse(ticket.photos.exists())
        self.assertEqual(list((self.private / "ticket_photos").iterdir()), [])

    def test_private_endpoint_uses_live_assignee_and_disables_cache(self):
        ticket = self.create()
        Ticket.objects.filter(pk=ticket.pk).update(current_assignee=self.maintenance, status="pending_work")
        photo = ticket.photos.get()
        url = reverse("tickets:photo_read", args=[photo.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        for user in (self.student, self.maintenance, self.admin):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(photo.accesses.count(), 3)
        Ticket.objects.filter(pk=ticket.pk).update(current_assignee=self.other_maintenance)
        self.client.force_login(self.maintenance)
        self.assertEqual(self.client.get(url).status_code, 404)
        with self.assertRaises(PermissionDenied):
            read_photo(photo_id=photo.pk, actor=self.maintenance)
        self.client.force_login(self.other_maintenance)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(url).status_code, 404)
        for public_url in (f"/media/{photo.storage_name}", f"/private_documents/ticket_photos/{photo.storage_name}"):
            self.assertEqual(self.client.get(public_url).status_code, 404)

    def test_partial_file_write_failure_removes_file_and_returns_form_feedback(self):
        storage = photo_storage()
        original_open = storage.open

        class BrokenWrite:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def write(self, data):
                self.stream.write(data[:5])
                raise OSError("disk write failed")

            def __exit__(self, *args):
                self.stream.close()

        self.client.force_login(self.student)
        data = {"building": self.teaching_building.pk, "fault_category": self.category.pk,
                "title": "磁盘故障", "description": "灯具损坏", "location_kind": "room", "room_number": "301"}
        with patch("tickets.photo_services.photo_storage", return_value=storage), patch.object(
            storage, "open", side_effect=lambda name, mode: BrokenWrite(original_open(name, mode))
        ):
            response = self.client.post(reverse("tickets:create"), {**data, "photos": image_upload()})
            self.assertContains(response, "工单暂时无法提交，请稍后重试")
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(TicketPhoto.objects.exists())
        self.assertEqual(list((self.private / "ticket_photos").iterdir()), [])

    def test_outer_rollback_cannot_leave_files_or_uncommitted_disposal_audits(self):
        # Files cannot participate in a caller's later database rollback.
        # The service must own the outermost business transaction.
        with transaction.atomic(), self.assertRaises(RuntimeError):
            self.create()
        self.assertFalse(TicketPhoto.objects.exists())
        ticket = self.create()
        before = set(photo_storage().listdir("")[1])
        with transaction.atomic(), self.assertRaises(ValidationError):
            upload_photos(ticket_id=ticket.pk, actor=self.student, uploads=[image_upload()])
        with transaction.atomic(), self.assertRaises(RuntimeError):
            dispose_photos(ticket_id=ticket.pk, actor=self.admin, reason="清理", policy_reference="政策")
        self.assertEqual(set(photo_storage().listdir("")[1]), before)
        self.assertEqual(ticket.photos.count(), 1)
        self.assertFalse(TicketPhotoDisposal.objects.exists())

    def test_pending_allows_append_but_admin_and_terminal_do_not(self):
        ticket = self.create()
        Ticket.objects.filter(pk=ticket.pk).update(current_assignee=self.maintenance, status="pending_work")
        submit_cancellation(ticket=ticket, actor=self.student)
        upload_photos(ticket_id=ticket.pk, actor=self.maintenance, uploads=[image_upload()])
        for actor in (self.admin, self.teacher, self.other_maintenance):
            with self.assertRaises(PermissionDenied):
                upload_photos(ticket_id=ticket.pk, actor=actor, uploads=[image_upload()])
        Ticket.objects.filter(pk=ticket.pk).update(status="cancelled", cancellation_pending=False)
        with self.assertRaises(PermissionDenied):
            upload_photos(ticket_id=ticket.pk, actor=self.student, uploads=[image_upload()])
        self.assertEqual(ticket.photos.count(), 2)

    def test_real_create_and_detail_forms_and_field_errors(self):
        self.client.force_login(self.student)
        data = {"building": self.teaching_building.pk, "fault_category": self.category.pk,
                "title": "现场照片工单", "description": "灯具损坏", "location_kind": "room", "room_number": "301"}
        self.assertContains(self.client.get(reverse("tickets:create")), 'enctype="multipart/form-data"')
        response = self.client.post(reverse("tickets:create"), {**data, "photos": image_upload()}, follow=True)
        self.assertContains(response, "现场照片 1")
        ticket = Ticket.objects.get()
        response = self.client.post(reverse("tickets:photo_upload", args=[ticket.pk]),
                                    {"photos": SimpleUploadedFile("bad.png", b"bad")})
        self.assertContains(response, "照片内容损坏")
        self.assertEqual(ticket.photos.count(), 1)
        self.assertEqual(self.client.get(reverse("tickets:photo_upload", args=[ticket.pk])).status_code, 405)

    def test_retention_calendar_boundary_and_audited_disposal(self):
        ticket = self.create()
        closed = datetime(2024, 2, 29, 12, tzinfo=dt_timezone.utc)
        with patch("django.utils.timezone.now", return_value=closed):
            submit_cancellation(ticket=ticket, actor=self.student, reason="现场已自行解决")
        ticket.refresh_from_db()
        deadline = datetime(2025, 2, 28, 12, tzinfo=dt_timezone.utc)
        self.assertEqual(retention_deadline(ticket), deadline)
        kwargs = dict(ticket_id=ticket.pk, actor=self.admin, reason="按政策清理", policy_reference="校方资料政策")
        with patch("django.utils.timezone.now", return_value=deadline - timedelta(microseconds=1)):
            with self.assertRaises(ValidationError):
                dispose_photos(**kwargs)
        self.assertFalse(TicketPhotoDisposal.objects.exists())
        with patch("django.utils.timezone.now", return_value=deadline):
            self.assertEqual(dispose_photos(**kwargs), 1)
        photo = ticket.photos.get()
        self.assertTrue(photo.disposed_at)
        self.assertFalse(photo_storage().exists(photo.storage_name))
        self.assertEqual(photo.disposals.get().status, "completed")
        self.assertTrue(ticket.history.exists())

    def test_disposal_denies_open_unknown_close_and_preserves_failed_audit(self):
        ticket = self.create()
        kwargs = dict(ticket_id=ticket.pk, actor=self.admin, reason="清理", policy_reference="政策")
        for status in ("pending_acceptance", "cancelled"):
            Ticket.objects.filter(pk=ticket.pk).update(status=status)
            with self.assertRaises(ValidationError):
                dispose_photos(**kwargs)
        Ticket.objects.filter(pk=ticket.pk).update(status="pending_acceptance")
        with patch("django.utils.timezone.now", return_value=datetime(2020, 1, 1, tzinfo=dt_timezone.utc)):
            submit_cancellation(ticket=ticket, actor=self.student, reason="取消")
        with patch("tickets.photo_services.FileSystemStorage.delete", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                dispose_photos(**kwargs)
        self.assertEqual(TicketPhotoDisposal.objects.get().status, "failed")
        self.assertIsNone(ticket.photos.get().disposed_at)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ConcurrentPhotoTests(PhotoStorageMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        TicketFixtureMixin.setUpTestData.__func__(type(self))

    def test_concurrent_uploads_cannot_exceed_total_limit(self):
        ticket = TicketFixtureMixin.create_valid_ticket(self, photos=[image_upload() for _ in range(4)])
        # Initialize storage before starting independent database connections.
        barrier = Barrier(2)
        def send(_):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                upload_photos(ticket_id=ticket.pk, actor=self.student, uploads=[image_upload()])
                return True
            except ValidationError:
                return False
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(send, range(2))), [False, True])
        self.assertEqual(ticket.photos.count(), 5)

    def test_backup_restore_in_isolated_database_preserves_photo_permissions(self):
        import json
        import os
        import sqlite3
        import subprocess
        import sys
        from django.db import connection
        from core.backups import create_backup, restore_backup, verify_backup, sqlite_connection

        ticket = TicketFixtureMixin.create_valid_ticket(self, photos=[image_upload()])
        photo = ticket.photos.get()
        snapshot = Path(self.temp.name) / "live.sqlite3"
        with sqlite_connection(snapshot) as target:
            connection.connection.backup(target)
        with override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(snapshot)}},
                               BACKUP_ROOT=Path(self.temp.name) / "backups"):
            backup = create_backup()
            restored = restore_backup(backup, Path(self.temp.name) / "restored")
            verify_backup(restored)
            script = """
import os,json
os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
from django.conf import settings
from pathlib import Path
root=Path(os.environ['PHOTO_RESTORE_ROOT'])
settings.DATABASES['default']['NAME']=root/'db.sqlite3'
settings.PRIVATE_DOCUMENT_ROOT=root/'private_documents'
settings.ALLOWED_HOSTS=['testserver']
import django
django.setup()
from django.test import Client
from accounts.models import User
from tickets.models import TicketPhoto
photo=TicketPhoto.objects.get()
client=Client()
out={}
for role,username in [('student','student-one'),('teacher','teacher-one'),('admin','admin-one')]:
    client.force_login(User.objects.get(username=username))
    response=client.get('/tickets/photos/%s/' % photo.pk)
    out[role]=response.status_code
print(json.dumps(out))
"""
            result = subprocess.run([sys.executable, "-c", script],
                                    env={**os.environ, "PHOTO_RESTORE_ROOT": str(restored)},
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]),
                             {"student": 200, "teacher": 404, "admin": 200})
            photo_storage().delete(photo.storage_name)
            with self.assertRaisesMessage(ValueError, "工单照片缺失"):
                create_backup()
