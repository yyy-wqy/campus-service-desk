from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
import uuid

from django.contrib.auth import SESSION_KEY, authenticate
from django.core.cache import cache
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import AccountAuthenticationForm, RegistrationForm
from .models import LoginProtection, TeacherReview, User
from .management import ProfileForm

PASSWORD = "Campus!Repair-2026"


class AccountManagementTests(TestCase):
    def payload(self, **extra):
        return {"username": "teacher001", "real_name": "测试教师", "department": "计算机学院", "phone": "13800138000", "role": "teacher", "password1": PASSWORD, "password2": PASSWORD, **extra}

    def test_teacher_requires_details_without_requiring_attachment(self):
        for field in ("real_name", "username", "department", "phone"):
            response = self.client.post(reverse("accounts:register"), self.payload(**{field: ""}))
            self.assertIn(field, response.context["form"].errors)
            self.assertFalse(User.objects.exists())
        self.assertRedirects(self.client.post(reverse("accounts:register"), self.payload()), reverse("accounts:login"))
        teacher = User.objects.get(username="teacher001")
        self.assertEqual((teacher.department, teacher.approval_status), ("计算机学院", "pending"))

    def test_maintenance_generated_credential_ignores_submitted_username(self):
        response = self.client.post(reverse("accounts:register"), self.payload(role="maintenance", username="forged", organization_number="UNIT-001"), follow=True)
        user = User.objects.get(phone="13800138000")
        self.assertTrue(user.username.startswith("maint_"))
        self.assertNotEqual(user.username, "forged")
        self.assertContains(response, user.username)
        self.assertEqual(user.organization_number, "UNIT-001")

    def test_generated_account_collision_with_other_role_is_retried(self):
        first, second = uuid.uuid4(), uuid.uuid4()
        User.objects.create_user(username="maint_" + first.hex, role="teacher")
        with patch("accounts.forms.uuid.uuid4", side_effect=[first, second]):
            form = RegistrationForm(data=self.payload(role="maintenance"))
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.save().username, "maint_" + second.hex)

    def test_teacher_cannot_choose_reserved_maintenance_namespace(self):
        form = RegistrationForm(data=self.payload(username="MAINT_reserved"))
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_profile_only_updates_current_users_allowed_fields(self):
        user = User.objects.create_user(username="worker", role="maintenance", password=PASSWORD, phone="13800138000", approval_status="approved")
        other = User.objects.create_user(username="other", real_name="原姓名")
        self.client.force_login(user)
        response = self.client.post(reverse("accounts:profile"), {"real_name": "新姓名", "phone": "13800138001", "department": "维修部", "organization_number": "UNIT-NEW", "username": "changed", "role": "admin", "approval_status": "rejected", "id": other.pk, "email": "forged@example.com"})
        self.assertRedirects(response, reverse("accounts:profile"))
        user.refresh_from_db(); other.refresh_from_db()
        self.assertEqual((user.username, user.role, user.approval_status), ("worker", "maintenance", "approved"))
        self.assertEqual((user.phone, user.organization_number, user.email), ("13800138001", "UNIT-NEW", ""))
        self.assertEqual(other.real_name, "原姓名")
        self.assertEqual(self.client.get(f"/accounts/profile/{other.pk}/").status_code, 404)
        self.assertContains(self.client.get(reverse("accounts:profile")), "worker")

    def test_profile_cannot_claim_others_phone(self):
        user = User.objects.create_user(username="u", password=PASSWORD, approval_status="approved")
        User.objects.create_user(username="other", phone="13800138000")
        self.client.force_login(user)
        response = self.client.post(reverse("accounts:profile"), {"real_name": "姓名", "phone": "13800138000"})
        self.assertIn("phone", response.context["form"].errors)
        user.refresh_from_db(); self.assertEqual(user.phone, "")

    def test_profile_save_does_not_restore_stale_approval_status(self):
        user = User.objects.create_user(username="stale", approval_status="approved")
        form = ProfileForm(data={"real_name": "姓名", "phone": "13800138000"}, instance=user)
        self.assertTrue(form.is_valid(), form.errors)
        User.objects.filter(pk=user.pk).update(approval_status="rejected", approval_reason="重新核验")
        form.save()
        user.refresh_from_db()
        self.assertEqual((user.approval_status, user.approval_reason), ("rejected", "重新核验"))

    def test_teacher_reviews_require_admin_and_reason_and_preserve_history(self):
        teacher = User.objects.create_user(username="teacher", role="teacher", department="学院", password=PASSWORD)
        admin = User.objects.create_user(username="manager", role="admin", approval_status="approved")
        ordinary = User.objects.create_user(username="student", approval_status="approved")
        url = reverse("accounts:teacher_review", args=[teacher.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(ordinary)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"result": "approved"}).status_code, 403)
        self.client.force_login(admin)
        response = self.client.post(url, {"result": "rejected", "reason": "  "})
        self.assertIn("reason", response.context["form"].errors)
        self.assertFalse(TeacherReview.objects.exists())
        self.client.post(url, {"result": "rejected", "reason": "请线下核验工号"})
        self.client.post(url, {"result": "approved", "reason": "已核实"})
        records = list(TeacherReview.objects.order_by("id"))
        self.assertEqual([r.result for r in records], ["rejected", "approved"])
        self.assertTrue(all(r.reviewer_id == admin.pk and r.created_at for r in records))
        teacher.refresh_from_db(); self.assertEqual(teacher.approval_status, "approved")
        self.client.logout()
        self.assertRedirects(self.client.post(reverse("accounts:login"), {"username": teacher.username, "password": PASSWORD}), reverse("core:home"))
        User.objects.filter(pk=teacher.pk).update(approval_status="rejected")
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 403)

    def test_profile_and_reviews_enforce_csrf(self):
        admin = User.objects.create_user(username="admin", role="admin", approval_status="approved")
        teacher = User.objects.create_user(username="t", role="teacher")
        client = Client(enforce_csrf_checks=True); client.force_login(admin)
        for url in (reverse("accounts:profile"), reverse("accounts:teacher_review", args=[teacher.pk])):
            self.assertEqual(client.post(url, {}).status_code, 403)

    def test_deactivating_signed_in_account_denies_next_request_and_ends_session(self):
        user = User.objects.create_user(username="signed-in", password=PASSWORD, approval_status="approved")
        self.assertRedirects(self.client.post(reverse("accounts:login"), {"username": user.username, "password": PASSWORD}), reverse("core:home"))
        self.assertIn(SESSION_KEY, self.client.session)

        User.objects.filter(pk=user.pk).update(is_active=False)
        response = self.client.get(reverse("accounts:profile"))
        self.assertContains(response, "账号已停用", status_code=403)
        self.assertNotIn(SESSION_KEY, self.client.session)
        User.objects.filter(pk=user.pk).update(is_active=True)
        self.assertRedirects(self.client.get(reverse("accounts:profile")), f'{reverse("accounts:login")}?next={reverse("accounts:profile")}')


class LoginProtectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="protected", password=PASSWORD, approval_status="approved")

    def attempt(self, password="wrong", username="protected"):
        form = AccountAuthenticationForm(data={"username": username, "password": password})
        valid = form.is_valid()
        return valid, form

    def test_fifth_attempt_locks_and_exact_expiry_unlocks(self):
        start = timezone.now()
        with patch("accounts.login_protection.timezone.now", return_value=start):
            for i in range(5):
                valid, form = self.attempt()
                self.assertFalse(valid)
                self.assertEqual(form.non_field_errors().as_data()[0].code, "locked" if i == 4 else "incorrect_password")
            self.assertFalse(self.attempt(PASSWORD)[0])
            self.assertIsNone(authenticate(username=self.user.username, password=PASSWORD))
        with patch("accounts.login_protection.timezone.now", return_value=start + timedelta(minutes=15)):
            self.assertTrue(self.attempt(PASSWORD)[0])
        self.assertEqual(LoginProtection.objects.get(username="protected").failures, 0)

    def test_window_boundary_success_reset_and_account_independence(self):
        start = timezone.now()
        with patch("accounts.login_protection.timezone.now", return_value=start):
            for _ in range(4): self.attempt()
        with patch("accounts.login_protection.timezone.now", return_value=start + timedelta(minutes=15)):
            self.attempt()
            self.assertEqual(LoginProtection.objects.get(username="protected").failures, 1)
            self.assertTrue(self.attempt(PASSWORD)[0])
            self.attempt()
            self.assertEqual(LoginProtection.objects.get(username="protected").failures, 1)
            for _ in range(5): self.attempt(username="unknown")
            self.assertTrue(self.attempt(PASSWORD)[0])

    def test_http_locked_attempt_never_establishes_session(self):
        for _ in range(5): self.client.post(reverse("accounts:login"), {"username": "protected", "password": "wrong"})
        response = self.client.post(reverse("accounts:login"), {"username": "protected", "password": PASSWORD})
        self.assertContains(response, "账号已锁定")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_fixed_window_restarts_at_first_attempt_expiry(self):
        start = timezone.now()
        for seconds in (0, 300, 600, 890, 901):
            with patch("accounts.login_protection.timezone.now", return_value=start + timedelta(seconds=seconds)):
                self.assertNotEqual(self.attempt()[1].non_field_errors().as_data()[0].code, "locked")
        with patch("accounts.login_protection.timezone.now", return_value=start + timedelta(seconds=902)):
            self.assertNotEqual(self.attempt()[1].non_field_errors().as_data()[0].code, "locked")
            self.assertEqual(LoginProtection.objects.get(username="protected").failures, 2)

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "test-unknown-login-throttle"}})
    def test_unknown_account_attempts_are_rate_limited_by_address_without_database_rows(self):
        cache.clear()
        self.addCleanup(cache.clear)
        url = reverse("accounts:login")
        address = "192.0.2.1"
        with patch("accounts.login_protection.UNKNOWN_ATTEMPT_LIMIT", 2):
            for index in range(2):
                response = self.client.post(url, {"username": f"missing-{index}", "password": "wrong"}, REMOTE_ADDR=address)
                self.assertContains(response, "账号不存在")
            response = self.client.post(url, {"username": "another-missing", "password": "wrong"}, REMOTE_ADDR=address, HTTP_X_FORWARDED_FOR="192.0.2.2")
            self.assertContains(response, "登录尝试过多")
            response = self.client.post(url, {"username": "another-missing", "password": "wrong"}, REMOTE_ADDR="192.0.2.2")
            self.assertContains(response, "账号不存在")
            self.assertFalse(LoginProtection.objects.exists())
            response = self.client.post(url, {"username": self.user.username, "password": "wrong"}, REMOTE_ADDR=address)
            self.assertContains(response, "密码错误")
            self.assertEqual(LoginProtection.objects.get(username=self.user.username).failures, 1)


class AccountConcurrencyTests(TransactionTestCase):
    def test_concurrent_failures_are_not_lost(self):
        User.objects.create_user(username="concurrent", password=PASSWORD, approval_status="approved")
        def attempt(_):
            close_old_connections()
            try:
                return AccountAuthenticationForm(data={"username": "concurrent", "password": "wrong"}).is_valid()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=5) as pool:
            self.assertEqual(list(pool.map(attempt, range(5))), [False] * 5)
        state = LoginProtection.objects.get(username="concurrent")
        self.assertEqual(state.failures, 5)
        self.assertIsNotNone(state.locked_until)

    def test_concurrent_maintenance_registrations_are_unique(self):
        def register(index):
            close_old_connections()
            try:
                response = Client().post(reverse("accounts:register"), {"role": "maintenance", "real_name": "维修员", "phone": f"1380013800{index}", "organization_number": "SAME-UNIT", "password1": PASSWORD, "password2": PASSWORD})
                return response.status_code
            finally:
                close_old_connections()
        # SQLite serializes writes; a separate connection per request exercises
        # the shared global username constraint and generation path.
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(register, range(2))), [302, 302])
        users = User.objects.filter(role="maintenance")
        self.assertEqual(users.count(), 2)
        self.assertEqual(len(set(users.values_list("username", flat=True))), 2)
