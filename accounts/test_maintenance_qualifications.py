import io
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from pypdf import PdfWriter

from .material_forms import MAX_FILE_SIZE, MaterialForm, validate_material
from .material_services import (
    deactivate_worker, dispose_materials, private_storage, retire_material,
    review_application, submit_application, upload_material,
)
from .models import (
    LoginProtection, MaintenanceApplication as Application, MaintenanceDeactivation,
    MaintenanceMaterial as Material, MaintenanceReview, MaterialDisposal, User,
)

PASSWORD = "Campus!Repair-2026"
FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def image_upload(name="proof.png", image_format="PNG", tail=b""):
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, format=image_format)
    return SimpleUploadedFile(name, output.getvalue() + tail, content_type="application/octet-stream")


def pdf_upload(encrypted=False):
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt("secret")
    writer.write(output)
    return SimpleUploadedFile("proof.pdf", output.getvalue(), content_type="application/pdf")


class MaterialValidationTests(SimpleTestCase):
    def test_actual_valid_pdf_png_and_both_jpeg_extensions_are_accepted(self):
        for upload in (pdf_upload(), image_upload(), image_upload("x.jpg", "JPEG"), image_upload("x.JPEG", "JPEG")):
            with self.subTest(name=upload.name):
                data, content_type, extension = validate_material(upload)
                self.assertTrue(data)
                self.assertIn(content_type, ("application/pdf", "image/png", "image/jpeg"))
                self.assertIn(extension, (".pdf", ".jpg", ".png"))

    def test_spoofed_extensions_corrupt_empty_oversize_encrypted_files_are_rejected(self):
        cases = [image_upload("x.pdf"), image_upload("x.jpg"), image_upload("x.svg"),
                 SimpleUploadedFile("x.png", b"not an image", content_type="image/png"),
                 SimpleUploadedFile("x.pdf", b"%PDF-1.7\ninvalid\n%%EOF"),
                 SimpleUploadedFile("empty.png", b""),
                 SimpleUploadedFile("huge.png", b"x" * (MAX_FILE_SIZE + 1)), pdf_upload(encrypted=True)]
        for upload in cases:
            with self.subTest(name=upload.name, size=upload.size):
                with self.assertRaises(ValidationError):
                    validate_material(upload)

    def test_image_trailing_payload_is_not_retained(self):
        data, _, _ = validate_material(image_upload(tail=b"<script>bad()</script>"))
        self.assertNotIn(b"<script>", data)

    def test_qualification_requires_name_direction_and_attachment_but_optional_fields_can_be_empty(self):
        for missing in ("name", "direction", "attachment"):
            data = {"category": "qualification", "name": "维修资质", "direction": "电气"}
            files = {"attachment": image_upload()}
            (files if missing == "attachment" else data).pop(missing)
            form = MaterialForm(data, files)
            self.assertFalse(form.is_valid())
            self.assertIn(missing, form.errors)
        form = MaterialForm({"category": "qualification", "name": "维修资质", "direction": "电气", "permanent": "on"}, {"attachment": pdf_upload()})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["certificate_category"], "其他")
        self.assertIsNone(form.cleaned_data["expires_on"])

    def test_extensible_category_and_date_rules(self):
        data = {"category": "qualification", "name": "资质", "direction": "水暖", "certificate_category": "校方补充类别", "issued_on": "2026-01-02", "expires_on": "2025-01-01"}
        form = MaterialForm(data, {"attachment": image_upload()})
        self.assertFalse(form.is_valid())
        self.assertIn("expires_on", form.errors)
        form = MaterialForm({**data, "permanent": "on"}, {"attachment": image_upload()})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["expires_on"])


class PrivateMaterialSetup:
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.options = override_settings(PRIVATE_DOCUMENT_ROOT=Path(self.directory.name) / "private", MEDIA_ROOT=Path(self.directory.name) / "public", PASSWORD_HASHERS=FAST_HASHERS)
        self.options.enable()
        self.addCleanup(self.options.disable)
        self.worker = User.objects.create_user(username="maint_test", password=PASSWORD, role="maintenance", real_name="维修员", approval_status="pending")
        self.app = Application.objects.create(worker=self.worker)
        self.admin = User.objects.create_user(username="reviewer", role="admin", approval_status="approved")
        self.other = User.objects.create_user(username="other", role="maintenance", approval_status="pending")
        self.other_app = Application.objects.create(worker=self.other)
        self.student = User.objects.create_user(username="student", role="student", approval_status="approved")

    def material(self, category="employment", **extra):
        form = MaterialForm({"category": category, "name": "电工证", "direction": "电气维修", **extra}, {"attachment": image_upload()})
        self.assertTrue(form.is_valid(), form.errors)
        return upload_material(self.app.pk, self.worker, form)

    def complete(self):
        return self.material(), self.material("qualification", permanent="on")

    def pending(self):
        materials = self.complete()
        submit_application(self.app.pk, self.worker)
        return materials

    def login_worker(self):
        return self.client.post(reverse("accounts:login"), {"username": self.worker.username, "password": PASSWORD, "next": "/business/"})


@override_settings(ROOT_URLCONF="accounts.tests")
class MaintenanceWorkflowTests(PrivateMaterialSetup, TestCase):
    def test_registration_creates_application_and_allows_restricted_draft_login(self):
        response = self.client.post(reverse("accounts:register"), {"role": "maintenance", "real_name": "新维修员", "phone": "13800138000", "password1": PASSWORD, "password2": PASSWORD}, follow=True)
        user = User.objects.get(phone="13800138000")
        self.assertContains(response, user.username)
        self.assertEqual(user.qualification_application.status, "draft")
        self.assertRedirects(self.client.post(reverse("accounts:login"), {"username": user.username, "password": PASSWORD}), reverse("accounts:qualifications"))

    def test_full_supplement_and_approval_flow_preserves_material_snapshots(self):
        self.assertRedirects(self.login_worker(), reverse("accounts:qualifications"))
        self.assertIn(SESSION_KEY, self.client.session)
        employment, old = self.pending()
        self.client.force_login(self.admin)
        url = reverse("accounts:maintenance_review", args=[self.app.pk])
        for result in ("needs_more", "rejected"):
            response = self.client.post(url, {"result": result, "reason": " "})
            self.assertIn("reason", response.context["form"].errors)
        self.assertFalse(MaintenanceReview.objects.exists())
        self.assertRedirects(self.client.post(url, {"result": "needs_more", "reason": "请提供清晰证书"}), url)
        first = MaintenanceReview.objects.get()
        self.assertEqual(set(first.materials.values_list("pk", flat=True)), {employment.pk, old.pk})
        self.assertEqual((first.reviewer, first.result, first.version), (self.admin, "needs_more", 1))
        self.assertTrue(first.created_at)
        self.client.logout()
        self.assertRedirects(self.login_worker(), reverse("accounts:qualifications"))
        self.assertEqual(self.client.get("/business/").status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)
        self.assertContains(self.client.get(reverse("accounts:qualifications")), "请提供清晰证书")
        self.assertRedirects(self.client.post(reverse("accounts:material_retire", args=[old.pk])), reverse("accounts:qualifications"))
        replacement = self.material("qualification")
        response = self.client.post(reverse("accounts:qualification_submit"), {"status": "approved", "approval_status": "approved"})
        self.assertRedirects(response, reverse("accounts:qualifications"))
        self.app.refresh_from_db(); self.worker.refresh_from_db()
        self.assertEqual((self.app.status, self.worker.approval_status, self.app.version), ("pending", "pending", 2))
        self.assertTrue(private_storage().exists(old.storage_name))
        self.assertEqual(self.client.get("/business/").status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(url, {"result": "approved", "reason": "人工核验通过"})
        records = list(MaintenanceReview.objects.order_by("pk"))
        self.assertEqual([r.result for r in records], ["needs_more", "approved"])
        self.assertEqual(set(records[1].materials.values_list("pk", flat=True)), {employment.pk, replacement.pk})
        self.assertTrue(records[0].materials.filter(pk=old.pk).exists())
        self.assertContains(self.client.get(url), "电气维修")
        self.assertContains(self.client.get(url), "人工核验通过")
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get("/business/").status_code, 200)
        self.assertContains(self.client.get(reverse("core:home")), "我的任务")
        Application.objects.filter(pk=self.app.pk).update(status="pending")
        self.assertEqual(self.client.get("/business/").status_code, 403)

    def test_each_category_one_to_three_and_no_fourth_or_missing_category_submission(self):
        with self.assertRaises(ValidationError):
            submit_application(self.app.pk, self.worker)
        for _ in range(3):
            self.material()
        with self.assertRaises(ValidationError):
            self.material()
        self.assertEqual(Material.objects.count(), 3)
        with self.assertRaises(ValidationError):
            submit_application(self.app.pk, self.worker)
        for _ in range(3):
            self.material("qualification")
        submit_application(self.app.pk, self.worker)
        self.assertEqual(Application.objects.get(pk=self.app.pk).status, "pending")
        with self.assertRaises(ValidationError):
            self.material()
        with self.assertRaises(ValidationError):
            retire_material(Material.objects.first(), self.worker)

    def test_upload_errors_and_forged_ownership_or_approval_never_change_target(self):
        self.client.force_login(self.worker)
        url = reverse("accounts:qualification_upload")
        for key, target in (("application_id", self.other_app.pk), ("application", self.other_app.pk), ("worker_id", self.other.pk), ("user_id", self.other.pk)):
            response = self.client.post(url, {"category": "employment", key: target, "attachment": image_upload()})
            self.assertEqual(response.status_code, 403)
        response = self.client.post(url, {"category": "employment", "attachment": SimpleUploadedFile("fake.png", b"bad")})
        self.assertIn("attachment", response.context["form"].errors)
        self.assertFalse(Material.objects.exists())
        self.assertFalse(Path(self.directory.name, "public").exists())
        response = self.client.post(url, {"category": "employment", "status": "approved", "approval_status": "approved", "attachment": image_upload()})
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db(); self.worker.refresh_from_db()
        self.assertEqual((self.app.status, self.worker.approval_status), ("draft", "pending"))
        form = MaterialForm({"category": "employment"}, {"attachment": image_upload()})
        self.assertTrue(form.is_valid())
        with self.assertRaises(PermissionDenied):
            upload_material(self.other_app.pk, self.worker, form)
        self.assertFalse(self.other_app.materials.exists())

    def test_failed_database_write_removes_private_file(self):
        with patch("accounts.material_services.MaintenanceMaterial.save", side_effect=RuntimeError("database failure")):
            with self.assertRaises(RuntimeError):
                self.material()
        self.assertFalse(Material.objects.exists())
        self.assertEqual(list(Path(self.directory.name, "private").glob("*")), [])

    def test_storage_failure_shows_safe_retry_message_without_saved_material(self):
        self.client.force_login(self.worker)
        with patch("django.core.files.storage.FileSystemStorage.save", side_effect=OSError("private storage unavailable")):
            response = self.client.post(reverse("accounts:qualification_upload"), {"category": "employment", "attachment": image_upload()})
        self.assertContains(response, "材料保存失败，请稍后重试")
        self.assertNotContains(response, "private storage unavailable")
        self.assertFalse(Material.objects.exists())

    def test_http_upload_submit_review_and_restricted_sessions(self):
        self.assertRedirects(self.login_worker(), reverse("accounts:qualifications"))
        upload_url = reverse("accounts:qualification_upload")
        for category in ("employment", "qualification"):
            response = self.client.post(upload_url, {"category": category, "name": "校内电气资质", "direction": "供电维护", "attachment": pdf_upload()})
            self.assertRedirects(response, reverse("accounts:qualifications"))
        response = self.client.post(reverse("accounts:qualification_submit"), {})
        self.assertRedirects(response, reverse("accounts:qualifications"))
        self.assertEqual(self.client.get("/business/").status_code, 403)
        self.client.force_login(self.admin)
        review_url = reverse("accounts:maintenance_review", args=[self.app.pk])
        response = self.client.post(review_url, {"result": "approved", "reason": "线下核验完成"})
        self.assertRedirects(response, review_url)
        self.assertContains(self.client.get(review_url), "供电维护")
        self.client.logout()
        self.assertRedirects(self.login_worker(), "/business/")
        self.assertEqual(self.client.get("/business/").status_code, 200)

    def test_rejection_and_formal_deactivation_are_recorded_and_prevent_business(self):
        self.pending()
        self.client.force_login(self.admin)
        review_url = reverse("accounts:maintenance_review", args=[self.app.pk])
        self.client.post(review_url, {"result": "rejected", "reason": "证明与申请人不符"})
        self.app.refresh_from_db(); self.worker.refresh_from_db()
        self.assertEqual((self.app.status, self.worker.approval_status), ("rejected", "rejected"))
        self.assertEqual(MaintenanceReview.objects.get().reason, "证明与申请人不符")
        self.assertFalse(MaintenanceDeactivation.objects.exists())
        url = reverse("accounts:maintenance_deactivate", args=[self.worker.pk])
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(url, {"reason": "结束服务"}).status_code, 403)
        self.client.post(url, {"reason": "结束服务"})
        event = MaintenanceDeactivation.objects.get(worker=self.worker)
        self.assertEqual((event.actor, event.reason), (self.admin, "结束服务"))
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get("/business/").status_code, 302)
        self.client.logout()
        self.assertEqual(self.login_worker().status_code, 200)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_downloads_require_owner_or_active_approved_admin_and_never_expose_storage_path(self):
        material = self.material()
        url = reverse("accounts:material_download", args=[material.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        for user, expected in ((self.worker, 200), (self.admin, 200), (self.other, 403), (self.student, 403)):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, expected)
            if expected == 200:
                self.assertIn("attachment;", response["Content-Disposition"])
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")
                self.assertEqual(response["Cache-Control"], "private, no-store")
                self.assertTrue(b"".join(response.streaming_content))
                response.close()
        self.client.force_login(self.worker)
        self.assertNotContains(self.client.get(reverse("accounts:qualifications")), material.storage_name)
        self.client.logout()
        with override_settings(ROOT_URLCONF="config.urls", DEBUG=True):
            for path in (f"/media/{material.storage_name}", f"/private_documents/{material.storage_name}"):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_review_and_other_user_changes_are_admin_only_and_csrf_protected(self):
        material = self.material()
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("accounts:material_retire", args=[material.pk])).status_code, 404)
        self.client.force_login(self.student)
        review_url = reverse("accounts:maintenance_review", args=[self.app.pk])
        for url in (reverse("accounts:maintenance_reviews"), review_url):
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {"result": "approved"}).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.worker)
        for name in ("qualification_upload", "qualification_submit"):
            self.assertEqual(csrf_client.post(reverse(f"accounts:{name}"), {}).status_code, 403)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(review_url, {"result": "approved"}).status_code, 403)

    def test_pending_rejected_inactive_and_inconsistent_approval_cannot_handle_tasks(self):
        self.pending()
        for status, user_status in (("pending", "pending"), ("rejected", "rejected"), ("draft", "approved"), ("approved", "pending")):
            Application.objects.filter(pk=self.app.pk).update(status=status)
            User.objects.filter(pk=self.worker.pk).update(approval_status=user_status)
            self.worker.refresh_from_db()
            self.client.force_login(self.worker)
            self.assertEqual(self.client.get("/business/").status_code, 403)
            self.client.logout()
            if user_status != "approved":
                LoginProtection.objects.all().delete()
                self.assertEqual(self.login_worker().status_code, 200)
                self.assertNotIn(SESSION_KEY, self.client.session)
        Application.objects.filter(pk=self.app.pk).update(status="approved")
        User.objects.filter(pk=self.worker.pk).update(approval_status="approved", is_active=False)
        self.worker.refresh_from_db()
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get("/business/").status_code, 302)
        LoginProtection.objects.all().delete()
        self.assertEqual(self.login_worker().status_code, 200)

    def test_missing_physical_attachment_cannot_be_submitted_or_approved(self):
        employment, _ = self.complete()
        private_storage().delete(employment.storage_name)
        with self.assertRaises(ValidationError):
            submit_application(self.app.pk, self.worker)
        Application.objects.filter(pk=self.app.pk).update(status="pending")
        with self.assertRaises(ValidationError):
            review_application(self.app.pk, self.admin, "approved", "")
        self.assertFalse(MaintenanceReview.objects.exists())

    def test_missing_file_still_allows_admin_to_request_replacement_and_finish_review(self):
        employment, _ = self.pending()
        private_storage().delete(employment.storage_name)
        review_application(self.app.pk, self.admin, "needs_more", "附件缺失，请重新上传")
        retire_material(employment, self.worker)
        self.material()
        submit_application(self.app.pk, self.worker)
        review_application(self.app.pk, self.admin, "approved", "重新核验完成")
        self.assertEqual(list(self.app.reviews.order_by("id").values_list("result", flat=True)), ["needs_more", "approved"])


class MaterialRetentionTests(PrivateMaterialSetup, TestCase):
    def test_active_rejected_and_temporarily_locked_accounts_are_never_cleanup_eligible(self):
        material = self.material()
        User.objects.filter(pk=self.worker.pk).update(approval_status="rejected")
        LoginProtection.objects.create(username=self.worker.username, failures=5, locked_until=timezone.now() + timedelta(minutes=15))
        self.worker.refresh_from_db()
        for active in (True, False):
            User.objects.filter(pk=self.worker.pk).update(is_active=active)
            with self.assertRaises(ValidationError):
                dispose_materials(self.worker, self.admin, "依规处置", "校方制度")
        self.assertTrue(private_storage().exists(material.storage_name))
        self.assertFalse(MaintenanceDeactivation.objects.exists())
        self.assertFalse(MaterialDisposal.objects.exists())

    def test_formal_deactivation_is_audited_and_180_day_boundary_is_enforced(self):
        material = self.material()
        with self.assertRaises(ValidationError):
            deactivate_worker(self.worker, self.admin, " ")
        event = deactivate_worker(self.worker, self.admin, "合同终止")
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.is_active)
        self.assertEqual((event.actor, event.reason), (self.admin, "合同终止"))
        boundary = event.created_at + timedelta(days=180)
        with patch("accounts.material_services.timezone.now", return_value=boundary - timedelta(microseconds=1)):
            with self.assertRaises(ValidationError):
                dispose_materials(self.worker, self.admin, "依规处置", "制度-1")
        with patch("accounts.material_services.timezone.now", return_value=boundary):
            with self.assertRaises(PermissionDenied):
                dispose_materials(self.worker, self.student, "依规处置", "制度-1")
            with self.assertRaises(ValidationError):
                dispose_materials(self.worker, self.admin, "依规处置", " ")
            audit = dispose_materials(self.worker, self.admin, "依规处置", "制度-1")
        self.assertEqual((audit.status, audit.actor, audit.policy_reference), ("completed", self.admin, "制度-1"))
        self.assertEqual(list(audit.materials.all()), [material])
        self.assertFalse(private_storage().exists(material.storage_name))
        material.refresh_from_db()
        self.assertTrue(material.disposed_at)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("accounts:material_download", args=[material.pk])).status_code, 404)
        response = self.client.get(reverse("accounts:maintenance_review", args=[self.app.pk]))
        self.assertContains(response, "合同终止")
        self.assertContains(response, "制度-1")

    def test_storage_failure_leaves_audit_and_manual_command_requires_explicit_confirmation(self):
        material = self.material()
        event = deactivate_worker(self.worker, self.admin, "离职")
        MaintenanceDeactivation.objects.filter(pk=event.pk).update(created_at=timezone.now() - timedelta(days=181))
        with patch("django.core.files.storage.FileSystemStorage.delete", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                dispose_materials(self.worker, self.admin, "依规处置", "制度")
        audit = MaterialDisposal.objects.get()
        self.assertEqual(audit.status, "failed")
        self.assertTrue(audit.materials.filter(pk=material.pk).exists())
        self.assertTrue(private_storage().exists(material.storage_name))
        options = {"worker_id": self.worker.pk, "actor_id": self.admin.pk, "reason": "依规处置", "policy_reference": "制度", "stdout": io.StringIO()}
        with self.assertRaises(CommandError):
            call_command("dispose_maintenance_materials", **options)
        call_command("dispose_maintenance_materials", confirm=True, **options)
        self.assertEqual(MaterialDisposal.objects.filter(status="completed").count(), 1)


@override_settings(ROOT_URLCONF="accounts.tests")
class ConcurrentUploadTests(PrivateMaterialSetup, TransactionTestCase):
    def test_concurrent_fourth_upload_cannot_exceed_three(self):
        self.material(); self.material()
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(self.worker)

        def upload(client):
            close_old_connections()
            try:
                return client.post(reverse("accounts:qualification_upload"), {"category": "employment", "attachment": image_upload()}).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(upload, clients)), [200, 302])
        self.assertEqual(self.app.materials.filter(active=True).count(), 3)
        self.assertEqual(len(list(Path(self.directory.name, "private").glob("*"))), 3)


class QualificationMigrationTests(TransactionTestCase):
    def test_existing_accounts_keep_identity_and_need_material_review_without_silent_approval(self):
        before = ("accounts", "0003_loginprotection_user_department_and_more")
        after = ("accounts", "0005_materialdisposal_failure_note_and_more")
        executor = MigrationExecutor(connection)
        try:
            executor.migrate([before])
            HistoricalUser = executor.loader.project_state([before]).apps.get_model("accounts", "User")
            for index, status in enumerate(("pending", "approved", "rejected")):
                HistoricalUser.objects.create(username=f"legacy_{status}", role="maintenance", approval_status=status, real_name="原姓名", phone=f"1380013800{index}", organization_number="UNIT-OLD")
            HistoricalUser.objects.create(username="legacy_student", role="student")
            executor = MigrationExecutor(connection)
            executor.migrate([after])
            self.assertEqual(Application.objects.count(), 3)
            for status in ("pending", "approved", "rejected"):
                user = User.objects.get(username=f"legacy_{status}")
                self.assertEqual((user.approval_status, user.real_name, user.organization_number), (status, "原姓名", "UNIT-OLD"))
                self.assertEqual(user.qualification_application.status, "rejected" if status == "rejected" else "draft")
                self.assertFalse(user.can_handle_maintenance)
        finally:
            MigrationExecutor(connection).migrate([after])
